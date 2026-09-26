from pathlib import Path
import subprocess, sys

p = Path("clipboard.py")
raw = p.read_bytes()
had_bom = raw.startswith(b"\xef\xbb\xbf")
if had_bom:
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
lines = text.split("\n")

# Find the write() method's body lines by content, but at single-line granularity.
new_body = [
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

# Locate the write() def line
start = None
for i, ln in enumerate(lines):
    if ln.strip() == "def write(self, s: str) -> int:":
        start = i
        break
if start is None:
    print("write() def not found")
    sys.exit(1)

# Confirm the next lines match the old body
expected_old = [
    "        self._real.write(s)",
    "        self._real.flush()",
    "        self._buf.write(s)",
    "        return len(s)",
]
for j, exp in enumerate(expected_old):
    if lines[start + 1 + j] != exp:
        print("line %d does not match expected: %r (got %r)" % (
            start + 2 + j, exp, lines[start + 1 + j]))
        sys.exit(1)

# Also fix the standalone flush() method.
flush_start = None
for i, ln in enumerate(lines):
    if ln.strip() == "def flush(self):" and i > start:
        flush_start = i
        break

new_lines = (
    lines[:start]
    + new_body
    + lines[start + 1 + len(expected_old):]
)

if flush_start is not None:
    # Re-locate flush after the write splice shifted indices
    flush_start = None
    for i, ln in enumerate(new_lines):
        if ln.strip() == "def flush(self):" and i > start:
            flush_start = i
            break
    if flush_start is not None and new_lines[flush_start + 1].strip() == "self._real.flush()":
        new_flush = [
            "    def flush(self):",
            "        try:",
            "            self._real.flush()",
            "        except (BrokenPipeError, OSError):",
            "            pass",
        ]
        new_lines = new_lines[:flush_start] + new_flush + new_lines[flush_start + 2:]

text = "\n".join(new_lines)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    if had_bom:
        p.write_bytes(b"\xef\xbb\xbf" + raw)
    else:
        p.write_bytes(raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED")
