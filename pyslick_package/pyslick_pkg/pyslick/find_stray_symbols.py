#!/usr/bin/env python3
"""find_stray_symbols.py

Utility script to scan a directory (recursively) for stray JSX symbols such as
unmatched opening/closing tags or isolated `</>` fragments in .tsx files.

Usage:
    python find_stray_symbols.py <project_root>
"""

import sys
import os
import re
from pathlib import Path

OPEN_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*)\b[^>/]*?(?<!/)>")
CLOSE_TAG_RE = re.compile(r"</([A-Za-z][A-Za-z0-9]*)>")
SELF_CLOSING_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*)\b[^>]*?/>")
STRAY_FRAGMENT_RE = re.compile(r"</>")

def scan_file(file_path: Path):
    problems = []
    stack = []
    with file_path.open('r', encoding='utf-8') as f:
        for i, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if STRAY_FRAGMENT_RE.search(line):
                problems.append((i, "Stray </> fragment"))
            line_no_comments = re.sub(r"{\/\*.*?\*\/}", "", line)
            for _ in SELF_CLOSING_RE.finditer(line_no_comments):
                pass
            for m in OPEN_TAG_RE.finditer(line_no_comments):
                tag = m.group(1)
                stack.append((tag, i))
            for m in CLOSE_TAG_RE.finditer(line_no_comments):
                tag = m.group(1)
                if stack and stack[-1][0] == tag:
                    stack.pop()
                else:
                    problems.append((i, f"Unmatched closing tag </{tag}>"))
    for tag, lineno in stack:
        problems.append((lineno, f"Unmatched opening tag <{tag}>"))
    return problems

def main(root_dir: str):
    root = Path(root_dir)
    if not root.is_dir():
        print(f"Error: {root_dir} is not a directory", file=sys.stderr)
        sys.exit(1)
    total = 0
    for tsx_path in root.rglob('*.tsx'):
        problems = scan_file(tsx_path)
        if problems:
            total += len(problems)
            print(f"File: {tsx_path}")
            for line_no, msg in problems:
                print(f"  Line {line_no}: {msg}")
    if total == 0:
        print("OK: No stray symbols detected.")
    else:
        print(f"\nTotal problems found: {total}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python find_stray_symbols.py <project_root>")
        sys.exit(1)
    main(sys.argv[1])
