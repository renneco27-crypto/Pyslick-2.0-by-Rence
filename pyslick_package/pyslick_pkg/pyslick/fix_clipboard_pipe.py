from pathlib import Path
import subprocess, sys

p = Path("clipboard.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
orig = text

# ---- write method ----
old_write = '''    def write(self, s: str) -> int:
        self._real.write(s)
        self._real.flush()
        self._buf.write(s)
        return len(s)
'''

new_write = '''    def write(self, s: str) -> int:
        try:
            self._real.write(s)
            self._real.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self._buf.write(s)
        except Exception:
            pass
        return len(s)
'''

# ---- flush method ----
old_flush = '''    def flush(self):
        self._real.flush()
'''

new_flush = '''    def flush(self):
        try:
            self._real.flush()
        except (BrokenPipeError, OSError):
            pass
'''

if new_write in text:
    print("write: already patched")
elif old_write in text:
    text = text.replace(old_write, new_write, 1)
    print("write: patched")
else:
    print("write: anchor not found")
    sys.exit(1)

if new_flush in text:
    print("flush: already patched")
elif old_flush in text:
    text = text.replace(old_flush, new_flush, 1)
    print("flush: patched")
else:
    print("flush: anchor not found")
    sys.exit(1)

if text == orig:
    print("no changes")
    sys.exit(0)

p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    p.write_bytes(raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED")
