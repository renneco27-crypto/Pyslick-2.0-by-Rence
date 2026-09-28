from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("prework3-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "recon.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Find the exact "    phase1_orient()" line at top of main() (not the def at line 135).
target = "    phase1_orient()"
main_call_idx = None
for i, ln in enumerate(lines):
    if ln == target and i > 500:  # past the def, inside main()
        main_call_idx = i
        break

if main_call_idx is None:
    print("main()'s phase1_orient() call not found")
    sys.exit(1)

# Check if already patched.
window = eol.join(lines[max(0, main_call_idx - 10):main_call_idx + 5])
if "Phase 0: pre-work" in window or "run_pre_work(directive" in window:
    print("already patched")
    sys.exit(0)

print("found phase1_orient() call at line %d" % (main_call_idx + 1))

new_lines = [
    "    # Phase 0: pre-work -- has someone already fixed this?",
    "    try:",
    "        from .pre_work import run_pre_work",
    "        run_pre_work(directive, None)",
    "    except Exception as _e:",
    "        print(f\"  (pre-work skipped: {_e})\")",
    "",
    "    phase1_orient()",
]

# Replace the single phase1_orient() line with the block.
new_text_lines = lines[:main_call_idx] + new_lines + lines[main_call_idx + 1:]
new_text = eol.join(new_text_lines)

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
