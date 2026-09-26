from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Locate `if __name__ == "__main__":`
start = None
for i, ln in enumerate(lines):
    if ln.startswith('if __name__ == "__main__":'):
        start = i
        break

if start is None:
    print("__main__ block not found")
    sys.exit(1)

# Check for existing os._exit
tail = eol.join(lines[start:start + 20])
if "os._exit" in tail:
    print("already patched")
    sys.exit(0)

# New block.
new_block = [
    'if __name__ == "__main__":',
    '    try:',
    '        main()',
    '    finally:',
    '        # Bypass Python\'s shutdown flush, which raises OSError on',
    '        # Windows when stdout is a closed pipe (piping to head/less).',
    '        try:',
    '            sys.stdout.flush()',
    '        except Exception:',
    '            pass',
    '        os._exit(0)',
]

# Find end of __main__ block.
end = start + 1
for i in range(start + 1, len(lines)):
    if lines[i].startswith("if __name__") or lines[i].startswith("def ") or lines[i].startswith("class "):
        end = i
        break
else:
    end = len(lines)
while end > start and lines[end - 1].strip() == "":
    end -= 1

new_lines = lines[:start] + new_block + lines[end:]
text = eol.join(new_lines)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    p.write_bytes(raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED")
