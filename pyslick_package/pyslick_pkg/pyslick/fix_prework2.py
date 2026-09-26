from pathlib import Path
import subprocess, sys

p = Path("pre_work.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
lines = text.split("\n")

# Find the line containing `capture_output=True, text=True,` inside _git_run
idx = None
for i, ln in enumerate(lines):
    if "capture_output=True, text=True," in ln:
        idx = i
        break

if idx is None:
    print("anchor line not found")
    sys.exit(1)

print("found at line", idx + 1)
print("before:", repr(lines[idx]))

lines[idx] = lines[idx].replace(
    "capture_output=True, text=True,",
    'capture_output=True, encoding="utf-8", errors="strict",'
)
print("after :", repr(lines[idx]))

text = "\n".join(lines)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    p.write_bytes(raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED")
