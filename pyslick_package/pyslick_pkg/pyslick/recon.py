#!/usr/bin/env python3
"""
recon.py — PySlick Autonomous Recon Pipeline

Run:
    pyslick recon "make the microphone larger"

    # With the write-test-fix loop enabled:
    pyslick recon "make the microphone larger" --auto-fix --test-cmd "pytest -q"
    pyslick recon "..." --auto-fix --test-cmd "pnpm run build" --max-attempts 5

Phases (all automatic except the final patch write):
    1  git log               — orient, find prior fixes
    2a find_nearest_nodes    — fuzzy graph match → ranked node list
    2b graphify.query()      — connected callees/callers for top matched .py files
    3  patchit -l            — read with line numbers
    4  jsx_tag_checker       — validate JSX
    5  show diff → CONFIRM   — one human gate
       (or, with --auto-fix: patch → run --test-cmd → on fail, ask Claude
        for a correction → retry up to --max-attempts, still gated by a
        final "keep this change?" confirm even once tests pass)
    6  patchit -f            — apply the patch
    7  git log               — verify checkpoint recorded

--auto-fix flags (all optional, all off by default):
    --auto-fix              enable the retry loop for phase 5
    --test-cmd "<command>"  the command to run after each patch attempt,
                             e.g. "pytest -q", "pnpm run build", "npm test"
                             Required for --auto-fix to do anything beyond
                             the normal single-shot patch.
    --max-attempts N         cap on retries (default 3)
"""

import sys
import os
import re
import subprocess

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from find_nearest_nodes import (
    load_graph_nodes,
    load_graphify_vocab,
    expand_query_with_vocab,
    find_closest_graph_nodes,
)
from comment_blocks import (
    scan_project_for_comment_blocks,
    comment_nodes_as_graph_nodes,
)
from graphify import query as graphify_query
from jsx_tag_checker import check_jsx_tags
from patchit import (
    mode_show_lines,
    mode_find_replace,
    read_file,
)
from autoloop import run_auto_fix_loop, print_summary
from llm import maybe_extract_find_replace

try:
    from . import git_log, git_checkpoint
except ImportError:
    try:
        from pyslick import git_log, git_checkpoint
    except ImportError:
        def git_log(): pass
        def git_checkpoint(commit_msg=None): pass

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


def _ensure_graph_exists():
    """Check if graphify graph exists. If not, try to install graphify and run extraction."""
    # Check if graphify-out/graph.json exists
    graph_path = os.path.join(os.getcwd(), "graphify-out", "graph.json")
    if os.path.exists(graph_path):
        return True

    warn("No graphify graph found. Attempting to auto-generate...")

    # Check if graphify is installed
    try:
        result = subprocess.run(["graphify", "--version"], capture_output=True, text=True)
        if result.returncode == 0:
            ok("graphify is installed")
        else:
            raise FileNotFoundError
    except (FileNotFoundError, subprocess.SubprocessError):
        warn("graphify not found. Attempting to install...")
        try:
            print(f"{DIM}Running: pip install graphify{RST}")
            subprocess.run([sys.executable, "-m", "pip", "install", "graphify"], check=True)
            ok("graphify installed successfully")
        except subprocess.CalledProcessError:
            err("Failed to install graphify. Please install manually: pip install graphify")
            return False

    # Run graphify extract
    try:
        print(f"{DIM}Running: graphify extract . --code-only{RST}")
        result = subprocess.run(
            ["graphify", "extract", ".", "--code-only"],
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )
        if result.returncode == 0:
            ok("Graph generated successfully")
            return True
        else:
            err(f"Graphify extraction failed: {result.stderr}")
            return False
    except Exception as e:
        err(f"Failed to run graphify extract: {e}")
        return False


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


def phase2_locate(directive: str) -> list[str] | None:
    hdr("Phase 2a", f"Fuzzy graph match — find-nearest-nodes: '{directive}'")

    # Auto-generate graph if it doesn't exist
    _ensure_graph_exists()

    nodes = load_graph_nodes()
    if not nodes:
        warn("No graph nodes loaded (run: graphify extract . --code-only for function-level matches).")
        nodes = []

    # Scan the project for comment-defined blocks (both explicit
    # pyslick:start/end markers and natural descriptive comments) and
    # merge them into the same candidate pool as the AST graph nodes, so
    # a directive can match a comment even when there's no named function
    # to anchor to (CSS rule groups, HTML sections, config blocks, etc).
    comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
    comment_nodes = comment_nodes_as_graph_nodes(comment_nodes_raw)
    if comment_nodes:
        print(f"  Loaded {len(comment_nodes)} comment block(s) "
              f"({sum(1 for n in comment_nodes if n['type'] == 'marker_block')} marker, "
              f"{sum(1 for n in comment_nodes if n['type'] == 'descriptive_block')} descriptive)")

    unterminated = [n for n in comment_nodes_raw if n.type == "marker_block" and n.end_line == -1]
    if unterminated:
        warn(f"{len(unterminated)} unterminated pyslick:start marker(s) found — missing pyslick:end:")
        for n in unterminated:
            warn(f"    {n.file}:{n.start_line}  '{n.comment_text}'")

    all_nodes = nodes + comment_nodes
    if not all_nodes:
        err("No graph nodes and no comment blocks found — nothing to search.")
        err("Run: graphify extract . --code-only   (or add some comments/markers)")
        return None

    print(f"  Total candidates: {len(all_nodes)}")
    vocab = load_graphify_vocab()
    expanded = expand_query_with_vocab(directive, vocab)
    if expanded != directive:
        print(f"  {DIM}[vocab expanded]: {expanded}{RST}")

    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio

    labels = [n["label"] for n in all_nodes]
    raw_results = process.extract(expanded, labels, scorer=WRatio, limit=8)

    print("\n--- Closest Matches (AST nodes + comment blocks) ---")
    top_node_ids = []
    for match, score, index in raw_results:
        node = all_nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            cn = node["_comment_node"]
            print(f"  [{score:5.1f}%]  {DIM}[comment]{RST} \"{node['label'][:60]}\"  "
                  f"-> {cn.file}:{cn.start_line}-{cn.end_line}")
        else:
            print(f"  [{score:5.1f}%]  {node['label']}  ({node['type']})  ->  {node['id']}")
        top_node_ids.append(node["id"])

    hdr("Phase 2b", "Connected nodes — graphify.query on matched modules")

    py_files = _py_files_from_node_ids([n for n in top_node_ids if not n.startswith(("marker::", "comment::"))])

    if py_files:
        for py_file in py_files[:2]:
            _run_graphify_query(py_file, directive)
    else:
        warn("No .py module files resolved from node ids — skipping graphify query.")
        warn("(This is normal for frontend/comment-block targets — matches above are enough)")

    print()
    raw_input_str = input(
        f"  {BOLD}Enter target file path(s){RST} "
        f"{DIM}(comma-separate for multiple, e.g. electron-main.js, preload.js "
        f"— or press Enter to abort){RST}: "
    ).strip()

    if not raw_input_str:
        warn("No file path given — aborting.")
        return None

    file_paths = [p.strip().replace("\\", "/") for p in raw_input_str.split(",") if p.strip()]

    if not file_paths:
        warn("No valid file path given — aborting.")
        return None

    if len(file_paths) == 1:
        ok(f"Target: {file_paths[0]}")
    else:
        ok(f"Targets ({len(file_paths)}): {', '.join(file_paths)}")
    return file_paths


def phase3_inspect(file_path: str) -> tuple[str, bool]:
    """Inspect a single file. For multi-file runs this is called once per
    file by phase3_inspect_all; the header/logic per file is unchanged."""
    hdr("Phase 3", f"Inspect — patchit -l  +  jsx-check  [{file_path}]")

    if not os.path.isfile(file_path):
        err(f"File not found: {file_path}")
        return "", False

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


def phase3_inspect_all(file_paths: list[str]) -> dict[str, tuple[str, bool]]:
    """Run phase3_inspect for every target file. A missing file is reported
    and skipped rather than aborting the whole multi-file run — the other
    files still get inspected."""
    results = {}
    for fp in file_paths:
        listing, jsx_ok = phase3_inspect(fp)
        if not listing and not os.path.isfile(fp):
            warn(f"Skipping {fp} — not found.")
            continue
        results[fp] = (listing, jsx_ok)
    return results


def phase4_identify_all(inspected: dict[str, tuple[str, bool]], directive: str = "") -> dict[str, tuple[str, str]]:
    """Run phase4_identify once per file. A file where the user gives no
    find/replace is simply left out of the returned dict — the run
    continues with whatever files DO have a target."""
    targets = {}
    for fp, (listing, _jsx_ok) in inspected.items():
        hdr("Phase 4", f"Identify — find exact string to patch  [{fp}]")
        find_str, replace_str = _phase4_identify_core(listing, directive)
        if find_str and replace_str:
            targets[fp] = (find_str, replace_str)
        else:
            warn(f"No patch target given for {fp} — skipping it.")
    return targets


def phase4_identify(listing: str) -> tuple[str | None, str | None]:
    hdr("Phase 4", "Identify — find exact className to patch")
    return _phase4_identify_core(listing)


def _phase4_identify_core(listing: str, directive: str = "") -> tuple[str | None, str | None]:
    """Try local LLM first to extract find/replace, fall back to manual input."""

    # Try local LLM if directive is provided
    if directive:
        print(f"\n  {DIM}[local LLM] Attempting to extract find/replace blocks...{RST}")
        find_str, replace_str = maybe_extract_find_replace(directive, listing)
        if find_str and replace_str:
            ok(f"Local LLM extracted find/replace:")
            print(f"  {YELL}Find:    {find_str[:100]}{'...' if len(find_str) > 100 else ''}{RST}")
            print(f"  {GREEN}Replace: {replace_str[:100]}{'...' if len(replace_str) > 100 else ''}{RST}")
            confirm = input(f"\n  {BOLD}Use this LLM suggestion? (yes/no){RST}: ").strip().lower()
            if confirm in ("yes", "y"):
                return find_str, replace_str
            warn("LLM suggestion declined — falling back to manual input.")

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


def phase5_patch(
    file_path: str,
    find_str: str,
    replace_str: str,
    auto_fix: bool = False,
    test_cmd: list[str] | None = None,
    max_attempts: int = 3,
    directive: str = "",
) -> bool:
    hdr("Phase 5", "Patch — diff preview → confirm → apply")

    print(f"\n  {BOLD}Planned change:{RST}")
    print(f"  {RED}  - {find_str}{RST}")
    print(f"  {GREEN}  + {replace_str}{RST}")
    print(f"  {DIM}  in {file_path}{RST}")

    if auto_fix:
        if not test_cmd:
            warn("--auto-fix was requested but no --test-cmd was given.")
            warn("Without a test command there's nothing to verify against — "
                 "falling back to the normal single-shot patch flow.")
        else:
            confirm_loop = input(
                f"\n{BOLD}  ⏸  Run auto-fix loop (up to {max_attempts} attempts, "
                f"test: {' '.join(test_cmd)})? (yes/no): {RST}"
            ).strip().lower()
            if confirm_loop not in ("yes", "y"):
                warn("Auto-fix loop declined — aborting patch.")
                return False

            result = run_auto_fix_loop(
                filepath=file_path,
                find_str=find_str,
                replace_str=replace_str,
                test_cmd=test_cmd,
                max_attempts=max_attempts,
            )
            print_summary(result)

            if not result.success:
                err("Auto-fix loop did not produce a passing, confirmed patch.")
                return False

            ok("Patch applied and verified — creating safety checkpoint...")
            commit_msg = f"resolving: {directive}" if directive else None
            git_checkpoint(commit_msg)
            return True

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
    commit_msg = f"resolving: {directive}" if directive else None
    git_checkpoint(commit_msg)
    return True


def phase5_patch_all(
    targets: dict[str, tuple[str, str]],
    auto_fix: bool = False,
    test_cmd: list[str] | None = None,
    max_attempts: int = 3,
    directive: str = "",
) -> list[str]:
    """Patch multiple files. For the normal (non-auto-fix) path this shows
    ALL planned changes together and asks ONE confirmation, matching the
    single-file flow's one-gate design instead of prompting per file.
    --auto-fix still runs its test-and-retry loop per file (it needs a
    pass/fail signal per patch), but each file's loop still ends behind
    its own pass/fail — there's no way to safely collapse that into one
    gate without losing the per-file verification auto-fix exists for.
    Returns the list of file paths that were actually patched."""
    hdr("Phase 5", f"Patch — {len(targets)} file(s) — diff preview → confirm → apply")

    for fp, (find_str, replace_str) in targets.items():
        print(f"\n  {BOLD}{fp}{RST}")
        print(f"  {RED}  - {find_str}{RST}")
        print(f"  {GREEN}  + {replace_str}{RST}")

    if auto_fix and test_cmd:
        warn("--auto-fix with multiple files: each file gets its own test-and-retry "
             "loop and its own pass/fail confirm, since auto-fix needs a per-patch "
             "verification signal — this can't be safely collapsed into one gate.")
        patched = []
        for fp, (find_str, replace_str) in targets.items():
            if phase5_patch(fp, find_str, replace_str,
                             auto_fix=True, test_cmd=test_cmd, max_attempts=max_attempts, directive=directive):
                patched.append(fp)
        return patched

    confirm = input(
        f"\n{BOLD}  ⏸  Apply all {len(targets)} planned change(s) above? (yes/no): {RST}"
    ).strip().lower()
    if confirm not in ("yes", "y"):
        warn("Patch aborted by user — no files changed.")
        return []

    patched = []
    for fp, (find_str, replace_str) in targets.items():
        original_stdin = sys.stdin
        feed = f"{find_str}\nEND\n{replace_str}\nEND\ny\n"
        sys.stdin = __import__("io").StringIO(feed)
        try:
            orig_argv = sys.argv
            sys.argv = ["patchit", fp, "-f"]
            mode_find_replace(fp)
            sys.argv = orig_argv
        finally:
            sys.stdin = original_stdin
        ok(f"Patched: {fp}")
        patched.append(fp)

    if patched:
        ok(f"{len(patched)} file(s) patched — creating safety checkpoint...")
        commit_msg = f"resolving: {directive}" if directive else None
        git_checkpoint(commit_msg)

    return patched


def phase6_verify():
    hdr("Phase 6", "Verify — confirm checkpoint in git log")
    git_log()
    ok("Pipeline complete.")


def _parse_recon_argv(argv: list[str]) -> tuple[str, bool, list[str] | None, int]:
    """Pull --auto-fix / --test-cmd "..." / --max-attempts N out of argv,
    returning (directive, auto_fix, test_cmd, max_attempts). Anything not
    matching a flag is treated as part of the directive, preserving the
    existing "pyslick recon <free text>" usage."""
    auto_fix = False
    test_cmd: list[str] | None = None
    max_attempts = 3
    words = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--auto-fix":
            auto_fix = True
            i += 1
        elif arg == "--test-cmd":
            if i + 1 >= len(argv):
                err("--test-cmd requires a quoted command string, e.g. --test-cmd \"pytest -q\"")
                sys.exit(1)
            test_cmd = argv[i + 1].split()
            i += 2
        elif arg == "--max-attempts":
            if i + 1 >= len(argv):
                err("--max-attempts requires a number")
                sys.exit(1)
            try:
                max_attempts = int(argv[i + 1])
            except ValueError:
                err("--max-attempts must be an integer")
                sys.exit(1)
            i += 2
        else:
            words.append(arg)
            i += 1

    return " ".join(words), auto_fix, test_cmd, max_attempts


def main():
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════╗
║      PySlick Autonomous Recon-to-Patch           ║
╚══════════════════════════════════════════════════╝{RST}""")

    if len(sys.argv) > 1:
        directive, auto_fix, test_cmd, max_attempts = _parse_recon_argv(sys.argv[1:])
    else:
        directive = input(f"\n  {BOLD}Directive:{RST} ").strip()
        auto_fix, test_cmd, max_attempts = False, None, 3

    if not directive:
        err("No directive given.")
        sys.exit(1)

    print(f"\n  {DIM}Directive: {directive}{RST}")
    if auto_fix:
        print(f"  {DIM}Auto-fix loop: enabled  "
              f"(max {max_attempts} attempts"
              f"{', test: ' + ' '.join(test_cmd) if test_cmd else ', no test-cmd given'}){RST}")

    phase1_orient()

    file_paths = phase2_locate(directive)
    if not file_paths:
        sys.exit(1)

    inspected = phase3_inspect_all(file_paths)
    if not inspected:
        err("No files could be inspected — exiting.")
        sys.exit(1)

    jsx_issues = [fp for fp, (_listing, jsx_ok) in inspected.items() if not jsx_ok]
    if jsx_issues:
        warn(f"JSX issues present in: {', '.join(jsx_issues)}")
        go = input(f"  {YELL}Continue anyway? (yes/no):{RST} ").strip().lower()
        if go not in ("yes", "y"):
            err("Aborted.")
            sys.exit(1)

    targets = phase4_identify_all(inspected, directive)
    if not targets:
        err("No patch targets — exiting.")
        sys.exit(1)

    patched = phase5_patch_all(
        targets,
        auto_fix=auto_fix, test_cmd=test_cmd, max_attempts=max_attempts, directive=directive,
    )

    if patched:
        phase6_verify()
    else:
        warn("No patch written — skipping Phase 6.")


if __name__ == "__main__":
    main()
