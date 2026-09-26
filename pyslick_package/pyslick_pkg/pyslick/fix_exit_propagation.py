from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Find __main__ block.
start = None
for i, ln in enumerate(lines):
    if ln.startswith('if __name__ == "__main__":'):
        start = i
        break
if start is None:
    print("__main__ block not found")
    sys.exit(1)

# Find end of block (next top-level def/class/if __name__, or EOF).
end = None
for i in range(start + 1, len(lines)):
    if lines[i].startswith("def ") or lines[i].startswith("class ") or lines[i].startswith('if __name__'):
        end = i
        break
else:
    end = len(lines)
while end > start and lines[end - 1].strip() == "":
    end -= 1

print("replacing lines %d-%d:" % (start + 1, end))
for i in range(start, end):
    print("  ", repr(lines[i]))

new_block = [
    'if __name__ == "__main__":',
    '    _rc = 0',
    '    try:',
    '        main()',
    '    except SystemExit as _e:',
    '        _rc = _e.code if isinstance(_e.code, int) else 1',
    '    except Exception:',
    '        import traceback as _tb',
    '        _tb.print_exc()',
    '        _rc = 1',
    '    finally:',
    '        # Bypass Python shutdown flush. On Windows, a closed stdout pipe',
    '        # (e.g. piping to head) makes the interpreter-shutdown flush raise',
    '        # OSError, which Python logs as "Exception ignored in:".',
    '        # os._exit skips the shutdown path entirely.',
    '        try:',
    '            sys.stdout.flush()',
    '        except Exception:',
    '            pass',
    '        os._exit(_rc)',
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
