from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("graphcmd-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "graphify.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

# 1. Add handler after the stats handler.
old_stats = '''        elif sub in ("stats", "summary", "overview"):
            show_stats(".")
'''
new_stats = '''        elif sub in ("stats", "summary", "overview"):
            show_stats(".")
            return

        elif sub == "graph":
            # pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]
            rest = args[1:]
            if not rest:
                print("Usage: pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]")
                return
            fmt = "ascii"
            depth = 1
            direction = "both"
            positional = []
            i = 0
            while i < len(rest):
                a = rest[i]
                if a == "--format" and i + 1 < len(rest):
                    fmt = rest[i + 1]
                    i += 2
                elif a.startswith("--format="):
                    fmt = a.split("=", 1)[1]
                    i += 1
                elif a == "--depth" and i + 1 < len(rest):
                    try:
                        depth = int(rest[i + 1])
                    except ValueError:
                        pass
                    i += 2
                elif a.startswith("--depth="):
                    try:
                        depth = int(a.split("=", 1)[1])
                    except ValueError:
                        pass
                    i += 1
                elif a == "--direction" and i + 1 < len(rest):
                    direction = rest[i + 1]
                    i += 2
                elif a.startswith("--direction="):
                    direction = a.split("=", 1)[1]
                    i += 1
                else:
                    positional.append(a)
                    i += 1
            if not positional:
                print("Usage: pyslick graphify graph <func> [--format=...] [--depth N] [--direction in|out|both]")
                return
            show_graph(positional[0], root=".", fmt=fmt, depth=depth, direction=direction)
            return
'''

if new_stats in text:
    print("handler already present")
else:
    if old_stats not in text:
        print("anchor for stats handler not found")
        print("--- searching for 'stats' handler ---")
        idx = text.find("stats\", \"summary\"")
        if idx > 0:
            print(text[idx - 200:idx + 200])
        sys.exit(1)
    text = text.replace(old_stats, new_stats, 1)
    print("handler wired")

# 2. Add help line for graph subcommand.
old_help = '''    {GREEN}pyslick graphify stats{RST}
        Show total indexed functions, call links, and top connected God-Node functions.
    """'''
new_help = '''    {GREEN}pyslick graphify stats{RST}
        Show total indexed functions, call links, and top connected God-Node functions.

    {GREEN}pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]{RST}
        Render callers and callees as an ascii tree (default) or a mermaid flowchart.
    """'''

if new_help in text:
    print("help already updated")
elif old_help in text:
    text = text.replace(old_help, new_help, 1)
    print("help updated")
else:
    print("help anchor not found -- skipping help update (not fatal)")

# Write and verify.
shutil.copy2(p, BK / p.name)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
