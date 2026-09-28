from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("stream-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "toolbox.py"
body_file = ROOT / "_new_body.txt"

if not body_file.is_file():
    print("_new_body.txt not found")
    sys.exit(1)

raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

body = body_file.read_text(encoding="utf-8")
body = body.replace("\r\n", "\n")
if eol == "\r\n":
    body = body.replace("\n", "\r\n")

if "_streaming_scan" in text:
    print("already patched")
    sys.exit(0)

start_marker = "    total_shown = 0" + eol + "    for filepath, score, matched_tokens in results:"
if start_marker not in text:
    print("per-file loop start marker not found")
    sys.exit(1)
start_idx = text.index(start_marker)

rec_marker = "Recommended reads (from call graph):"
rec_idx = text.find(rec_marker)
if rec_idx == -1:
    print("Recommended reads block not found")
    sys.exit(1)

next_def = text.find(eol + "def ", rec_idx)
if next_def == -1:
    print("could not find end of mode_semantic_grep")
    sys.exit(1)

new_text = text[:start_idx] + body + text[next_def:]

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
