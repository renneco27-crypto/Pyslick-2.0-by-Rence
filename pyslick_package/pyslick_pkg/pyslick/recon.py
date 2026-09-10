#!/usr/bin/env python3
"""
recon.py — PySlick Autonomous Recon Pipeline

Run:
    pyslick recon "make the microphone larger"

Phases (all automatic except the final patch write):
    1  git log               — orient, find prior fixes
    2a find_nearest_nodes    — fuzzy graph match → ranked node list
    2b graphify.query()      — connected callees/callers for top matched .py files
    3  patchit -l            — read with line numbers
    4  jsx_tag_checker       — validate JSX
    5  show diff → CONFIRM   — one human gate
    6  patchit -f            — apply the patch
    7  git log               — verify checkpoint recorded
"""

import sys
import os
import re

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from find_nearest_nodes import (
    load_graph_nodes,
    load_graphify_vocab,
    expand_query_with_vocab,
    find_closest_graph_nodes,
)
from graphify import query as graphify_query
from jsx_tag_checker import check_jsx_tags
from patchit import (
    mode_show_lines,
    mode_find_replace,
    read_file,
)

from . import git_log, git_checkpoint

BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

def hdr(phase: str, title: str):
    print(f"\n{BOLD}{CYAN}━━  {phase}  {RST}{BOLD}{title}{RST}")
    print(f"{DIM}{'─' * 58}{RST}")

def ok(msg):  print(f"{GREEN}  ✔ {msg}{RST}")
def warn(msg): print(f"{YELL}  ⚠ {msg}{RST}")
def err(msg):  print(f"{RED}  ✖ {msg}{RST}")


def phase1_orient():
    hdr("Phase 1", "Orient — git log")
    git_log()


def _py_files_from_node_ids(node_ids: list[str]) -> list[str]:
    seen = []
    for nid in node_ids:
        parts = nid.split("_")
        if len(parts) >= 2:
            module = parts[1]
            candidate = os.path.join(os.getcwd(), f"{module}.py")
            if os.path.isfile(candidate) and candidate not in seen:
                seen.append(candidate)
    return seen


def _run_graphify_query(py_file: str, question: str, top_k: int = 3):
    print(f"\n  {DIM}graphify.query({os.path.basename(py_file)!r}, {question!r}){RST}")
    try:
        results = graphify_query(py_file, question, top_k=top_k, depth=2, direction="both")
        if not results:
            print(f"  {DIM}  (no matches){RST}")
            return

        for r in results:
            sym = r.symbol
            callees = sorted(sym.get("callees", set()))
            callers = sorted(sym.get("callers", set()))

            print(f"\n  {BOLD}{CYAN}[{sym['type']}] {sym['name']}{RST}  "
                  f"L{sym['start_line']}–{sym['end_line']}  "
                  f"{DIM}score={r.score:.2f}{RST}")

            if sym.get("docstring"):
                print(f"  {DIM}  \"{sym['docstring'][:100]}\"{RST}")
            if callees:
                print(f"  {GREEN}  calls   → {', '.join(callees)}{RST}")
            if callers:
                print(f"  {YELL}  called by ← {', '.join(callers)}{RST}")

            if r.connections:
                print(f"  {DIM}  connected nodes:{RST}")
                for c in r.connections:
                    csym = c.symbol
                    print(f"    {DIM}· [{csym['type']}] {csym['name']} "
                          f"L{csym['start_line']}–{csym['end_line']}{RST}")

    except FileNotFoundError:
        warn(f"graphify: file not found — {py_file}")
    except Exception as e:
        warn(f"graphify: {e}")


def phase2_locate(directive: str) -> str | None:
    hdr("Phase 2a", f"Fuzzy graph match — find-nearest-nodes: '{directive}'")

    nodes = load_graph_nodes()
    if not nodes:
        err("No graph nodes loaded. Run: graphify extract . --code-only")
        return None

    print(f"  Loaded {len(nodes)} graph nodes")
    vocab = load_graphify_vocab()
    expanded = expand_query_with_vocab(directive, vocab)
    if expanded != directive:
        print(f"  {DIM}[vocab expanded]: {expanded}{RST}")

    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio

    labels = [n["label"] for n in nodes]
    raw_results = process.extract(expanded, labels, scorer=WRatio, limit=5)

    print("\n--- Closest Graph Node Matches (Fuzzy Vocab) ---")
    top_node_ids = []
    for match, score, index in raw_results:
        node = nodes[index]
        print(f"  [{score:5.1f}%]  {node['label']}  ({node['type']})  ->  {node['id']}")
        top_node_ids.append(node["id"])

    hdr("Phase 2b", "Connected nodes — graphify.query on matched modules")

    py_files = _py_files_from_node_ids(top_node_ids)

    if py_files:
        for py_file in py_files[:2]:
            _run_graphify_query(py_file, directive)
    else:
        warn("No .py module files resolved from node ids — skipping graphify query.")
        warn("(This is normal for frontend .tsx targets — fuzzy results above are enough)")

    print()
    file_path = input(
        f"  {BOLD}Enter target file path{RST} "
        f"{DIM}(e.g. src/app/.../page.tsx  or  press Enter to abort){RST}: "
    ).strip()

    if not file_path:
        warn("No file path given — aborting.")
        return None

    file_path = file_path.replace("\\", "/")
    ok(f"Target: {file_path}")
    return file_path


def phase3_inspect(file_path: str) -> tuple[str, bool]:
    hdr("Phase 3", "Inspect — patchit -l  +  jsx-check")

    if not os.path.isfile(file_path):
        err(f"File not found: {file_path}")
        sys.exit(1)

    print(f"\n  {BOLD}[patchit -l]{RST}")
    mode_show_lines(file_path)
    listing = read_file(file_path)

    print(f"\n  {BOLD}[jsx-check]{RST}")
    check_jsx_tags(file_path)

    jsx_clean = input(f"\n  JSX check done. Looked clean? {DIM}(y/n){RST}: ").strip().lower()
    jsx_ok = jsx_clean in ("y", "yes", "")

    if jsx_ok:
        ok("JSX validated")
    else:
        warn("JSX issues noted — proceeding with caution")

    return listing, jsx_ok


def phase4_identify(listing: str) -> tuple[str | None, str | None]:
    hdr("Phase 4", "Identify — find exact className to patch")

    class_re = re.compile(r'(\d+)\s+.*className=["\']([^"\']{10,})["\']')
    hits = class_re.findall(listing)

    if hits:
        print("\n  className entries found:")
        for i, (lineno, classes) in enumerate(hits):
            print(f"  [{i}]  line {lineno}: {YELL}{classes}{RST}")

        choice = input(
            f"\n  Pick index to use as FIND target "
            f"{DIM}(or just paste your own string){RST}: "
        ).strip()

        if choice.isdigit() and int(choice) < len(hits):
            find_str = hits[int(choice)][1]
        else:
            find_str = choice
    else:
        warn("No className lines auto-detected from listing.")
        find_str = input("  Paste the exact string to FIND: ").strip()

    if not find_str:
        return None, None

    ok(f"Find:    {find_str}")
    replace_str = input(f"  {BOLD}Replace with:{RST} ").strip()

    if not replace_str:
        return None, None

    return find_str, replace_str


def phase5_patch(file_path: str, find_str: str, replace_str: str) -> bool:
    hdr("Phase 5", "Patch — diff preview → confirm → apply")

    print(f"\n  {BOLD}Planned change:{RST}")
    print(f"  {RED}  - {find_str}{RST}")
    print(f"  {GREEN}  + {replace_str}{RST}")
    print(f"  {DIM}  in {file_path}{RST}")

    confirm = input(f"\n{BOLD}  ⏸  Apply this patch? (yes/no): {RST}").strip().lower()
    if confirm not in ("yes", "y"):
        warn("Patch aborted by user.")
        return False

    original_stdin = sys.stdin
    feed = f"{find_str}\nEND\n{replace_str}\nEND\ny\n"
    sys.stdin = __import__("io").StringIO(feed)

    try:
        orig_argv = sys.argv
        sys.argv = ["patchit", file_path, "-f"]
        mode_find_replace(file_path)
        sys.argv = orig_argv
    finally:
        sys.stdin = original_stdin

    ok("Patch applied — creating safety checkpoint...")
    git_checkpoint()
    return True


def phase6_verify():
    hdr("Phase 6", "Verify — confirm checkpoint in git log")
    git_log()
    ok("Pipeline complete.")


def main():
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════╗
║      PySlick Autonomous Recon-to-Patch           ║
╚══════════════════════════════════════════════════╝{RST}""")

    if len(sys.argv) > 1:
        directive = " ".join(sys.argv[1:])
    else:
        directive = input(f"\n  {BOLD}Directive:{RST} ").strip()

    if not directive:
        err("No directive given.")
        sys.exit(1)

    print(f"\n  {DIM}Directive: {directive}{RST}")

    phase1_orient()

    file_path = phase2_locate(directive)
    if not file_path:
        sys.exit(1)

    listing, jsx_ok = phase3_inspect(file_path)
    if not jsx_ok:
        go = input(f"  {YELL}JSX issues present. Continue anyway? (yes/no):{RST} ").strip().lower()
        if go not in ("yes", "y"):
            err("Aborted.")
            sys.exit(1)

    find_str, replace_str = phase4_identify(listing)
    if not find_str or not replace_str:
        err("No patch target — exiting.")
        sys.exit(1)

    patched = phase5_patch(file_path, find_str, replace_str)

    if patched:
        phase6_verify()
    else:
        warn("No patch written — skipping Phase 6.")


if __name__ == "__main__":
    main()
