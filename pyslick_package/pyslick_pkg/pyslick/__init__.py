#!/usr/bin/env python3
"""
pyslick — Super Python program consolidating all PySlick tools.

Installed as a console script, so once you've run `pip install -e .`
(or `pipx install .`) from the project root, just type in PowerShell:

    pyslick query "what do I need to change to make the mic button bigger"
    pyslick recon "make the microphone larger"
    pyslick patchit myfile.py -f
    pyslick --help
"""

import sys
import os
import subprocess
import io
import shutil
from datetime import datetime

# Set UTF-8 encoding for stdout/stderr (Windows PowerShell safe)
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

PYSLICK_DIR = os.path.dirname(os.path.abspath(__file__))
if PYSLICK_DIR not in sys.path:
    sys.path.insert(0, PYSLICK_DIR)


# ─────────────────────────────────────────────────────────────────────────
# Dynamic imports (avoid paying import cost for tools you don't use)
# ─────────────────────────────────────────────────────────────────────────
def import_find_nearest_nodes():
    from find_nearest_nodes import main as find_nearest_nodes_main
    return find_nearest_nodes_main

def import_find_stray_symbols():
    from find_stray_symbols import main as find_stray_symbols_main
    return find_stray_symbols_main

def import_graphify_query():
    from graphify import query as graphify_query, query_json, map_symbols
    return graphify_query, query_json, map_symbols

def import_graphify_sitter():
    from graphify_sitter import main as graphify_sitter_main
    return graphify_sitter_main

def import_indentation():
    from indentation import analyze_indentation_scopes
    return analyze_indentation_scopes

def import_jsx_check():
    from jsx_tag_checker import check_jsx_tags
    return check_jsx_tags

def import_patchit():
    from patchit import main as patchit_main
    return patchit_main

def import_recon():
    # recon.py uses a package-relative import for git_log/git_checkpoint,
    # so it must be imported as a submodule of this package, not top-level.
    from .recon import main as recon_main
    return recon_main

def import_query():
    from query import main as query_main
    return query_main

def import_toolbox():
    from toolbox import mode_ls, mode_lines, mode_grep
    return mode_ls, mode_lines, mode_grep


# ─────────────────────────────────────────────────────────────────────────
# Git operations
# ─────────────────────────────────────────────────────────────────────────
def git_checkpoint():
    """Create a git checkpoint before operations"""
    try:
        result = subprocess.run(['git', 'rev-parse', '--git-dir'],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            print("Not in a git repository. Creating local backup instead.")
            return create_local_backup()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        commit_msg = f"pyslick checkpoint before operation - {timestamp}"

        subprocess.run(['git', 'add', '.'], capture_output=True)
        result = subprocess.run(['git', 'commit', '-m', commit_msg],
                                 capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✓ Git checkpoint created: {commit_msg}")
            return True
        else:
            print("No changes to checkpoint (working directory clean)")
            return True

    except Exception as e:
        print(f"Error creating git checkpoint: {e}")
        return create_local_backup()

def create_local_backup():
    """Create a local backup if git is not available"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"pyslick_backup_{timestamp}"
        shutil.copytree('.', backup_dir, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '.git', 'node_modules', '.next', 'dist', 'build'
        ))
        print(f"✓ Local backup created: {backup_dir}")
        return True
    except Exception as e:
        print(f"Error creating local backup: {e}")
        return False

def git_stash():
    try:
        result = subprocess.run(['git', 'stash'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Changes stashed")
            return True
        print("No changes to stash")
        return True
    except Exception as e:
        print(f"Error stashing: {e}")
        return False

def git_rollback():
    try:
        result = subprocess.run(['git', 'rev-parse', '--git-dir'],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            print("Not in a git repository. Cannot rollback.")
            return False

        result = subprocess.run(['git', 'reset', '--hard', 'HEAD'],
                                 capture_output=True, text=True)
        if result.returncode == 0:
            print("✓ Rolled back to last commit")
            return True
        print("Error rolling back")
        return False
    except Exception as e:
        print(f"Error rolling back: {e}")
        return False

def git_status():
    try:
        result = subprocess.run(['git', 'status'], capture_output=True, text=True)
        print(result.stdout)
        return True
    except Exception as e:
        print(f"Error getting status: {e}")
        return False

def git_log():
    try:
        result = subprocess.run(['git', 'log', '--oneline', '-10'],
                                 capture_output=True, text=True)
        print(result.stdout)
        return True
    except Exception as e:
        print(f"Error getting log: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Help
# ─────────────────────────────────────────────────────────────────────────
def print_help():
    help_text = """
PySlick — Super Python Program for Code Analysis and Editing (no LLM required)

Usage:
    pyslick <command> [args]

Smart Agent Command:
    query <directive>
        The "just ask it" command. No LLM, no tokens — fuzzy graph match
        + AST call-graph walk finds only the relevant end-to-end
        functions instead of scanning the whole codebase.
        Options: --root, --top-files, --top-symbols, --depth, --full
        Example: pyslick query "what changes the mic button size"

Guided Agent Command:
    recon <directive>
        Full interactive find -> inspect -> patch -> checkpoint pipeline
        with one human confirmation gate before any file is written.

Read-Only Utility Commands (PowerShell one-liners, built in):
    ls [root] [--full-path]
        List files recursively, name only by default
        (== Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name)

    lines <file>
        Print a file with 3-digit numbered lines
        (== Get-Content | ForEach-Object numbered)

    grep <file> <pattern> [<pattern> ...] [--context N]
        Search a file for patterns with N lines of context (default 1)
        (== Select-String -Path -Pattern -Context N,N)

Code Analysis Commands:
    find-nearest-nodes <query>
        Fuzzy search for nodes in graphify output
        Requires: graphify output in graphify-out/graph.json, rapidfuzz

    find-stray-symbols <project_root>
        Scan directory for stray JSX symbols and unmatched tags (.tsx)

    graphify-query <file_path> <question>
        Query a Python file by description, get matched code + call chain
        Options: --top-k, --min-score, --depth, --direction

    graphify-sitter <user_query>
        Tree-sitter based parser for TypeScript/TSX files
        Requires: tree-sitter, tree-sitter-typescript

    indentation <file_path>
        Analyze indentation scopes and brace matching

    jsx-check <file_path>
        Check JSX/HTML tag matching in files

    patchit <file> [options]
        Local file editor with git-style diff
        Options: -l (show lines), -f (find/replace), -r (regex), -i (insert)

Git Operations:
    checkpoint / stash / rollback / status / log

Examples:
    pyslick query "make the microphone larger"
    pyslick recon "flashcard has too much space at the bottom"
    pyslick find-nearest-nodes "parse data"
    pyslick patchit myfile.py -f
"""
    print(help_text)


# ─────────────────────────────────────────────────────────────────────────
# Main dispatch
# ─────────────────────────────────────────────────────────────────────────
# Whitelist, not blacklist: only commands that can actually WRITE files
# trigger a checkpoint. This also means an unrecognized/typo'd command
# (e.g. a bare "graphify") never triggers a checkpoint before failing.
WRITE_COMMANDS = {"patchit", "recon"}


def main():
    """Main entry point for pyslick super program."""
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    # Only checkpoint before commands that can actually write files, and
    # skip it for patchit's read-only -l/--lines mode too.
    is_readonly_patchit = command == "patchit" and any(a in ("-l", "--lines") for a in args)
    auto_checkpoint = command in WRITE_COMMANDS and not is_readonly_patchit
    if auto_checkpoint:
        print("Creating safety checkpoint...")
        git_checkpoint()

    try:
        if command in ("--help", "-h"):
            print_help()
        elif command == "checkpoint":
            git_checkpoint()
        elif command == "stash":
            git_stash()
        elif command == "rollback":
            git_rollback()
        elif command == "status":
            git_status()
        elif command == "log":
            git_log()

        elif command == "ls":
            try:
                mode_ls, _, _ = import_toolbox()
                root = args[0] if args and not args[0].startswith("--") else "."
                mode_ls(root, names_only="--full-path" not in args)
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "lines":
            if not args:
                print("Error: lines requires <file_path>")
                sys.exit(1)
            try:
                _, mode_lines, _ = import_toolbox()
                mode_lines(args[0])
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "grep":
            if len(args) < 2:
                print("Error: grep requires <file_path> <pattern> [<pattern> ...] [--context N]")
                sys.exit(1)
            try:
                _, _, mode_grep = import_toolbox()
                file_path = args[0]
                context = 1
                patterns = []
                i = 1
                while i < len(args):
                    if args[i] == "--context" and i + 1 < len(args):
                        context = int(args[i + 1])
                        i += 2
                    else:
                        patterns.append(args[i])
                        i += 1
                mode_grep(file_path, patterns, context=context)
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "graphify":
            print("'graphify' isn't a pyslick subcommand by itself — did you mean one of:")
            print("  pyslick query \"<directive>\"           (fast, no-LLM smart lookup)")
            print("  pyslick graphify-query <file> <question>  (raw AST call-graph query)")
            print("  pyslick graphify-sitter \"<query>\"      (TS/TSX lookup, needs tree-sitter)")
            print("(If you're thinking of a separate 'graphify extract .' CLI tool for building")
            print(" graphify-out/graph.json, that's a different tool, not part of this package.)")

        elif command == "query":
            if not args:
                print('Error: query requires a directive, e.g. pyslick query "resize the mic button"')
                sys.exit(1)
            try:
                query_main = import_query()
                sys.argv = ["query"] + args
                query_main()
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "find-nearest-nodes":
            if not args:
                print("Error: find-nearest-nodes requires <query>")
                sys.exit(1)
            try:
                find_nearest_nodes_main = import_find_nearest_nodes()
                sys.argv = ["find_nearest_nodes"] + args
                find_nearest_nodes_main()
            except Exception as e:
                print(f"Error: {e}")
                print("Note: This command requires graphify output in graphify-out/graph.json")
                print("Run 'graphify extract .' first to generate the graph output.")
                sys.exit(1)

        elif command == "find-stray-symbols":
            if len(args) < 1:
                print("Error: find-stray-symbols requires <project_root>")
                sys.exit(1)
            try:
                find_stray_symbols_main = import_find_stray_symbols()
                find_stray_symbols_main(args[0])
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "graphify-query":
            if len(args) < 2:
                print("Error: graphify-query requires <file_path> <question>")
                sys.exit(1)
            try:
                graphify_query = import_graphify_query()[0]
                results = graphify_query(args[0], " ".join(args[1:]))
                for r in results:
                    print(r)
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "graphify-sitter":
            if not args:
                print("Error: graphify-sitter requires <user_query>")
                sys.exit(1)
            try:
                graphify_sitter_main = import_graphify_sitter()
                graphify_sitter_main(" ".join(args))
            except Exception as e:
                print(f"Error: {e}")
                print("Note: This command requires tree-sitter and tree-sitter-typescript")
                sys.exit(1)

        elif command == "indentation":
            if len(args) < 1:
                print("Error: indentation requires <file_path>")
                sys.exit(1)
            try:
                analyze_indentation_scopes = import_indentation()
                results = analyze_indentation_scopes(args[0])
                if not results:
                    print("OK: No scope or brace mismatches detected!")
                else:
                    for e in results:
                        print(e)
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "jsx-check":
            if len(args) < 1:
                print("Error: jsx-check requires <file_path>")
                sys.exit(1)
            try:
                check_jsx_tags = import_jsx_check()
                check_jsx_tags(args[0])
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "recon":
            try:
                recon_main = import_recon()
                sys.argv = ["recon"] + args
                recon_main()
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "patchit":
            try:
                patchit_main = import_patchit()
                sys.argv = ["patchit"] + args
                patchit_main()
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        else:
            print(f"Unknown command: {command}")
            print_help()
            sys.exit(1)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
