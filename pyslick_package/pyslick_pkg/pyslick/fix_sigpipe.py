from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")

if "signal.SIGPIPE" in text:
    print("already patched")
    sys.exit(0)

# Find the import block -- add after the initial imports.
# Look for "import sys" at module top.
lines = text.split("\n")
eol = "\r\n" if "\r\n" in text else "\n"
if eol == "\r\n":
    lines = text.split("\r\n")

insert_at = None
for i, ln in enumerate(lines):
    if ln.strip() == "import sys":
        insert_at = i + 1
        break

if insert_at is None:
    print("could not find 'import sys' at top of __init__.py")
    sys.exit(1)

patch = [
    "",
    "# Restore SIGPIPE to default so piping through head/less terminates",
    "# silently instead of raising BrokenPipeError at interpreter shutdown.",
    "try:",
    "    import signal as _signal",
    "    if hasattr(_signal, \"SIGPIPE\"):",
    "        _signal.signal(_signal.SIGPIPE, _signal.SIG_DFL)",
    "except Exception:",
    "    pass",
]

new_lines = lines[:insert_at] + patch + lines[insert_at:]
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
