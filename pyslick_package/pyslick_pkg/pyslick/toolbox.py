
try:
    from .text_io import safe_read as __safe_read
except ImportError:
    try:
        from text_io import safe_read as __safe_read
    except ImportError:
        __safe_read = None
#!/usr/bin/env python3
"""
toolbox.py — small PowerShell-equivalent read-only commands, bundled so you
don't have to remember the PowerShell one-liners.

    pyslick ls  [root]
        == Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name

    pyslick lines <file>
        == Get-Content -Path <file> | ForEach-Object -Begin {$i=1} -Process {"{0:D3}: {1}" -f $i++,$_}

    pyslick grep <file> <pattern> [<pattern> ...] [--context N]
        == Select-String -Path <file> -Pattern <patterns> -Context N,N
"""

import os
import re
import sys
import json
import shutil
import argparse
import subprocess

CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RST   = "\033[0m"

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", ".pyslick_backups",
    "graphify-out",
}
SKIP_DIR_PREFIXES = ("pyslick_backup_",)


# ─────────────────────────────────────────────────────────────────────────
# ls  ==  Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name
# ─────────────────────────────────────────────────────────────────────────
SKIP_FILE_EXTS = {".bak", ".map", ".lock", ".pyc"}

# Noise files pyslick should never surface
NOISE_FILES = {
    "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
    "last_query_stamp", "GRAPH_REPORT.md",
}
NOISE_PREFIXES = ("gemini-code-",)
NOISE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
NOISE_PATTERNS = (
    # SHA256-named files (64 hex chars + .json)
    re.compile(r'^[0-9a-f]{64}\.json$'),
    # graphify artifacts
    re.compile(r'^graph\.(html|json)$'),
    # stat/cache json
    re.compile(r'^stat-index\.json$'),
)

def _is_noise(fn: str) -> bool:
    if fn in NOISE_FILES:
        return True
    if any(fn.startswith(p) for p in NOISE_PREFIXES):
        return True
    _, ext = os.path.splitext(fn)
    if ext.lower() in NOISE_EXTS:
        return True
    if any(p.match(fn) for p in NOISE_PATTERNS):
        return True
    return False


def mode_ls(root: str = ".", names_only: bool = True):
    """List source files, grouped by directory.

    Files at the root are printed flat at the top (no heading). Files
    inside a subdirectory are printed under a `[dir] <name>/` heading,
    indented two spaces, so it's obvious which files belong to which
    directory instead of one flat mixed list. Files larger than 30 MB
    are skipped — catches model weights (.gguf, .bin, .safetensors),
    archives, and media without hardcoding extension lists that go stale.
    """
    _MAX_FILE_BYTES = 30 * 1024 * 1024

    def _keep_dir(d: str) -> bool:
        return (
            d not in SKIP_DIRS
            and not d.startswith(".")
            and not any(d.startswith(p) for p in SKIP_DIR_PREFIXES)
        )

    def _keep_file(fn: str, dirpath: str) -> bool:
        if fn.startswith("."):
            return False
        if any(fn.endswith(ext) for ext in SKIP_FILE_EXTS):
            return False
        if _is_noise(fn):
            return False
        try:
            if os.path.getsize(os.path.join(dirpath, fn)) > _MAX_FILE_BYTES:
                return False
        except OSError:
            return False
        return True

    root = os.path.normpath(root)
    root_files: list[str] = []
    subdir_files: dict[str, list[tuple[str, str]]] = {}
    subdir_order: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted([d for d in dirnames if _keep_dir(d)])

        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        files_here = sorted(fn for fn in filenames if _keep_file(fn, dirpath))
        if not files_here:
            continue

        if rel_dir == "":
            for fn in files_here:
                root_files.append(
                    fn if names_only
                    else os.path.normpath(os.path.join(dirpath, fn))
                )
        else:
            if rel_dir not in subdir_files:
                subdir_files[rel_dir] = []
                subdir_order.append(rel_dir)
            for fn in files_here:
                subdir_files[rel_dir].append(
                    (fn, os.path.normpath(os.path.join(dirpath, fn)))
                )

    count = 0

    for fn in root_files:
        print(fn)
        count += 1

    if root_files and subdir_order:
        print()

    for i, rel_dir in enumerate(subdir_order):
        entries = subdir_files[rel_dir]
        if not entries:
            continue
        print(f"{DIM}[dir]  {rel_dir}/{RST}")
        for fn, full in entries:
            label = fn if names_only else full
            print(f"  {label}")
            count += 1
        if i < len(subdir_order) - 1:
            print()

    if count == 0:
        print(f"{DIM}(no files found under '{root}'){RST}")



# ─────────────────────────────────────────────────────────────────────────
# grep  ==  Select-String -Pattern ... -Context N,N
# ────────────────────────c─────────────────────────────────────────────────
_RG_BIN = None  # cached: resolved ripgrep binary path, or "" if unavailable


def _ripgrep_binary() -> str:
    global _RG_BIN
    if _RG_BIN is None:
        _RG_BIN = shutil.which("rg") or ""
    return _RG_BIN
def mode_lines(filepath: str, start: int | None = None, end: int | None = None):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
        sys.exit(1)
    with __safe_read(filepath) as f:
        all_lines = f.readlines()

    total = len(all_lines)
    lo = 1 if start is None else max(1, start)
    hi = total if end is None else min(total, end)

    if start is not None and end is None and start > 0:
        hi = min(total, start)

    for i in range(lo - 1, hi):
        print(f"{i + 1:03d}: {all_lines[i].rstrip()}")

    if lo > 1 or hi < total:
        print(f"... ({total} lines total; showing {lo}-{hi})")



def _grep_via_ripgrep(path: str, patterns: list[str], context: int) -> list[tuple] | None:
    """One `rg --json` call per pattern (rg's JSON stream doesn't say which
    -e matched, and mode_grep needs to tag each printed hit with its
    pattern). Returns [(line_idx0, pattern, {line_idx0: text, ...}), ...],
    or None if `rg` isn't installed or the call fails — caller falls back
    to the pure-Python scan unchanged."""
    binary = _ripgrep_binary()
    if not binary:
        return None
    hits: list[tuple] = []
    try:
        for pat in patterns:
            cmd = [binary, "--json", f"-C{context}", "-e", pat, "--", path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode not in (0, 1):  # 1 == "no matches", not an error
                return None
            window: dict[int, str] = {}
            match_lines: list[int] = []
            for line in res.stdout.splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") not in ("match", "context"):
                    continue
                data = obj["data"]
                lno = data["line_number"] - 1
                text = data["lines"]["text"].rstrip("\n")
                window[lno] = text
                if obj["type"] == "match":
                    match_lines.append(lno)
            for lno in match_lines:
                hits.append((lno, pat, window))
        return hits
    except (subprocess.TimeoutExpired, OSError, Exception):
        return None

def _function_ranges_for_hits(filepath: str, hit_lines: list[int], cap: int = 80):
    """Return [(start, end, matched_lines)] of enclosing functions and comment blocks.

    Checks:
    1. Functions from tree-sitter AST tags.
    2. Descriptive comment blocks and explicit marker blocks from comment_blocks.py.
    3. Merges comments directly above functions so the function doc is included.
    """
    funcs = []
    try:
        from repomap import extract_tags
        tags = extract_tags(filepath)
        if tags:
            funcs = [t for t in tags if t.is_def and "class" not in t.kind.lower()]
    except Exception:
        pass

    cm_blocks = []
    try:
        from comment_blocks import scan_file_for_comment_blocks
        cm_blocks = scan_file_for_comment_blocks(filepath) or []
    except Exception:
        pass

    if not funcs and not cm_blocks:
        return None

    ranges: list[tuple[int, int, list[int]]] = []
    for line_no in hit_lines:
        matched_span = None

        # 1. Check if inside a function
        enclosing_funcs = [t for t in funcs if t.start_line <= line_no <= t.end_line]
        if enclosing_funcs:
            best_func = min(enclosing_funcs, key=lambda t: t.end_line - t.start_line)
            f_start = best_func.start_line
            f_end = best_func.end_line
            # Check if there is a comment block directly preceding this function
            for c in cm_blocks:
                if c.start_line < f_start and c.end_line >= f_start - 2:
                    f_start = c.start_line
                    break
            matched_span = (f_start, f_end)

        # 2. Check if inside a comment block or marker block
        if not matched_span:
            for c in cm_blocks:
                if c.start_line <= line_no <= c.end_line:
                    # If this comment leads into a function, expand to that function's end
                    target_func = next((t for t in funcs if t.start_line >= c.start_line and t.start_line <= c.end_line + 3), None)
                    if target_func:
                        matched_span = (c.start_line, target_func.end_line)
                    else:
                        matched_span = (c.start_line, c.end_line)
                    break

        if matched_span:
            ranges.append((matched_span[0], matched_span[1], [line_no]))
        else:
            ranges.append((line_no, line_no, [line_no]))

    merged: list[tuple[int, int, list[int]]] = []
    for start, end, hits_in in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            p_start, p_end, p_hits = merged[-1]
            merged[-1] = (p_start, max(p_end, end), sorted(set(p_hits + hits_in)))
        else:
            merged.append((start, end, hits_in))
    return merged


def clean_line_for_display(line: str, max_len: int = 220) -> str:
    """Sanitize and truncate lines for display to prevent huge base64/minified dumps."""
    s = line.rstrip("\r\n")
    # Collapse base64 data URIs
    s = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=]{20,}', 'data:...;base64,[omitted]', s)
    # Collapse long base64/hex hash strings
    s = re.sub(r'([A-Za-z0-9+/=]{60,})', lambda m: m.group(1)[:16] + '...[omitted]...' + m.group(1)[-8:], s)
    # Truncate if still over max_len
    if len(s) > max_len:
        s = s[:max_len] + f" ... [{len(line.strip())} chars]"
    return s


def _print_function_range(lines: list[str], start: int, end: int,
                          hit_lines: set[int], matched_patterns: dict[int, str],
                          cap: int = 80):
    total = end - start + 1
    if total > cap:
        first_end = start + 14
        last_start = end - 4
        mid_lo = max(first_end + 1, min(hit_lines) - 10)
        mid_hi = min(last_start - 1, max(hit_lines) + 10)
        segments = [(start, first_end), (mid_lo, mid_hi), (last_start, end)]
    else:
        segments = [(start, end)]

    prev_end = start - 2
    for seg_start, seg_end in segments:
        seg_start = max(seg_start, start)
        seg_end = min(seg_end, end)
        if seg_start > prev_end + 1:
            print(f"{DIM}  ...{RST}")
        for j in range(seg_start, seg_end + 1):
            idx = j - 1
            if idx < 0 or idx >= len(lines):
                continue
            is_hit = j in hit_lines
            marker = f"{GREEN}>{RST}" if is_hit else " "
            tag = f"{YELL}[{matched_patterns.get(j, '')}]{RST} " if is_hit else ""
            clean_text = clean_line_for_display(lines[idx])
            print(f"{marker} {DIM}{j:4d}:{RST} {tag}{clean_text}")
        prev_end = seg_end
    if total > cap:
        print(f"{DIM}  [function truncated — {total} lines total]{RST}")
    print()

def mode_grep(filepath: str, patterns: list[str], context: int = 1):
    # ── normalize: if a pattern arrived containing '|' (e.g. from a shell
    # that couldn't escape it), split it into separate patterns so
    # "Phone\|Facebook" still works instead of silently matching nothing ──
    normalized = []
    for p in patterns:
        normalized.extend(p.split("|"))
    patterns = [p.strip() for p in normalized if p.strip()]

    if not os.path.exists(filepath):
        # Repo-relative fallback: strip leading slash, retry.
        _alt = filepath.lstrip("/\\").replace("\\", "/")
        if _alt and os.path.exists(_alt):
            filepath = _alt
        else:
            print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
            sys.exit(1)

    rg_hits = _grep_via_ripgrep(filepath, patterns, context)
    if rg_hits is not None:
        if not rg_hits:
            print(f"{DIM}No matches for {patterns} in {filepath}{RST}")
            return
        # Mirror the pure-Python loop below exactly (same "..." gap marker,
        # same lack of dedup for overlapping windows) rather than reusing
        # tool_grep's seen_ranges skip — that's a different tool's semantics.
        print(f"{BOLD}{filepath}{RST} — {len(rg_hits)} match(es) {DIM}(via ripgrep){RST}")
        hit_lines = [h[0] + 1 for h in rg_hits]
        matched_patterns = {h[0] + 1: h[1] for h in rg_hits}
        try:
            with __safe_read(filepath) as _f:
                src_lines = _f.readlines()
        except OSError:
            src_lines = []
        ranges = _function_ranges_for_hits(filepath, hit_lines) if src_lines else None
        if ranges:
            for r_start, r_end, r_hits in ranges:
                if r_start == r_end:
                    j = r_start
                    clean_text = clean_line_for_display(src_lines[j-1])
                    if 1 <= j <= len(src_lines):
                        print(f"{GREEN}>{RST} {DIM}{j:4d}:{RST} {YELL}[{matched_patterns.get(j, '')}]{RST} {clean_text}")
                    print()
                    continue
                _print_function_range(src_lines, r_start, r_end,
                                      set(r_hits), matched_patterns)
            return
        # Fallback: existing context-window output
        last_printed = -1
        for line_idx, matched_pattern, window in rg_hits:
            lo, hi = max(0, line_idx - context), line_idx + context
            start, end = lo, min(hi, max(window)) + 1
            if start > last_printed + 1:
                print(f"{DIM}  ...{RST}")
            for j in range(start, end):
                marker = f"{GREEN}>{RST}" if j == line_idx else " "
                tag = f"{YELL}[{matched_pattern}]{RST} " if j == line_idx else ""
                clean_text = clean_line_for_display(window.get(j, ''))
                print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{clean_text}")
            last_printed = end - 1
            print()
        return

    with __safe_read(filepath) as f:
        lines = f.readlines()

    compiled = [re.compile(p) for p in patterns]
    hits = []
    for i, line in enumerate(lines):
        for pat, raw in zip(compiled, patterns):
            if pat.search(line):
                hits.append((i, raw))
                break

    if not hits:
        print(f"{DIM}No matches for {patterns} in {filepath}{RST}")
        return

    print(f"{BOLD}{filepath}{RST} — {len(hits)} match(es)")
    hit_lines = [h[0] + 1 for h in hits]
    matched_patterns = {h[0] + 1: h[1] for h in hits}
    ranges = _function_ranges_for_hits(filepath, hit_lines)
    if ranges:
        for r_start, r_end, r_hits in ranges:
            if r_start == r_end:
                j = r_start
                if 1 <= j <= len(lines):
                    clean_text = clean_line_for_display(lines[j-1])
                    print(f"{GREEN}>{RST} {DIM}{j:4d}:{RST} {YELL}[{matched_patterns.get(j, '')}]{RST} {clean_text}")
                print()
                continue
            _print_function_range(lines, r_start, r_end,
                                  set(r_hits), matched_patterns)
        return
    # Fallback: existing context-window output
    last_printed = -1
    for line_idx, matched_pattern in hits:
        start = max(0, line_idx - context)
        end = min(len(lines), line_idx + context + 1)
        if start > last_printed + 1:
            print(f"{DIM}  ...{RST}")
        for j in range(start, end):
            marker = f"{GREEN}>{RST}" if j == line_idx else " "
            tag = f"{YELL}[{matched_pattern}]{RST} " if j == line_idx else ""
            clean_text = clean_line_for_display(lines[j])
            print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{clean_text}")
        last_printed = end - 1
        print()


def mode_semantic_grep(query: str, root: str = ".", top_k: int = 5, context: int = 1, exact: bool = False, stream: bool = False):
    """Semantic grep across the repository using BM25 index + AST function snapping.
    
    Interconnects with:
    - repomap (Codebase Overview for 'what does this codebase do')
    - relations (Entity graph path for 'how does A connect to B')
    - decompose (Multi-query splitting for compound questions)
    - synonyms (Domain vocabulary expansion)
    - text_index (Boosted docstrings & identifiers)
    """
    if exact:
        # Literal fast path -- skip BM25 and use oneshot's streaming scanner.
        from oneshot import run_oneshot
        run_oneshot(query, root=root, use_json=False)
        return

    try:
        from text_index import build_text_index, search_text_index_auto
    except ImportError:
        print(f"{RED}Error: text_index module not available for semantic grep.{RST}")
        return

    # ── 1. Overview Query Check ───────────────────────────────────────────
    try:
        from repomap import is_overview_query, get_codebase_overview
        if is_overview_query(query):
            overview = get_codebase_overview(root)
            print(f"\n{BOLD}{CYAN}━━  Codebase Overview  {RST}{BOLD}'{query}'{RST}")
            print(f"{DIM}{'─' * 60}{RST}")
            meta = overview.get("metadata", {})
            if meta.get("name"):
                print(f"  {BOLD}Project:{RST} {meta['name']}  {DIM}({meta.get('type', 'Codebase')}){RST}")
            if meta.get("description"):
                print(f"  {DIM}Description:{RST} {meta['description']}")

            if overview.get("entry_points"):
                print(f"\n  {BOLD}Key Entry Points:{RST}")
                for ep in overview["entry_points"]:
                    print(f"    {CYAN}· {ep}{RST}")

            if overview.get("top_files"):
                print(f"\n  {BOLD}Most Connected Core Files (PageRank):{RST}")
                for tf in overview["top_files"]:
                    print(f"    {GREEN}★ {tf['path']}{RST} {DIM}(score: {tf['pagerank']}){RST}")
                    if tf.get("summary"):
                        print(f"      {DIM}{tf['summary'][:120]}{RST}")
            print()
            return
    except Exception:
        pass

    # ── 2. Relation Query Check ───────────────────────────────────────────
    try:
        from relations import is_relation_query, resolve_relation
        if is_relation_query(query):
            rel = resolve_relation(query)
            if rel.get("confidence") != "none" and rel.get("paths"):
                print(f"\n{BOLD}{CYAN}━━  Entity Relation  {RST}{BOLD}'{query}'{RST}")
                print(f"{DIM}{'─' * 60}{RST}")
                print(f"  {GREEN}✔ {rel.get('note')}{RST}  {DIM}(confidence: {rel.get('confidence')}){RST}\n")
                for ev in rel.get("evidence", []):
                    rel_type = ev.get("relation") or "connects to"
                    print(f"  {DIM}Step {ev['step']}:{RST} {CYAN}{ev['from']}{RST} ──[{rel_type}]──> {CYAN}{ev['to']}{RST}")
                    if ev.get("file"):
                        print(f"    {DIM}at {ev['file']}:{ev.get('loc', '')}{RST}")
                print()
    except Exception:
        pass

    # ── 3. Compound Query Decomposition ───────────────────────────────────
    try:
        from decompose import decompose
        subqueries = decompose(query)
    except Exception:
        subqueries = [query]

    if len(subqueries) > 1:
        print(f"\n{BOLD}{CYAN}━━  Semantic Grep (Decomposed into {len(subqueries)} queries)  {RST}")
        for sq in subqueries:
            print(f"  {DIM}· {sq}{RST}")
    else:
        print(f"\n{BOLD}{CYAN}━━  Semantic Grep  {RST}{BOLD}'{query}'{RST}")

    if root and root != ".":
        print(f"{DIM}Scope: {root}{RST}")
    print(f"{DIM}{'─' * 60}{RST}")

    # ── 4. Retrieve & Merge BM25 Results ───────────────────────────────────
    idx = build_text_index(root)
    merged_results = []
    all_keywords = []
    all_syn_terms = []

    for sq in subqueries:
        search_query = sq
        syn_terms = []
        try:
            from synonyms import expand as _syn_expand
            exp = _syn_expand(sq)
            if exp and exp.get("terms"):
                syn_terms = [t for t in exp["terms"] if t.lower() not in sq.lower() and len(t) > 2]
                all_syn_terms.extend(syn_terms)
        except Exception:
            pass

        if syn_terms:
            search_query = f"{sq} {' '.join(syn_terms[:4])}"

        sub_res, keywords = search_text_index_auto(search_query, idx, top_k=top_k)
        all_keywords.extend(keywords)
        for r in sub_res:
            if not any(mr[0] == r[0] for mr in merged_results):
                merged_results.append(r)

    results = merged_results[:top_k]
    keywords = list(dict.fromkeys(all_keywords))
    syn_terms = list(dict.fromkeys(all_syn_terms))

    if not results:
        print(f"{DIM}No semantic matches found for '{query}'.{RST}")
        return

    # Stopwords to filter out from in-file line matching to avoid false hits on code keywords like 'for'
    STOP_WORDS = {"for", "the", "and", "with", "from", "that", "this", "are", "does", "did", "was", "were", "what", "how", "have", "has", "all", "out", "can"}
    # Extract query tokens for in-file line matching (original + keywords + top synonyms)
    q_tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_]+", query) if len(t) > 2 and t.lower() not in STOP_WORDS]
    if keywords:
        q_tokens.extend([k.lower() for k in keywords if len(k) > 2 and k.lower() not in STOP_WORDS])
    if syn_terms:
        q_tokens.extend([s.lower() for s in syn_terms[:4] if s.lower() not in STOP_WORDS])
    q_tokens = list(dict.fromkeys(q_tokens))


    total_shown = 0
    for filepath, score, matched_tokens in results:
        if not os.path.isfile(filepath):
            continue

        try:
            with __safe_read(filepath) as f:
                lines = f.readlines()
        except OSError:
            continue

        if not lines:
            continue

        # Find lines matching the search terms with semantic origin labeling using word boundaries
        hit_lines = []
        matched_map = {}
        compiled_tokens = []
        for tok in q_tokens:
            try:
                compiled_tokens.append((tok, re.compile(r"\b" + re.escape(tok) + r"\b", re.IGNORECASE)))
            except Exception:
                compiled_tokens.append((tok, None))

        for line_idx, line_text in enumerate(lines):
            # Skip massive lines
            if len(line_text) > 1500:
                continue
            for tok, rx in compiled_tokens:
                matched = rx.search(line_text) if rx else (tok.lower() in line_text.lower())
                if matched:
                    lineno = line_idx + 1
                    hit_lines.append(lineno)
                    strip_low = line_text.strip().lower()
                    is_docstring = '"""' in strip_low or "'''" in strip_low or "/**" in strip_low or "*/" in strip_low
                    is_comment = strip_low.startswith(("//", "#", "*", "<!--")) or is_docstring
                    is_marker = "pyslick:start" in strip_low or "pyslick:end" in strip_low
                    is_def = any(strip_low.startswith(kw) for kw in ("def ", "function ", "class ", "interface ", "export function ", "export const ", "export async function ", "export default ", "public ", "private "))

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

                    matched_map[lineno] = f"{origin}:{tok}" if origin else tok
                    break

        if not hit_lines:
            # Fallback to first line if no specific token matched (e.g. filename match)
            hit_lines = [1]
            matched_map[1] = "file"

        print(f"{BOLD}{GREEN}{filepath}{RST} {DIM}(score: {score:.2f}){RST} — {len(hit_lines)} match line(s)")
        print(f"{DIM}  VS Code shortcut: code -g \"{filepath}:{hit_lines[0]}\"{RST}")

        ranges = _function_ranges_for_hits(filepath, hit_lines)
        if ranges:
            max_ranges_to_show = 3
            for r_idx, (r_start, r_end, r_hits) in enumerate(ranges[:max_ranges_to_show]):
                if r_start == r_end:
                    j = r_start
                    if 1 <= j <= len(lines):
                        clean_text = clean_line_for_display(lines[j-1])
                        print(f"{GREEN}>{RST} {DIM}{j:4d}:{RST} {YELL}[{matched_map.get(j, '')}]{RST} {clean_text}")
                    print()
                    continue
                _print_function_range(lines, r_start, r_end, set(r_hits), matched_map)
            if len(ranges) > max_ranges_to_show:
                print(f"  {DIM}... [{len(ranges) - max_ranges_to_show} more matched block(s) omitted in {filepath}]{RST}\n")
        else:
            # Context window fallback
            last_printed = -1
            for lineno in hit_lines[:5]:
                line_idx = lineno - 1
                start = max(0, line_idx - context)
                end = min(len(lines), line_idx + context + 1)
                if start > last_printed + 1:
                    print(f"{DIM}  ...{RST}")
                for j in range(start, end):
                    marker = f"{GREEN}>{RST}" if j == line_idx else " "
                    tag = f"{YELL}[{matched_map.get(j+1, '')}]{RST} " if j == line_idx else ""
                    clean_text = clean_line_for_display(lines[j])
                    print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{clean_text}")
                last_printed = end - 1
                print()

        total_shown += 1
        if total_shown >= top_k:
            break


def main():
    parser = argparse.ArgumentParser(prog="pyslick", add_help=False)
    sub = parser.add_subparsers(dest="cmd")

    p_ls = sub.add_parser("ls")
    p_ls.add_argument("root", nargs="?", default=".")
    p_ls.add_argument("--full-path", action="store_true", help="print full paths instead of just filenames")

    p_lines = sub.add_parser("lines")
    p_lines.add_argument("file")
    p_lines.add_argument("range", nargs="?", default=None,
                         help="line number, or start-end (e.g. 17 or 10-25 or 10:25)")
    p_lines.add_argument("--head", type=int, default=None,
                         help="show first N lines")
    p_lines.add_argument("--tail", type=int, default=None,
                         help="show last N lines")

    p_grep = sub.add_parser("grep")
    p_grep.add_argument("file")
    p_grep.add_argument("patterns", nargs="*")
    p_grep.add_argument("--context", type=int, default=1)

    args = parser.parse_args()

    if args.cmd == "ls":
        mode_ls(args.root, names_only=not args.full_path)
    elif args.cmd == "lines":
        start = None
        end = None
        if args.head is not None:
            start = 1
            end = args.head
        elif args.tail is not None:
            with __safe_read(args.file) as _f:
                _total = len(_f.readlines())
            start = max(1, _total - args.tail + 1)
            end = _total
        elif args.range:
            m = re.match(r"^(\d+)(?:[-:](\d+))?$", args.range)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else start
            else:
                print(f"{RED}Bad range: {args.range}{RST}")
                sys.exit(1)
        mode_lines(args.file, start=start, end=end)
    elif args.cmd == "grep":
        if os.path.isfile(args.file):
            mode_grep(args.file, args.patterns, context=args.context)
        else:
            # If the first argument isn't a file, treat whole input as semantic search query
            full_query = " ".join([args.file] + args.patterns)
            mode_semantic_grep(full_query, context=args.context)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

