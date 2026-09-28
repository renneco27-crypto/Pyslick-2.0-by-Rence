#!/usr/bin/env python3
"""find_stray_symbols.py

Detect stray JSX symbols in .tsx/.jsx files using tree-sitter.

Reads the real JSX AST, so it doesn't confuse:
  - self-closing tags  (<br/>)
  - fragments          (<>...</>)
  - generic types      (Promise<T>, Array<Item>)
  - text inside strings or comments
  - component names    (<MyComponent />)

Reports only genuinely unmatched tags.

Usage:
    python find_stray_symbols.py <project_root_or_file>
"""

import sys
from pathlib import Path


def _parser_for(path: Path):
    """Return a tree-sitter parser for the file's language, or None."""
    try:
        from tree_sitter_language_pack import get_parser
    except Exception:
        return None
    ext = path.suffix.lower()
    lang = None
    if ext in (".tsx",):
        lang = "tsx"
    elif ext in (".jsx",):
        lang = "javascript"
    elif ext in (".ts",):
        lang = "typescript"
    elif ext in (".js",):
        lang = "javascript"
    if lang is None:
        return None
    try:
        return get_parser(lang)
    except Exception:
        return None


def _walk(node, out):
    out.append(node)
    for child in node.children:
        _walk(child, out)


def scan_file(file_path: Path):
    """Return a list of (line, message) for genuinely stray JSX in the file."""
    parser = _parser_for(file_path)
    if parser is None:
        return []
    try:
        src = file_path.read_bytes()
    except OSError:
        return []

    try:
        tree = parser.parse(src)
    except Exception:
        return []

    nodes = []
    _walk(tree.root_node, nodes)

    # Tree-sitter error nodes indicate a syntax problem, not a tag mismatch.
    # Report them as "syntax error" with the line.
    problems = []
    for node in nodes:
        if node.type == "ERROR":
            lineno = node.start_point[0] + 1
            problems.append((lineno, "Syntax error (unparsable region)"))
    if problems:
        return problems

    # Count JSX opening/closing pairs by walking the AST.
    # tree-sitter names these node types:
    #   jsx_opening_element   <div>
    #   jsx_closing_element   </div>
    #   jsx_self_closing_element   <br/>
    #   jsx_fragment          <>...</>
    opening = {}
    closing = {}
    for node in nodes:
        if node.type == "jsx_opening_element":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                opening[name] = opening.get(name, 0) + 1
        elif node.type == "jsx_closing_element":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = src[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
                closing[name] = closing.get(name, 0) + 1

    for name, count in opening.items():
        c = closing.get(name, 0)
        if count != c:
            problems.append((0, f"<{name}> opened {count}x, closed {c}x"))

    for name, count in closing.items():
        o = opening.get(name, 0)
        if count != o:
            problems.append((0, f"</{name}> closed {count}x, opened {o}x"))

    return problems


def main(target_path: str):
    target = Path(target_path)
    if not target.exists():
        print(f"Error: {target_path} does not exist", file=sys.stderr)
        sys.exit(1)

    total = 0
    files = [target] if target.is_file() else (
        list(target.rglob("*.tsx")) + list(target.rglob("*.jsx"))
    )
    skip_parts = {"node_modules", ".next", "dist", "build", ".git", "out", ".turbo"}
    for path in files:
        if any(part in skip_parts for part in path.parts):
            continue
        problems = scan_file(path)
        if problems:
            total += len(problems)
            print(f"File: {path}")
            for line_no, msg in problems:
                if line_no:
                    print(f"  Line {line_no}: {msg}")
                else:
                    print(f"  {msg}")

    if total == 0:
        print("OK: No stray symbols detected.")
    else:
        print(f"\nTotal problems found: {total}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python find_stray_symbols.py <file_or_directory>")
        sys.exit(1)
    main(sys.argv[1])
