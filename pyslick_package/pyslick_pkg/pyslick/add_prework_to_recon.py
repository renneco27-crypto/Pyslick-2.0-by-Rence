from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("recon-prework-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "recon.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

# Anchor: the print after auto_fix info, then phase1_orient().
old = '''    if auto_fix:
        print(f"  {DIM}Auto-fix loop: enabled  "
              f"(max {max_attempts} attempts"
              f"{', test: ' + ' '.join(test_cmd) if test_cmd else ', no test-cmd given'}){RST}")

    phase1_orient()
'''

new = '''    if auto_fix:
        print(f"  {DIM}Auto-fix loop: enabled  "
              f"(max {max_attempts} attempts"
              f"{', test: ' + ' '.join(test_cmd) if test_cmd else ', no test-cmd given'}){RST}")

    # ── Phase 0: pre-work — has someone already fixed this? ────────────────
    try:
        from .pre_work import run_pre_work
        run_pre_work(directive, None)
    except Exception as _e:
        print(f"  {DIM}(pre-work skipped: {_e}){RST}")

    phase1_orient()
'''

if new in text:
    print("already patched")
    sys.exit(0)
if old not in text:
    print("anchor not found")
    sys.exit(1)

shutil.copy2(p, BK / p.name)
p.write_bytes(text.replace(old, new, 1).encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
