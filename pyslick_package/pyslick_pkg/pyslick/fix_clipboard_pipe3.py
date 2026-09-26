from pathlib import Path
import subprocess, sys

p = Path("clipboard.py")
raw = p.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
if had_bom:
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")

# Detect line ending.
if "\r\n" in text:
    eol = "\r\n"
else:
    eol = "\n"

# Split on eol, keep exact content.
lines = text.split(eol)

# Locate write() def line.
start = None
for i, ln in enumerate(lines):
    if ln.strip() == "def write(self, s: str) -> int:":
        start = i
        break
if start is None:
    print("write() def not found")
    sys.exit(1)

expected_old = [
    "        self._real.write(s)",
    "        self._real.flush()",
    "        self._buf.write(s)",
    "        return len(s)",
]
for j, exp in enumerate(expected_old):
    got = lines[start + 1 + j]
    if got != exp:
        print("line %d mismatch. expected %r, got %r" % (start + 2 + j, exp, got))
        sys.exit(1)

new_write = [
    "    def write(self, s: str) -> int:",
    "        try:",
    "            self._real.write(s)",
    "            self._real.flush()",
    "        except (BrokenPipeError, OSError):",
    "            pass",
    "        try:",
    "            self._buf.write(s)",
    "        except Exception:",
    "            pass",
    "        return len(s)",
]

new_lines = lines[:start] + new_write + lines[start + 1 + len(expected_old):]

# Flush method.
for i, ln in enumerate(new_lines):
    if i > start and ln.strip() == "def flush(self):":
        # confirm next line is the bare self._real.flush()
        if i + 1 < len(new_lines) and new_lines[i + 1].strip() == "self._real.flush()":
            new_flush = [
                "    def flush(self):",
                "        try:",
                "            self._real.flush()",
                "        except (BrokenPipeError, OSError):",
                "            pass",
            ]
            new_lines = new_lines[:i] + new_flush + new_lines[i + 2:]
            print("flush() patched")
        else:
            print("flush() next line unexpected:", repr(new_lines[i + 1]))
        break

text = eol.join(new_lines)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    p.write_bytes(raw if not had_bom else b"\xef\xbb\xbf" + raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. line endings:", repr(eol))
