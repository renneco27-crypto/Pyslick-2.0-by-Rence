from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("graphcmd2-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "graphify.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"
lines = text.split(eol)

# Find the stats handler line.
stats_idx = None
for i, ln in enumerate(lines):
    if 'sub in ("stats", "summary", "overview")' in ln:
        stats_idx = i
        break

if stats_idx is None:
    print("stats handler not found")
    sys.exit(1)

print("stats handler at line %d" % (stats_idx + 1))
print("  line %d: %r" % (stats_idx, lines[stats_idx]))
print("  line %d: %r" % (stats_idx + 1, lines[stats_idx + 1]))

# Check whether graph handler already present.
tail = eol.join(lines[stats_idx:stats_idx + 40])
if 'sub == "graph"' in tail:
    print("graph handler already present -- SKIPPED")
    sys.exit(0)

# Find the end of the stats handler block (next `elif sub` or `else` at same indent).
indent = lines[stats_idx][:len(lines[stats_idx]) - len(lines[stats_idx].lstrip())]
end_idx = None
for i in range(stats_idx + 1, len(lines)):
    stripped = lines[i]
    # Stop when we hit a sibling branch at the same indentation.
    if stripped.startswith(indent + "elif") or stripped.startswith(indent + "else"):
        end_idx = i
        break

if end_idx is None:
    print("could not find end of stats handler block")
    sys.exit(1)

print("inserting before line %d" % (end_idx + 1))

# Build the graph handler block.
handler = [
    indent + 'elif sub == "graph":',
    indent + '    # pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]',
    indent + '    rest = args[1:]',
    indent + '    if not rest:',
    indent + '        print("Usage: pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]")',
    indent + '        return',
    indent + '    fmt = "ascii"',
    indent + '    depth = 1',
    indent + '    direction = "both"',
    indent + '    positional = []',
    indent + '    i = 0',
    indent + '    while i < len(rest):',
    indent + '        a = rest[i]',
    indent + '        if a == "--format" and i + 1 < len(rest):',
    indent + '            fmt = rest[i + 1]; i += 2',
    indent + '        elif a.startswith("--format="):',
    indent + '            fmt = a.split("=", 1)[1]; i += 1',
    indent + '        elif a == "--depth" and i + 1 < len(rest):',
    indent + '            try: depth = int(rest[i + 1])',
    indent + '            except ValueError: pass',
    indent + '            i += 2',
    indent + '        elif a.startswith("--depth="):',
    indent + '            try: depth = int(a.split("=", 1)[1])',
    indent + '            except ValueError: pass',
    indent + '            i += 1',
    indent + '        elif a == "--direction" and i + 1 < len(rest):',
    indent + '            direction = rest[i + 1]; i += 2',
    indent + '        elif a.startswith("--direction="):',
    indent + '            direction = a.split("=", 1)[1]; i += 1',
    indent + '        else:',
    indent + '            positional.append(a); i += 1',
    indent + '    if not positional:',
    indent + '        print("Usage: pyslick graphify graph <func> [--format=...] [--depth N] [--direction in|out|both]")',
    indent + '        return',
    indent + '    show_graph(positional[0], root=".", fmt=fmt, depth=depth, direction=direction)',
    indent + '    return',
    '',
]

new_lines = lines[:end_idx] + handler + lines[end_idx:]
new_text = eol.join(new_lines)

# Also update the help text.
old_help = '''    {GREEN}pyslick graphify stats{RST}
        Show total indexed functions, call links, and top connected God-Node functions.
    """'''
new_help = '''    {GREEN}pyslick graphify stats{RST}
        Show total indexed functions, call links, and top connected God-Node functions.

    {GREEN}pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]{RST}
        Render callers and callees as an ascii tree (default) or a mermaid flowchart.
    """'''
if new_help in new_text:
    print("help already updated")
elif old_help in new_text:
    new_text = new_text.replace(old_help, new_help, 1)
    print("help updated")
else:
    print("help anchor not found -- continuing without help update")

shutil.copy2(p, BK / p.name)
p.write_bytes(new_text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
