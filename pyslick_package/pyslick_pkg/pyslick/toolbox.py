#!/usr/bin/env python3
"""
toolbox.py — small PowerShell-equivalent read-only commands, bundled so you
don't have to remember the PowerShell one-liners.

    pyslick ls  [root]
        == Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name

    pyslick lines <file>
        == Get-Content -Path <file> | ForEach-Object -Begin {$i=1} -Process {"{0:D3}: {1}" -f $i++,$_}

    pyslick grep <file> <pattern> [<pattern> ...] [--context N]
        == Select-String -Path <file> -Pattern <patterns> -Context N,N
"""

import os
import re
import sys
import argparse

CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RST   = "\033[0m"

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", ".pyslick_backups",
}


# ─────────────────────────────────────────────────────────────────────────
# ls  ==  Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name
# ─────────────────────────────────────────────────────────────────────────
def mode_ls(root: str = ".", names_only: bool = True):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            count += 1
            if names_only:
                print(fn)
            else:
                print(os.path.normpath(os.path.join(dirpath, fn)))
    if count == 0:
        print(f"{DIM}(no files found under '{root}'){RST}")


# ─────────────────────────────────────────────────────────────────────────
# lines  ==  Get-Content | ForEach-Object numbered
# ─────────────────────────────────────────────────────────────────────────
def mode_lines(filepath: str):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            print(f"{i:03d}: {line.rstrip()}")


# ─────────────────────────────────────────────────────────────────────────
# grep  ==  Select-String -Pattern ... -Context N,N
# ─────────────────────────────────────────────────────────────────────────
def mode_grep(filepath: str, patterns: list[str], context: int = 1):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    compiled = [re.compile(p) for p in patterns]
    hits = []
    for i, line in enumerate(lines):
        for pat, raw in zip(compiled, patterns):
            if pat.search(line):
                hits.append((i, raw))
                break

    if not hits:
        print(f"{DIM}No matches for {patterns} in {filepath}{RST}")
        return

    print(f"{BOLD}{filepath}{RST} — {len(hits)} match(es)")
    last_printed = -1
    for line_idx, matched_pattern in hits:
        start = max(0, line_idx - context)
        end = min(len(lines), line_idx + context + 1)
        if start > last_printed + 1:
            print(f"{DIM}  ...{RST}")
        for j in range(start, end):
            marker = f"{GREEN}>{RST}" if j == line_idx else " "
            tag = f"{YELL}[{matched_pattern}]{RST} " if j == line_idx else ""
            print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{lines[j].rstrip()}")
        last_printed = end - 1
        print()


def main():
    parser = argparse.ArgumentParser(prog="pyslick", add_help=False)
    sub = parser.add_subparsers(dest="cmd")

    p_ls = sub.add_parser("ls")
    p_ls.add_argument("root", nargs="?", default=".")
    p_ls.add_argument("--full-path", action="store_true", help="print full paths instead of just filenames")

    p_lines = sub.add_parser("lines")
    p_lines.add_argument("file")

    p_grep = sub.add_parser("grep")
    p_grep.add_argument("file")
    p_grep.add_argument("patterns", nargs="+")
    p_grep.add_argument("--context", type=int, default=1)

    args = parser.parse_args()

    if args.cmd == "ls":
        mode_ls(args.root, names_only=not args.full_path)
    elif args.cmd == "lines":
        mode_lines(args.file)
    elif args.cmd == "grep":
        mode_grep(args.file, args.patterns, context=args.context)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
