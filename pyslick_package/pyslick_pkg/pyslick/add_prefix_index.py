from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("prefix-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "text_index.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

if "sorted_tokens" in text:
    print("already patched")
    sys.exit(0)

# 1. Store sorted_tokens in build_text_index's return dict.
old_ret = '''    result = {
        "postings": {t: dict(f) for t, f in postings.items()},
        "doc_len": doc_len,
        "avg_len": avg_len,
        "n_docs": n_docs,
    }'''
new_ret = '''    postings_plain = {t: dict(f) for t, f in postings.items()}
    result = {
        "postings": postings_plain,
        "sorted_tokens": sorted(postings_plain.keys()),
        "doc_len": doc_len,
        "avg_len": avg_len,
        "n_docs": n_docs,
    }'''

if old_ret not in text:
    print("build_text_index return not found")
    sys.exit(1)
text = text.replace(old_ret, new_ret, 1)

# 2. Replace the linear prefix scan in search_text_index.
old_scan = '''        if not files and len(tok) >= 4:
            # Prefix fallback: "auth" should match "authentication",
            # "authorization", "authToken". Merge all matching postings.
            merged: dict[str, int] = {}
            for p_tok, p_files in postings.items():
                if p_tok.startswith(tok):
                    for fp, tf in p_files.items():
                        merged[fp] = merged.get(fp, 0) + tf
            if merged:
                files = merged'''

new_scan = '''        if not files and len(tok) >= 4:
            # Prefix fallback: "auth" should match "authentication",
            # "authorization", "authToken". Uses a precomputed sorted
            # token list + binary search to avoid scanning every posting.
            sorted_tokens = index.get("sorted_tokens")
            if sorted_tokens:
                import bisect
                i = bisect.bisect_left(sorted_tokens, tok)
                merged: dict[str, int] = {}
                while i < len(sorted_tokens) and sorted_tokens[i].startswith(tok):
                    for fp, tf in postings[sorted_tokens[i]].items():
                        merged[fp] = merged.get(fp, 0) + tf
                    i += 1
            else:
                # Fallback for indexes built before this change
                merged = {}
                for p_tok, p_files in postings.items():
                    if p_tok.startswith(tok):
                        for fp, tf in p_files.items():
                            merged[fp] = merged.get(fp, 0) + tf
            if merged:
                files = merged'''

if old_scan not in text:
    print("prefix scan block not found")
    sys.exit(1)
text = text.replace(old_scan, new_scan, 1)

shutil.copy2(p, BK / p.name)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
