from pathlib import Path
import subprocess, sys

p = Path("__init__.py")
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Find "def main():" at module level.
main_idx = None
for i, ln in enumerate(lines):
    if ln == "def main():" or ln.startswith("def main()"):
        main_idx = i
        break

if main_idx is None:
    print("def main() not found")
    sys.exit(1)

# Find the end of main() -- next top-level def/class, or end of file.
end_idx = len(lines)
for i in range(main_idx + 1, len(lines)):
    if lines[i].startswith("def ") or lines[i].startswith("class "):
        # Back up to before trailing blank lines.
        end_idx = i
        break
else:
    end_idx = len(lines)

print("main() spans lines %d..%d" % (main_idx + 1, end_idx))

# Check whether already patched.
tail = "\n".join(lines[end_idx - 15:end_idx])
if "devnull" in tail:
    print("already patched")
    sys.exit(0)

# Find the last non-blank, non-comment line in main() to anchor.
# We'll insert just before the final blank line of main().
last_content = None
for i in range(end_idx - 1, main_idx, -1):
    stripped = lines[i].strip()
    if stripped and not stripped.startswith("#"):
        last_content = i
        break

if last_content is None:
    print("could not locate last content line of main()")
    sys.exit(1)

print("last content line %d: %r" % (last_content + 1, lines[last_content]))

# Insert the shutdown guard after the last content line.
guard = [
    "    # Suppress BrokenPipeError at interpreter shutdown when piped to head/less.",
    "    try:",
    "        sys.stdout.flush()",
    "    except (BrokenPipeError, OSError):",
    "        try:",
    "            devnull = os.open(os.devnull, os.O_WRONLY)",
    "            os.dup2(devnull, sys.stdout.fileno())",
    "        except Exception:",
    "            pass",
]

# Look one line further to make sure we're at main()'s indent level (4 spaces).
# If last_content's line doesn't start with 4 spaces, we're not in main().
first = lines[last_content]
if not first.startswith("    "):
    print("last content line is not indented under main(); aborting")
    sys.exit(1)

new_lines = lines[:last_content + 1] + guard + lines[last_content + 1:]
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
