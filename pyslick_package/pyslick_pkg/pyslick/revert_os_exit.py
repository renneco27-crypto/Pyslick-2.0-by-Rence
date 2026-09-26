from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

start = None
for i, ln in enumerate(lines):
    if ln.startswith('if __name__ == "__main__":'):
        start = i
        break

if start is None:
    print("__main__ block not found")
    sys.exit(1)

# Find end of the block.
end = start + 1
for i in range(start + 1, len(lines)):
    if lines[i].startswith("if __name__") or lines[i].startswith("def ") or lines[i].startswith("class "):
        end = i
        break
else:
    end = len(lines)
while end > start and lines[end - 1].strip() == "":
    end -= 1

# Check whether the block already is the simple form.
block = eol.join(lines[start:end])
if block == 'if __name__ == "__main__":' + eol + '    main()':
    print("already simple")
    sys.exit(0)

print("replacing %d lines with simple __main__" % (end - start))
for i in range(start, end):
    print("  ", repr(lines[i]))

new_block = [
    'if __name__ == "__main__":',
    '    main()',
]

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

print("\nAPPLIED")
