#!/usr/bin/env python3
"""
pyslick — Local code analysis, search, and AI-assisted patching.

Install once from the project root:
    python setup_and_install.py

Then run from your project directory in PowerShell:

    pyslick agent  "make the mic button 40% larger"   # full AI agent session
    pyslick query  "what changes the mic button size"  # fast fuzzy lookup
    pyslick recon  "flashcard has too much padding"    # guided patch pipeline
    pyslick ls                                         # list source files
    pyslick lines  src/app/page.tsx                    # view with line numbers
    pyslick grep   src/app/page.tsx className --context 3
    pyslick copy                                       # copy last output to clipboard
    pyslick --help                                     # full command reference
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

# Clipboard capture — imported lazily so startup cost is zero
def _get_clipboard():
    from .clipboard import capture_and_offer, cmd_copy
    return capture_and_offer, cmd_copy


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

def import_recon_pack():
    from .recon_pack import main as recon_pack_main
    return recon_pack_main

def import_query():
    from query import main as query_main
    return query_main

def import_toolbox():
    from toolbox import mode_ls, mode_lines, mode_grep
    return mode_ls, mode_lines, mode_grep

def import_comment_blocks():
    from comment_blocks import (
        scan_project_for_comment_blocks,
        scan_file_for_comment_blocks,
        print_scan_report,
    )
    return scan_project_for_comment_blocks, scan_file_for_comment_blocks, print_scan_report


# ─────────────────────────────────────────────────────────────────────────
# Git operations
# ─────────────────────────────────────────────────────────────────────────
def git_checkpoint(commit_msg: str = None):
    """Create a git checkpoint before operations. If commit_msg is provided,
    uses it; otherwise uses a default timestamped message. Attempts git push
    if remote is configured."""
    try:
        result = subprocess.run(['git', 'rev-parse', '--git-dir'],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            print("Not in a git repository. Creating local backup instead.")
            return create_local_backup()

        if commit_msg is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            commit_msg = f"pyslick checkpoint before operation - {timestamp}"

        subprocess.run(['git', 'add', '.'], capture_output=True)
        result = subprocess.run(['git', 'commit', '-m', commit_msg],
                                 capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✓ Git checkpoint created: {commit_msg}")
            
            # Try to push if remote is configured
            push_result = subprocess.run(['git', 'push'], capture_output=True, text=True)
            if push_result.returncode == 0:
                print("✓ Changes pushed to remote")
            else:
                print("⚠ Could not push to remote (no remote configured or authentication issue)")
                print("  Changes are committed locally only")
            return True
        else:
            print("No changes to checkpoint (working directory clean)")
            return True

    except Exception as e:
        print(f"Error creating git checkpoint: {e}")
        return create_local_backup()

def create_local_backup(commit_msg: str = None):
    """Create a local backup if git is not available. Keeps only the newest 5 backups,
    deleting the oldest when a new one is created."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = f"pyslick_backup_{timestamp}"
        shutil.copytree('.', backup_dir, ignore=shutil.ignore_patterns(
            '__pycache__', '*.pyc', '.git', 'node_modules', '.next', 'dist', 'build'
        ))
        print(f"✓ Local backup created: {backup_dir}")
        
        # Prune old backups, keep only 5 newest
        _prune_local_backups(keep=5)
        
        return True
    except Exception as e:
        print(f"Error creating local backup: {e}")
        return False


def _prune_local_backups(keep: int = 5):
    """Keep only the newest `keep` local backups, deleting the oldest."""
    try:
        # Find all pyslick_backup_* directories
        backups = []
        for item in os.listdir('.'):
            if item.startswith('pyslick_backup_') and os.path.isdir(item):
                backups.append(item)
        
        if len(backups) <= keep:
            return
        
        # Sort by timestamp (embedded in directory name)
        backups.sort()
        
        # Delete the oldest ones
        to_delete = backups[:-keep]
        for old_backup in to_delete:
            try:
                shutil.rmtree(old_backup)
                print(f"  Removed old backup: {old_backup}")
            except Exception as e:
                print(f"  Warning: could not remove {old_backup}: {e}")
    except Exception as e:
        print(f"Warning: error pruning old backups: {e}")

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
PySlick — Local code analysis, search, and AI-assisted patching

Usage:
    pyslick <command> [args]

━━  AI Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  agent <directive>
      Full agentic AI session (Claude). Autonomously scans your project
      using a vocabulary of tool calls (get-file, scan-lines, grep,
      find-blocks, ast-query, jsx-check …), synthesises a patch proposal,
      shows a diff, then waits for ONE confirmation before writing.
      Example:  pyslick agent "make the mic button 40% larger"
      Example:  pyslick agent "remove the extra padding at the bottom of flashcards"

  query <directive>
      Fast, no-LLM smart lookup. Fuzzy graph match + AST call-graph walk
      returns only the relevant functions/classes — not the whole file.
      Read-only. Use this to orient before patching.
      Options:  --root, --top-files, --top-symbols, --depth, --full
      Example:  pyslick query "what changes the mic button size"

  recon <directive>
      Guided interactive pipeline: find → inspect → patch → checkpoint.
      Asks you to confirm the target file and the exact find/replace before
      anything is written. Add --auto-fix --test-cmd "pytest -q" to enable
      a Claude-assisted retry loop on test failures.
      Example:  pyslick recon "flashcard has too much space at the bottom"
      Example:  pyslick recon "resize mic" --auto-fix --test-cmd "pnpm build"

  recon-pack <directive>
      Local context packer (no API key). Uses local LLM for query expansion,
      fuzzy graph match + comment-block scan to find relevant files, then
      writes a curated JSON bundle of file content to paste into a web AI.
      Options: --max-files N, --max-lines N, --whole-file-max-lines N, --context-lines N
      Example:  pyslick recon-pack "connect bing extension websocket to electron main"
      Example:  pyslick recon-pack "resize mic button" --max-files 4 --max-lines 800

━━  Read / Search Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ls [root] [--full-path]
      List source files recursively, skipping node_modules / dist / .git.
      Default: filename only.  --full-path: relative path from root.

  lines <file>
      Print a file with 3-digit line numbers (great for pasting into LLMs).

  grep <file> <pattern> [<pattern> …] [--context N]
      Search a file for one or more patterns, N lines of context each.
      Default context: 1.  Patterns can be plain text or regex.

  comment-scan [path]
      Scan a file or whole project for named comment blocks
      (# pyslick:start / # pyslick:end markers) and descriptive comments
      that sit above code regions. Useful for CSS, HTML, config files.

━━  Code Analysis Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  graphify-query <file.py> <question>
      AST call-graph query on a Python file: returns matched
      functions/classes + callers/callees scored by relevance.
      Options: --top-k, --min-score, --depth, --direction

  graphify-sitter <query>
      Tree-sitter parser for TypeScript/TSX files.
      Requires: tree-sitter, tree-sitter-typescript

  find-nearest-nodes <query>
      Fuzzy search across a pre-built graphify graph (graphify-out/graph.json).
      Run "graphify extract ." first to generate the graph.

  find-stray-symbols <project_root>
      Scan for unmatched JSX tags and stray symbols in .tsx files.

  indentation <file>
      Analyse indentation scopes and brace matching.

  jsx-check <file>
      Validate JSX/HTML tag matching. Run before patching .tsx/.html files.

━━  Edit Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  patchit <file> [options]
      Manual file editor with git-style diff preview and safety backup.
        -l / --lines      show file with line numbers (read-only)
        -f / --find       interactive find-and-replace
        -r / --regex      regex find-and-replace
        -i / --insert     insert lines at a given line number

━━  Clipboard  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  copy [--show]
      Copy the last pyslick session output to the clipboard — paste
      straight into your LLM chat box.
      --show also prints the saved text to the terminal.
      Tip: after ANY command, press Ctrl+C within ~2 s to copy instantly.

━━  Git Operations  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  checkpoint   commit all changes as a named pyslick safety snapshot
  stash        git stash current changes
  rollback     git reset --hard HEAD (undo all uncommitted changes)
  status       git status
  log          last 10 commits (one-line)

━━  Local LLM  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  llm-status
      Check whether llama-cpp-python and a GGUF model are installed.
      The local LLM (~90 MB) expands vague directives into better search
      terms before fuzzy matching runs. Optional — everything works without it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quick start:
    pyslick ls                                   # see what files exist
    pyslick query "mic button size"              # find relevant code
    pyslick agent "make the mic button bigger"   # let AI propose the fix
    pyslick copy                                 # paste output into your LLM
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

    # ── pyslick copy [--show] — re-copy last session, no capture wrapper ──
    if command == "copy":
        _, cmd_copy = _get_clipboard()
        cmd_copy(show="--show" in args)
        return

    # ── All other commands run inside the clipboard capture context ────────
    capture_and_offer, _ = _get_clipboard()

    with capture_and_offer():
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

            elif command == "comment-scan":
                try:
                    scan_project, scan_file, print_report = import_comment_blocks()
                    target = args[0] if args else "."
                    if os.path.isfile(target):
                        nodes = scan_file(target)
                    else:
                        nodes = scan_project(target)
                    print_report(nodes)
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

            elif command == "recon-pack":
                try:
                    recon_pack_main = import_recon_pack()
                    sys.argv = ["recon-pack"] + args
                    recon_pack_main()
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

            elif command == "llm-status":
                try:
                    from .llm import status_report
                    print(status_report())
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "agent":
                if not args:
                    print('Error: agent requires a directive, e.g. pyslick agent "resize the mic button"')
                    sys.exit(1)
                try:
                    from .agent import main as agent_main
                    sys.argv = ["agent"] + args
                    agent_main()
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
