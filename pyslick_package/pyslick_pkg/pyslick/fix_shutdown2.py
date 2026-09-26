from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Find the guard block we inserted last time (should be after main() call in __main__).
start = None
end = None
for i, ln in enumerate(lines):
    if "Suppress BrokenPipeError at interpreter shutdown" in ln:
        start = i - 1  # line before is `main()` call site, we'll re-do this whole area
        break

if start is None:
    print("could not find previous guard block")
    sys.exit(1)

# Find end of the previous guard (last line of except block).
end = start
for i in range(start, len(lines)):
    if lines[i].strip() == "" and i > start + 3:
        end = i
        break
    end = i + 1

# Show what we're removing.
print("removing lines %d..%d:" % (start + 1, end))
for i in range(start, end):
    print("  ", repr(lines[i]))

# New guard: unconditional dup2 before exit.
new_guard = [
    "if __name__ == \"__main__\":",
    "    try:",
    "        main()",
    "    finally:",
    "        # Redirect stdout to devnull so Python's shutdown flush",
    "        # writes nowhere and cannot raise BrokenPipeError.",
    "        try:",
    "            sys.stdout.flush()",
    "        except Exception:",
    "            pass",
    "        try:",
    "            _devnull = os.open(os.devnull, os.O_WRONLY)",
    "            os.dup2(_devnull, sys.stdout.fileno())",
    "            os.dup2(_devnull, sys.stderr.fileno())",
    "        except Exception:",
    "            pass",
]

# Replace from the line containing "if __name__" through end.
main_block_start = None
for i in range(start, -1, -1):
    if lines[i].startswith("if __name__"):
        main_block_start = i
        break

if main_block_start is None:
    print("could not find 'if __name__' block")
    sys.exit(1)

# Find the true end of the __main__ block.
main_block_end = main_block_start
for i in range(main_block_start + 1, len(lines)):
    if lines[i].startswith("if __name__") or lines[i].startswith("def ") or lines[i].startswith("class "):
        main_block_end = i
        break
else:
    main_block_end = len(lines)

# Trim trailing blank lines from the block.
while main_block_end > main_block_start and lines[main_block_end - 1].strip() == "":
    main_block_end -= 1

new_lines = lines[:main_block_start] + new_guard + lines[main_block_end:]
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
