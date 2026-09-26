from pathlib import Path
import subprocess, sys

def read(p):
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", errors="strict")

def write(p, text):
    p.write_bytes(text.encode("utf-8"))

def compile_ok(p):
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout)

edits = []

# -----------------------------------------------------------------------
# 1. pre_work.py fallback _git_run: text=True -> utf-8 strict
# -----------------------------------------------------------------------
pw = Path("pre_work.py")
t = read(pw)
old = 'capture_output=True, text=True,\n                               timeout=timeout)'
new = 'capture_output=True, encoding="utf-8", errors="strict",\n                               timeout=timeout)'
if new in t:
    print("pre_work.py :: already fixed")
elif old in t:
    write(pw, t.replace(old, new, 1))
    ok, err = compile_ok(pw)
    if not ok:
        print("pre_work.py :: FAILED:", err.splitlines()[0])
        sys.exit(1)
    print("pre_work.py :: git decode -> utf-8 strict  APPLIED")
else:
    print("pre_work.py :: anchor not found")

# -----------------------------------------------------------------------
# 2. recon_semantic.py _git_run: text=True -> utf-8 strict
# -----------------------------------------------------------------------
rs = Path("recon_semantic.py")
t = read(rs)
old = 'cwd=root, capture_output=True, text=True, timeout=timeout,'
new = 'cwd=root, capture_output=True, encoding="utf-8", errors="strict", timeout=timeout,'
if new in t:
    print("recon_semantic.py :: already fixed")
elif old in t:
    write(rs, t.replace(old, new, 1))
    ok, err = compile_ok(rs)
    if not ok:
        print("recon_semantic.py :: FAILED:", err.splitlines()[0])
        sys.exit(1)
    print("recon_semantic.py :: git decode -> utf-8 strict  APPLIED")
else:
    print("recon_semantic.py :: anchor not found")

# -----------------------------------------------------------------------
# 3. __init__.py: prework alias
# -----------------------------------------------------------------------
init = Path("__init__.py")
t = read(init)
old = '            elif command == "pre-work":'
new = '            elif command in ("pre-work", "prework"):'
if new in t:
    print("__init__.py :: prework alias already present")
elif old in t:
    t = t.replace(old, new, 1)
    print("__init__.py :: prework alias added")
else:
    print("__init__.py :: prework alias anchor not found")
    sys.exit(1)

# -----------------------------------------------------------------------
# 4. __init__.py: add pre-work to --help
# -----------------------------------------------------------------------
old_help = '''  recon-pack "<directive>" [--max-files N] [--max-lines N]'''
new_help = '''  pre-work "<task>" [--file <path>]
      Git-aware pre-debug check. Searches every branch, every commit, and
      every open PR for work that already matches your task. Run this
      before writing any fix.
      Example:  pyslick pre-work "fix padding" --file src/App.tsx

  recon-pack "<directive>" [--max-files N] [--max-lines N]'''
if new_help in t:
    print("__init__.py :: pre-work help already present")
elif old_help in t:
    t = t.replace(old_help, new_help, 1)
    print("__init__.py :: pre-work added to --help")
else:
    print("__init__.py :: help anchor not found")

write(init, t)
ok, err = compile_ok(init)
if not ok:
    print("__init__.py :: FAILED:", err.splitlines()[0])
    sys.exit(1)

print("\nDONE")
