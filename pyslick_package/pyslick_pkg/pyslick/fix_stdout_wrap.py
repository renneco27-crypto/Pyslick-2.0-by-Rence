from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

target = [
    "    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')",
    "    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')",
]

replacement = [
    "    try:",
    "        sys.stdout.reconfigure(encoding='utf-8', errors='replace')",
    "        sys.stderr.reconfigure(encoding='utf-8', errors='replace')",
    "    except (AttributeError, ValueError):",
    "        # Python < 3.7 or already-reconfigured stream; nothing to do.",
    "        pass",
]

# Find the two lines.
start = None
for i, ln in enumerate(lines):
    if ln == target[0] and i + 1 < len(lines) and lines[i + 1] == target[1]:
        start = i
        break

if start is None:
    print("target lines not found adjacent; searching separately...")
    for i, ln in enumerate(lines):
        if "io.TextIOWrapper(sys.stdout.buffer" in ln:
            print("  stdout wrapper at line %d" % (i + 1))
        if "io.TextIOWrapper(sys.stderr.buffer" in ln:
            print("  stderr wrapper at line %d" % (i + 1))
    sys.exit(1)

# Confirm we're in the right context: previous non-blank line should mention encoding / UTF-8 / try.
print("found at lines %d-%d" % (start + 1, start + 2))
print("context lines %d-%d:" % (start - 2, start + 3))
for i in range(max(0, start - 2), min(len(lines), start + 3)):
    print("  %4d| %s" % (i + 1, lines[i]))

new_lines = lines[:start] + replacement + lines[start + 2:]
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
