from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("stream-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "toolbox.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

if "_streaming_scan" in text:
    print("already patched")
    sys.exit(0)

# Find the whole per-file loop in mode_semantic_grep: from
#     total_shown = 0
#     for filepath, score, matched_tokens in results:
# to the end of the Recommended reads block.
start_marker = "    total_shown = 0\n    for filepath, score, matched_tokens in results:"
# normalize EOL
start_marker = start_marker.replace("\n", eol)

if start_marker not in text:
    print("per-file loop start marker not found")
    sys.exit(1)

start_idx = text.index(start_marker)

# Find the end: the Recommended reads block we inserted earlier
end_marker = '            print()\n'
# Find the last occurrence of that belongs to the Recommended reads block:
end_marker_norm = '            print()'
rec_marker = "Recommended reads (from call graph):"
rec_idx = text.find(rec_marker)
if rec_idx == -1:
    print("Recommended reads block not found -- run add_graph_boost first")
    sys.exit(1)

# End of Recommended reads block: next `def ` after rec_idx
next_def = text.find("\ndef ", rec_idx)
if next_def == -1:
    print("could not find end of mode_semantic_grep")
    sys.exit(1)

# The replacement region runs from start_idx to next_def (exclusive of the \ndef).
region_end = next_def

new_body = '''    total_shown = 0
    for filepath, score, matched_tokens in results:
        if not os.path.isfile(filepath):
            continue

        try:
            _fh = open(filepath, "r", encoding="utf-8-sig", errors="replace")
        except OSError:
            continue

        # ── Streaming scan ────────────────────────────────────────────
        # Read line by line. Print each hit the moment it's found, so the
        # first output appears in ~50ms instead of waiting for the whole
        # file to load. Hit line numbers are collected for the function
        # summary that prints after the file finishes.
        print(f"{BOLD}{GREEN}{filepath}{RST} {DIM}(score: {score:.2f}){RST}")

        hit_lines = []
        matched_map = {}
        compiled_tokens = []
        for tok in q_tokens:
            try:
                compiled_tokens.append((tok, re.compile(r"\\b" + re.escape(tok) + r"\\b", re.IGNORECASE)))
            except Exception:
                compiled_tokens.append((tok, None))

        with _fh:
            for line_idx, line_text in enumerate(_fh):
                lineno = line_idx + 1
                # Skip massive lines (base64 blobs, minified)
                if len(line_text) > 1500:
                    continue
                for tok, rx in compiled_tokens:
                    matched = rx.search(line_text) if rx else (tok.lower() in line_text.lower())
                    if not matched:
                        continue
                    hit_lines.append(lineno)
                    strip_low = line_text.strip().lower()
                    is_docstring = ('"""' in strip_low or "'''" in strip_low
                                    or "/**" in strip_low or "*/" in strip_low)
                    is_comment = strip_low.startswith(("//", "#", "*", "<!--")) or is_docstring
                    is_marker = "pyslick:start" in strip_low or "pyslick:end" in strip_low
                    is_def = any(strip_low.startswith(kw) for kw in
                                 ("def ", "function ", "class ", "interface ",
                                  "export function ", "export const ",
                                  "export async function ", "export default ",
                                  "public ", "private "))
                    if is_marker:
                        origin = "marker"
                    elif is_docstring:
                        origin = "docstring"
                    elif is_comment:
                        origin = "comment"
                    elif is_def:
                        origin = "def"
                    else:
                        origin = ""
                    tag = f"{origin}:{tok}" if origin else tok
                    matched_map[lineno] = tag
                    # Print immediately
                    clean_text = clean_line_for_display(line_text)
                    print(f"{GREEN}>{RST} {DIM}{lineno:4d}:{RST} {YELL}[{tag}]{RST} {clean_text}")
                    break  # one match per line

        if not hit_lines:
            # No hits, still show a one-liner so the user knows we scanned it
            print(f"  {DIM}(no matching lines){RST}")
            print()
            continue

        # ── Function summary ──────────────────────────────────────────
        ranges = _function_ranges_for_hits(filepath, hit_lines)
        if ranges:
            names = []
            for r_start, r_end, _r_hits in ranges:
                names.append(f"{filepath}:{r_start}-{r_end}")
            print(f"  {DIM}--- {len(hit_lines)} hit(s) inside {len(ranges)} block(s): "
                  f"{', '.join(names[:6])}{' ...' if len(names) > 6 else ''}{RST}")
        else:
            print(f"  {DIM}--- {len(hit_lines)} hit(s) (no enclosing function){RST}")
        print()

        total_shown += 1

    # Recommended reads block from graph labels
    if _nodes_by_id:
        recs = _recommended_reads(query, _nodes_by_id, top_n=5)
        if recs:
            print(f"\\n{BOLD}{CYAN}Recommended reads (from call graph):{RST}")
            for label, sfile, sloc in recs:
                print(f"  {GREEN}*{RST} {BOLD}{label}{RST}  {DIM}({sfile}:{sloc}){RST}")
            print()

'''

new_text = text[:start_idx] + new_body + text[region_end:]

shutil.copy2(p, BK / p.name)
p.write_bytes(new_text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
