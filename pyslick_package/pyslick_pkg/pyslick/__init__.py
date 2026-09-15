#!/usr/bin/env python3
"""
pyslick — Local code analysis, search, and AI-assisted patching.

Install once from the project root:
    python setup_and_install.py

Then run from your project directory in PowerShell:

    pyslick "show me packagejson"                      # AI agent session (default)
    pyslick "make the mic button 40% larger"           # AI code inspection / patch
    pyslick query "what changes the mic button size"   # fast non-LLM fuzzy lookup
    pyslick recon "flashcard has too much padding"     # guided patch pipeline
    pyslick ls                                         # list source files
    pyslick lines src/app/page.tsx                     # view with line numbers
    pyslick grep src/app/page.tsx className --context 3
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

def import_watch():
    from watch import watch
    return watch

def import_comment_blocks():
    from comment_blocks import (
        scan_project_for_comment_blocks,
        scan_file_for_comment_blocks,
        print_scan_report,
    )
    return scan_project_for_comment_blocks, scan_file_for_comment_blocks, print_scan_report


import re

# ─────────────────────────────────────────────────────────────────────────
# Git operations
# ─────────────────────────────────────────────────────────────────────────
def generate_smart_commit_message(fallback_directive: str | None = None) -> str:
    """
    Generate an intelligent commit message strictly by parsing the comments
    and modified functions in `git diff`. No complex LLM reasoning required.
    """
    try:
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        status_lines = [l.strip() for l in status_res.stdout.splitlines() if l.strip()]

        changed_files = []
        for line in status_lines:
            parts = line.split()
            if len(parts) >= 2:
                fn = os.path.basename(parts[-1])
                if fn not in changed_files and not fn.endswith(".pyc"):
                    changed_files.append(fn)

        diff_res = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        diff_text = diff_res.stdout
        if not diff_text:
            diff_res = subprocess.run(["git", "diff", "--cached"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
            diff_text = diff_res.stdout

        file_comments = {}
        file_functions = {}
        current_file = None

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    current_file = os.path.basename(parts[-1])
                    if current_file.startswith("b/"):
                        current_file = current_file[2:]
            elif line.startswith("+") and not line.startswith("+++"):
                clean = line[1:].strip()
                if clean.startswith(("#", "//", "/*", "*")):
                    c_text = re.sub(r"^[#/*\s─\-=•]+", "", clean).strip()
                    c_text = re.sub(r"[\"']+$", "", c_text).strip()
                    if len(c_text) >= 10 and not c_text.startswith(("import", "http", "from ", "def ", "class ", "<", ">")):
                        if current_file:
                            file_comments.setdefault(current_file, []).append(c_text)
                m = re.search(r"(?:def|function|class|async\s+function)\s+([a-zA-Z0-9_$]+)", clean)
                if m:
                    fn_name = m.group(1)
                    if current_file and fn_name not in file_functions.setdefault(current_file, []) and not fn_name.startswith("__"):
                        file_functions[current_file].append(fn_name)

        # 1. Base commit message on added/modified comments in code
        msg_parts = []
        for f, cmts in file_comments.items():
            if cmts:
                cmt = cmts[0]
                cmt = cmt[0].upper() + cmt[1:] if len(cmt) > 1 else cmt
                if len(cmt) > 60:
                    cmt = cmt[:57] + "..."
                msg_parts.append(f"{f}: {cmt}")

        if msg_parts:
            return "Update " + "; ".join(msg_parts[:2])

        # 2. Base on modified function names
        fn_parts = []
        for f, fns in file_functions.items():
            if fns:
                fn_parts.append(f"{f} ({', '.join(fns[:2])})")
        if fn_parts:
            return f"Update {', '.join(fn_parts[:2])}"

        # 3. Base on modified files list
        if changed_files:
            file_str = ", ".join(changed_files[:3])
            if fallback_directive and not any(g in fallback_directive.lower() for g in ["push to github", "git push", "git commit", "checkpoint"]):
                return f"Update {file_str}: {fallback_directive.strip()}"
            return f"Update {file_str}"

        return f"pyslick checkpoint - {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    except Exception:
        return fallback_directive or f"pyslick checkpoint - {datetime.now().strftime('%Y%m%d_%H%M%S')}"


def git_checkpoint(commit_msg: str = None):
    """Create a git checkpoint before operations. Automatically generates a
    comment-driven descriptive commit message from git diff if not provided."""
    try:
        result = subprocess.run(['git', 'rev-parse', '--git-dir'],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            print("Not in a git repository. Creating local backup instead.")
            return create_local_backup()

        # If commit_msg is missing or generic, generate smart message from diff comments
        is_generic = commit_msg is None or any(commit_msg.strip().lower() == g for g in ["push to github", "git push", "git commit", "checkpoint", "push", "save"])
        if is_generic:
            commit_msg = generate_smart_commit_message(commit_msg)

        subprocess.run(['git', 'add', '.'], capture_output=True)
        result = subprocess.run(['git', 'commit', '-m', commit_msg],
                                 capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✓ Git checkpoint created: {commit_msg}")
            
            # Try to push if remote is configured
            try:
                push_result = subprocess.run(['git', 'push'], capture_output=True, text=True, timeout=5)
                if push_result.returncode == 0:
                    print("✓ Changes pushed to remote")
                else:
                    print("⚠ Could not push to remote (no remote configured or authentication issue)")
                    print("  Changes are committed locally only")
            except subprocess.TimeoutExpired:
                print("⚠ Git push timed out. Changes committed locally.")
            return True
        else:
            print("No changes to checkpoint (working directory clean)")
            return True

    except Exception as e:
        print(f"Error creating git checkpoint: {e}")
        return create_local_backup()

def create_local_backup(commit_msg: str = None):
    """Delta backup: store unified diffs of changed files in .pyslick/backups/.
    Falls back to full file copy only if no previous snapshot exists."""
    import difflib
    pyslick_dir = os.path.join(os.getcwd(), ".pyslick")
    backup_root = os.path.join(pyslick_dir, "backups")
    os.makedirs(backup_root, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_dir = os.path.join(backup_root, f"snap_{timestamp}")
    os.makedirs(snap_dir, exist_ok=True)

    skip = {"node_modules", ".git", "__pycache__", ".next", "dist", "build",
            ".pyslick", "pyslick_backup_"}
    changed = 0
    try:
        for dirpath, dirnames, filenames in os.walk("."):
            dirnames[:] = [d for d in dirnames if not any(d.startswith(s) for s in skip)]
            for fn in filenames:
                if fn.endswith((".pyc", ".bak")):
                    continue
                fpath = os.path.join(dirpath, fn)
                rel = os.path.relpath(fpath, ".")
                # Find newest previous snap for delta
                prev_snaps = sorted([
                    d for d in os.listdir(backup_root)
                    if d.startswith("snap_") and d < f"snap_{timestamp}"
                ], reverse=True)
                prev_content = None
                for ps in prev_snaps:
                    prev_file = os.path.join(backup_root, ps, rel + ".orig")
                    if os.path.exists(prev_file):
                        with open(prev_file, "r", encoding="utf-8", errors="replace") as pf:
                            prev_content = pf.readlines()
                        break
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as cf:
                        curr_content = cf.readlines()
                except Exception:
                    continue
                out_path = os.path.join(snap_dir, rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                if prev_content is None:
                    # First time — store full file
                    with open(out_path + ".orig", "w", encoding="utf-8") as of:
                        of.writelines(curr_content)
                    changed += 1
                else:
                    diff = list(difflib.unified_diff(prev_content, curr_content, fromfile=rel, tofile=rel))
                    if diff:
                        with open(out_path + ".diff", "w", encoding="utf-8") as df:
                            df.writelines(diff)
                        changed += 1
        if changed:
            print(f"✓ Delta backup saved: .pyslick/backups/snap_{timestamp} ({changed} file(s))")
        else:
            # Nothing changed — remove empty snap
            shutil.rmtree(snap_dir, ignore_errors=True)
            print("✓ No changes since last backup.")
        _prune_local_backups(keep=5)
        return True
    except Exception as e:
        print(f"Error creating backup: {e}")
        return False


def _prune_local_backups(keep: int = 5):
    """Keep only the newest `keep` snapshots in .pyslick/backups/, deleting the oldest."""
    backup_root = os.path.join(os.getcwd(), ".pyslick", "backups")
    if not os.path.isdir(backup_root):
        return
    try:
        snaps = sorted([
            d for d in os.listdir(backup_root)
            if d.startswith("snap_") and os.path.isdir(os.path.join(backup_root, d))
        ])
        if len(snaps) <= keep:
            return
        for old in snaps[:-keep]:
            try:
                shutil.rmtree(os.path.join(backup_root, old))
                print(f"  Removed old snapshot: {old}")
            except Exception as e:
                print(f"  Warning: could not remove {old}: {e}")
    except Exception as e:
        print(f"Warning: error pruning snapshots: {e}")

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

Tip: reference an exact file with a leading slash, e.g. "/server.js" or
"/relay/server.js" — this skips fuzzy file-guessing entirely and goes
straight to that file. Combine multiple: "compare /a.js and /b.js".

━━  AI Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  agent <directive>
      Full agentic AI session (Claude). Autonomously scans your project
      using a vocabulary of tool calls (get-file, scan-lines, grep,
      find-blocks, ast-query, jsx-check …), synthesises a patch proposal,
      shows a diff, then waits for ONE confirmation before writing.
      Example:  pyslick agent "make the mic button 40% larger"
      Example:  pyslick agent "remove the extra padding at the bottom of flashcards"

  <directive>            (no subcommand)
      Rule-based router. Broad Q&A goes to universal recon: ranked files,
      per-file snippets, and (if the directive names two entities) a
      RELATION block showing the graph path between them.
      Snippets auto-snap to the enclosing function's full extent â€” a
      mid-body grep hit still shows the whole function, never a truncated
      ±8-line window. Stopword-stripped, so "what does X do" doesn't
      match every docstring containing the word "do".
      Overview questions ("what does this codebase do", "app overview",
      "describe the project", â€¦) route to the App Overview panel:
      god-node files by in-degree + their opening comments.

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

  ask <question> [--auto]
      Recon loop via browser relay (Miamico extension). Sends your question
      to Claude in Edge, then Claude responds one pyslick command at a time.
      Each command is shown with a y/n prompt before running (skip with --auto).
      Results feed back to Claude automatically. Stops when Claude outputs
      "I have enough information." and prints its findings summary.
      Requires: Miamico extension installed + relay server running.
      Example:  pyslick ask "how does the mic button get its size"
      Example:  pyslick ask "what calls ensure_running" --auto

━━  Read / Search Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ls [root] [--full-path]
      List source files recursively, skipping node_modules / dist / .git.
      Default: filename only.  --full-path: relative path from root.

  lines <file>
    text-search "<query>"
        BM25 search over comments, strings, CSS classes, JSX attrs.
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

━━  Watch Mode  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  watch [--root .] [--auto-apply]
      Watch the project for a saved file containing a marker comment
      (// pyslick? <directive>  or  # pyslick? <directive>) and detect it
      automatically. Without --auto-apply, a detected marker just prints
      what to run next — it never invokes the agent on its own. With
      --auto-apply, it invokes the agent for you on save, which still
      shows a diff and asks before writing anything (same as running
      `pyslick agent` yourself — this flag does not skip that step).
      Requires: watchdog (pip install watchdog). Ctrl+C to stop.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quick start:
    pyslick "show me packagejson"                # inspect/scan files directly
    pyslick "make the mic button bigger"         # let AI propose the fix
    pyslick query "mic button size"              # fast non-LLM fuzzy lookup
    pyslick ls                                   # see what files exist
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

    # ── pyslick --logs — show logs from current session ──
    if command == "--logs":
        import logging
        log_file = os.path.join(os.getcwd(), ".pyslick", "pyslick.log")
        if os.path.exists(log_file):
            print(f"Showing logs from: {log_file}")
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                print(f.read())
        else:
            print(f"No log file found at: {log_file}")
            print("Logs are created when pyslick commands run.")
        return

    # ── pyslick copy [--show] — re-copy last session, no capture wrapper ──
    if command == "copy":
        _, cmd_copy = _get_clipboard()
        cmd_copy(show="--show" in args)
        return

    # ── All other commands run inside the clipboard capture context ────────
    capture_and_offer, _ = _get_clipboard()

    with capture_and_offer():
        # Only checkpoint before commands that can actually write files, and
        # skip it for patchit's read-only -l/--lines, --dict, --check modes too.
        is_readonly_patchit = command == "patchit" and any(a in ("-l", "--lines", "--dict", "-d", "--check") for a in args)
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
                    file_path = args[0]
                    start = None
                    end = None
                    rest = args[1:]
                    if "--head" in rest:
                        i = rest.index("--head")
                        if i + 1 < len(rest) and rest[i+1].isdigit():
                            start = 1
                            end = int(rest[i+1])
                    elif "--tail" in rest:
                        i = rest.index("--tail")
                        if i + 1 < len(rest) and rest[i+1].isdigit():
                            with open(file_path, "r", encoding="utf-8", errors="replace") as _f:
                                _total = len(_f.readlines())
                            start = max(1, _total - int(rest[i+1]) + 1)
                            end = _total
                    elif rest and not rest[0].startswith("--"):
                        import re as _re
                        m = _re.match(r"^(\d+)(?:[-:](\d+))?$", rest[0])
                        if m:
                            start = int(m.group(1))
                            end = int(m.group(2)) if m.group(2) else start
                    mode_lines(file_path, start=start, end=end)
                except Exception as e:
                    print(f"Error: {e}")

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

            elif command == "text-search":
                if not args:
                    print('Error: text-search requires a query, e.g. pyslick text-search "purple cursor"')
                    sys.exit(1)
                try:
                    from text_index import (
                        build_text_index, search_text_index_auto, format_auto_results,
                    )
                    _idx = build_text_index(".")
                    _res, _kw = search_text_index_auto(" ".join(args), _idx)
                    format_auto_results(_res, _kw)
                except Exception as e:
                    print(f"Error: {e}")
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

            elif command == "watch":
                try:
                    watch_fn = import_watch()
                    watch_root = "."
                    auto_apply = "--auto-apply" in args
                    if "--root" in args:
                        idx = args.index("--root")
                        if idx + 1 < len(args):
                            watch_root = args[idx + 1]
                    watch_fn(root=watch_root, auto_apply=auto_apply)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "agent":
                if not args:
                    print('Error: agent requires a directive, e.g. pyslick "resize the mic button"')
                    sys.exit(1)
                try:
                    from .agent import main as agent_main
                    sys.argv = ["agent"] + args
                    agent_main()
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)
            elif command == "runrepo":
                import subprocess
                import os as _os
                target = args[0] if args else _os.getcwd()
                bat = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "scripts", "runrepo.bat")
                if not _os.path.isfile(bat):
                    print(f"[runrepo] missing script: {bat}")
                    sys.exit(1)
                subprocess.call([bat, target], shell=True)

            elif command == "ask":
                if not args:
                    print('Error: ask requires a question, e.g. pyslick ask "how does the mic button get its size"')
                    print('       Add --auto to run commands without y/n prompts.')
                    sys.exit(1)
                try:
                    from .ask import run_ask
                    auto     = "--auto" in args
                    raw_args = [a for a in args if a != "--auto"]
                    run_ask(" ".join(raw_args), auto=auto)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            else:
                # Default: bare pyslick "<query>" (e.g. pyslick "show me packagejson")
                # is routed directly to the AI agent. Reassemble argv into a
                # single directive string so flags like --full / --grep X
                # survive as text instead of tripping agent's argparse.
                # Ask the local router what this is before sending to the agent.
                try:
                    from .router import route as _route
                    _intent = _route(_joined) if "_joined" in dir() else _route(" ".join([command] + args))
                except Exception:
                    _intent = None

                    import subprocess
                    import os as _os
                    bat = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "scripts", "runrepo.bat")
                    if _os.path.isfile(bat):
                        target = _os.environ.get("Pyslick_TARGET") or _os.getcwd()
                        subprocess.call([bat, target], shell=True)
                        return
                    print(f"[run_repo] script missing: {bat}")
      
                directive_args = [command] + args
                _joined = " ".join(directive_args)
                try:
                    from .agent import main as agent_main
                    sys.argv = ["agent", _joined]
                    agent_main()
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    main()
