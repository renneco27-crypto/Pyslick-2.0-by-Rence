from pathlib import Path
import subprocess, sys

p = Path("__main__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

new_content = '''import os
import sys
from pyslick import main

if __name__ == "__main__":
    _rc = 0
    try:
        main()
    except SystemExit as _e:
        _rc = _e.code if isinstance(_e.code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        _rc = 1
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(_rc)
'''

p.write_bytes(new_content.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    p.write_bytes(raw)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED __main__.py")
