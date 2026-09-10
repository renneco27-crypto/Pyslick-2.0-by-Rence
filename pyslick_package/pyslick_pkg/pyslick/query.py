#!/usr/bin/env python3
"""
query.py — PySlick's "smart agent, no LLM" entry point.

    pyslick query "what do I need to change to make the mic button bigger"

Pipeline (all local, all fast — safe on a slow CPU, no model to load):

  1. Build (or reuse) a lightweight file index of the project — skips
     node_modules/.git/dist/build/.next/__pycache__ so it never scans the
     whole repo, just candidate source files.
  2. Rank candidate files against the directive using rapidfuzz (string
     similarity against filename + relative path tokens). If a graphify
     graph (graphify-out/graph.json) exists, that's used first for a
     richer node-level match; otherwise falls back to the raw file scan.
  3. For every top-ranked *.py file, run graphify's AST call-graph query
     (graphify.query) to pull out only the matched function/class plus
     its direct callers/callees (depth=2 by default) — i.e. the
     "necessary end-to-end" surface, not the whole file.
  4. Print a compact report: file, symbol, line range, one-line doc,
     what it calls, what calls it. Use --full to also dump source.

This is read-only. It never writes, patches, or commits anything —
that's what `pyslick patchit` / `pyslick recon` are for.
"""

import os
import sys
import json
import argparse
from pathlib import Path

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from graphify import query as graphify_query

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out",
}

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}
GRAPH_PATH = os.path.join("graphify-out", "graph.json")


# ─────────────────────────────────────────────────────────────────────────
# Step 1 — cheap file index (no parsing, just a directory walk)
# ─────────────────────────────────────────────────────────────────────────
def build_file_index(root: str = ".") -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix in CODE_EXTS:
                files.append(os.path.normpath(os.path.join(dirpath, fn)))
    return files


# ─────────────────────────────────────────────────────────────────────────
# Step 2 — rank candidates
# ─────────────────────────────────────────────────────────────────────────
def rank_files_by_name(directive: str, files: list[str], top_n: int = 6) -> list[tuple[str, float]]:
    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio

    # score against "dir/dir/filename" with separators turned to spaces,
    # so tokens like "mic", "button", "size" match path/file words.
    labels = [f.replace(os.sep, " ").replace("_", " ").replace("-", " ") for f in files]
    results = process.extract(directive, labels, scorer=WRatio, limit=top_n)
    return [(files[idx], score) for _label, score, idx in results]


def rank_via_graph(directive: str, top_n: int = 6):
    """Use an existing graphify-out/graph.json for a richer node-level match, if present."""
    if not os.path.exists(GRAPH_PATH):
        return None
    try:
        from find_nearest_nodes import load_graph_nodes, load_graphify_vocab, expand_query_with_vocab
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio

        nodes = load_graph_nodes()
        if not nodes:
            return None
        vocab = load_graphify_vocab()
        expanded = expand_query_with_vocab(directive, vocab)

        labels = [n["label"] for n in nodes]
        raw = process.extract(expanded, labels, scorer=WRatio, limit=top_n)

        candidate_files = []
        seen = set()
        for _match, score, idx in raw:
            nid = nodes[idx]["id"]
            parts = nid.split("_")
            if len(parts) >= 2:
                module = parts[1]
                for ext in (".py",):
                    cand = f"{module}{ext}"
                    if os.path.isfile(cand) and cand not in seen:
                        seen.add(cand)
                        candidate_files.append((cand, score))
        return candidate_files or None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Step 3 — AST call-graph query per candidate .py file
# ─────────────────────────────────────────────────────────────────────────
def inspect_file(py_file: str, directive: str, top_k: int, depth: int):
    try:
        return graphify_query(py_file, directive, top_k=top_k, depth=depth, direction="both")
    except FileNotFoundError:
        return []
    except SyntaxError:
        return []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────
# Step 4 — report
# ─────────────────────────────────────────────────────────────────────────
def print_report(directive: str, hits: list, full: bool):
    if not hits:
        print(f"{YELL}No matching symbols found for:{RST} \"{directive}\"")
        print(f"{DIM}Tip: run this from your project root, and make sure the relevant "
              f"files end in .py (JS/TS files are located but not AST-parsed yet).{RST}")
        return

    print(f"\n{BOLD}{CYAN}Query:{RST} \"{directive}\"")
    print(f"{DIM}{'─'*70}{RST}")

    for file_path, results in hits:
        if not results:
            continue
        print(f"\n{BOLD}{file_path}{RST}")
        for r in results:
            sym = r.symbol
            parent = f" (in {sym['parent']})" if sym.get("parent") else ""
            print(f"  {GREEN}[{sym['type']}]{RST} {BOLD}{sym['name']}{RST}{parent}  "
                  f"{DIM}L{sym['start_line']}-{sym['end_line']}  score={r.score:.1f}{RST}")
            if sym.get("docstring"):
                print(f"    {DIM}\"{sym['docstring'][:100]}\"{RST}")

            callees = sorted(sym.get("callees", set()))
            callers = sorted(sym.get("callers", set()))
            if callees:
                print(f"    {CYAN}calls   →{RST} {', '.join(callees)}")
            if callers:
                print(f"    {YELL}called by ←{RST} {', '.join(callers)}")

            if r.connections:
                print(f"    {DIM}connected (depth 2):{RST}")
                for c in r.connections:
                    csym = c.symbol
                    print(f"      {DIM}· [{csym['type']}] {csym['name']} "
                          f"L{csym['start_line']}-{csym['end_line']}{RST}")

            if full:
                print(f"{DIM}    ── source ──{RST}")
                for line in r.code.splitlines():
                    print(f"    {line}")

    print(f"\n{DIM}{'─'*70}{RST}")
    print(f"{BOLD}Next step:{RST} pyslick patchit <file> -f   (or -r / -i)  to edit one of the spots above.")
    print(f"{DIM}Or run 'pyslick recon \"{directive}\"' for the guided find→patch→checkpoint flow.{RST}")


# ─────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────
def run(directive: str, root: str = ".", top_files: int = 3, top_symbols: int = 3,
        depth: int = 2, full: bool = False):
    graph_ranked = rank_via_graph(directive, top_n=top_files * 2)

    if graph_ranked:
        candidates = graph_ranked
    else:
        all_files = build_file_index(root)
        if not all_files:
            print(f"{RED}No source files found under '{root}'.{RST}")
            return
        candidates = rank_files_by_name(directive, all_files, top_n=top_files * 3)

    # keep only .py for AST inspection, top N
    py_candidates = [f for f, _score in candidates if f.endswith(".py")][:top_files]
    non_py = [f for f, _score in candidates if not f.endswith(".py")][:top_files]

    hits = []
    for py_file in py_candidates:
        results = inspect_file(py_file, directive, top_k=top_symbols, depth=depth)
        hits.append((py_file, results))

    print_report(directive, hits, full)

    if non_py:
        print(f"\n{DIM}Non-Python files also matched by name (not AST-inspected):{RST}")
        for f in non_py:
            print(f"  {DIM}· {f}{RST}")


def main():
    parser = argparse.ArgumentParser(prog="pyslick query", add_help=True)
    parser.add_argument("directive", nargs="+", help="natural language description of what you want to change")
    parser.add_argument("--root", default=".", help="project root to scan (default: cwd)")
    parser.add_argument("--top-files", type=int, default=3, help="max candidate files to inspect (default 3)")
    parser.add_argument("--top-symbols", type=int, default=3, help="max matched symbols per file (default 3)")
    parser.add_argument("--depth", type=int, default=2, help="call-graph depth to follow (default 2)")
    parser.add_argument("--full", action="store_true", help="also print full matched source code")
    args = parser.parse_args()

    directive = " ".join(args.directive)
    run(directive, root=args.root, top_files=args.top_files,
        top_symbols=args.top_symbols, depth=args.depth, full=args.full)


if __name__ == "__main__":
    main()
