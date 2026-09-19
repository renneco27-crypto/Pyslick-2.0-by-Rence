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
    from toolbox import mode_ls, mode_lines, mode_grep, mode_semantic_grep
    return mode_ls, mode_lines, mode_grep, mode_semantic_grep

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
╔══════════════════════════════════════════════════════════════════════════════╗
║               PySlick 2.0 — Architecture & Developer Guide                   ║
║  Local Semantic Code Navigation, Call-Graph Comprehension & Context Packing  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Usage:
    pyslick <command> [args]
    pyslick "<natural language query>"

Tip: reference an exact file with a leading slash, e.g. "/server.js" or
"/relay/server.js" — this skips fuzzy guessing and inspects that exact file.

━━  Core AI & Recon Workflows  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  recon-pack "<directive>" [--max-files N] [--max-lines N]
      Zero-API local context packer for DeepSeek, Claude Web & ChatGPT.
      Decomposes compound queries, uses BM25 semantic text index + AST graph
      to find relevant files, snaps matches to complete enclosing functions,
      and writes a curated JSON bundle to .pyslick_context/<timestamp>.json
      and auto-copies to Windows clipboard.
      When asked "what does this codebase do", generates an Architecture Overview
      with PageRank top connected files and opening docstrings.
      Example:  pyslick recon-pack "what does this codebase do"
      Example:  pyslick recon-pack "where is supabase client and how is cache handled"

  recon "<directive>" [--auto-fix] [--test-cmd "<cmd>"] [--max-attempts N]
      Guided interactive inspection & patch pipeline:
      Phase 1: Orient (git log)
      Phase 2: Multi-language AST inspection & symbol caller/callee resolution
      Phase 3: Diff preview & one human confirmation gate
      Phase 4: Patch application & safety checkpoint
      Example:  pyslick recon "make the microphone button larger"
      Example:  pyslick recon "fix padding" --auto-fix --test-cmd "pnpm build"

  agent "<directive>"
      Full autonomous tool-calling session (Anthropic/Claude API).
      Plans tool calls, inspects files, synthesizes patch proposal, shows diff,
      and waits for one human confirmation before writing.

  ask "<question>" [--auto]
      Interactive browser-relay loop via Miamico Edge extension (Claude).
      Executes commands turn-by-turn with live feedback until findings complete.

━━  Search, Grep & Overview Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  grep "<natural language query>"
      Universal 2-stage hybrid semantic search:
      1. BM25 text index over boosted comments (2.5x), docstrings, and identifiers
      2. Tree-Sitter AST scope snapping (matches expand to full enclosing functions)
      3. Automatically handles compound questions via query decomposition
      4. Automatically resolves relationship queries ("how does X connect to Y")
      5. Automatically renders Architecture Overview for "what does this codebase do"
      Example:  pyslick grep "where is supabase client and how is cache registered"
      Example:  pyslick grep "what does this codebase do"
      Example:  pyslick grep "how does useOfflineCache connect to sw"

  grep <file> <pattern> [<pattern> …] [--context N]
      Targeted in-file pattern search with function enclosure expansion.
      Example:  pyslick grep src/sw.ts CACHE_URLS --context 3

  deps [target] / imports [target]
      Scan all project manifests (package.json, pyproject.toml, etc.) and
      source code to list declared dependencies and all file import locations.
      Example:  pyslick deps
      Example:  pyslick deps supabase
      Example:  pyslick imports serwist

  query "<directive>"
      Fast non-LLM AST symbol, caller/callee, and multi-language dependency lookup.
      Example:  pyslick query "createBrowserSupabase"

  text-search "<query>"
      Direct BM25 search over comments, docstrings, strings, and JSX attributes.

  comment-scan [path]
      Scan project or file for named comment blocks (# pyslick:start/end)
      and descriptive header comments that describe architectural regions.

━━  Read & Navigate Commands  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ls [root] [--full-path]
      Clean grouped directory listing (skipping dependencies, cache & build artifacts).

  lines <file> [range | --head N | --tail N]
      View file with line numbers (e.g. `pyslick lines src/app/page.tsx 10-50`).

━━  Code Analysis & Linting  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  find-nearest-nodes "<query>"
      Fuzzy search across pre-built graphify symbol graph (graphify-out/graph.json).

  find-stray-symbols <file_or_dir>
      Scan a single .tsx/.jsx file or entire directory for unclosed/mismatched JSX tags.

  indentation <file>
      Validate scope alignment and brace balance.

  jsx-check <file>
      Validate JSX/HTML tag hierarchy and matching.

  graphify-query <file.py> <question>
      AST call-graph query on Python files (callers, callees, depth).

━━  Edit, Git & Clipboard  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  patchit <file> [-l | -f | -r | -i]
      Manual file editor with diff preview and automatic safety backups.

  copy [--show]
      Copy the last pyslick output to clipboard (or press Ctrl+C within 2s of any command).

  checkpoint [message]
      Create a comment-driven smart git commit snapshot.

  status / log / stash / rollback
      Standard git status, last 10 commits, stash, or hard rollback.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Quick Start:
    pyslick recon-pack "what does this codebase do"
    pyslick grep "where is supabase client and how is cache registered"
    pyslick deps supabase
    pyslick copy
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
    # Auto-install goto helper once per machine.
    _marker_file = os.path.join(os.path.expanduser("~"), ".pyslick", ".profile_installed")
    if not os.path.exists(_marker_file):
        try:
            from install_profile import install as _install_profile
            _install_profile()
            os.makedirs(os.path.dirname(_marker_file), exist_ok=True)
            open(_marker_file, "w").close()
        except Exception:
            pass
    command = sys.argv[1]
    # ── Folder navigation: pyslick "go to X" ──
    args = sys.argv[2:]
    try:
        from go_nav import run_cd
        _full = command if not args else f"{command} {' '.join(args)}"
        if run_cd(_full):
            sys.exit(0)
    except Exception as _e:
        print(f"[pyslick] go_nav error: {_e}")


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
                    mode_ls, _, _, _ = import_toolbox()
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
                    _, mode_lines, _, _ = import_toolbox()
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
                if not args:
                    print("Error: grep requires <file_path> <pattern> [<pattern> ...] [--context N] or \"<natural language query>\"")
                    sys.exit(1)
                try:
                    _, _, mode_grep, mode_semantic_grep = import_toolbox()
                    context = 1
                    cleaned_args = []
                    i = 0
                    while i < len(args):
                        if args[i] == "--context" and i + 1 < len(args):
                            context = int(args[i + 1])
                            i += 2
                        else:
                            cleaned_args.append(args[i])
                            i += 1

                    if not cleaned_args:
                        print("Error: grep requires a pattern or search query.")
                        sys.exit(1)

                    # Detect if a directory path was passed in args (e.g. pyslick grep "query" "src" or pyslick grep "src" "query")
                    target_dir = "."
                    query_tokens = []
                    for a in cleaned_args:
                        if os.path.isdir(a):
                            target_dir = a
                        else:
                            query_tokens.append(a)

                    if len(cleaned_args) >= 2 and os.path.isfile(cleaned_args[0]):
                        file_path = cleaned_args[0]
                        patterns = cleaned_args[1:]
                        mode_grep(file_path, patterns, context=context)
                    elif len(cleaned_args) == 1 and os.path.isfile(cleaned_args[0]):
                        _, mode_lines, _, _ = import_toolbox()
                        mode_lines(cleaned_args[0])
                    else:
                        # Semantic grep across repository or target dir using BM25 and AST expansion
                        query_str = " ".join(query_tokens) if query_tokens else " ".join(cleaned_args)
                        mode_semantic_grep(query_str, root=target_dir, context=context)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command in ("deps", "imports", "dependencies"):
                try:
                    from repomap import scan_imports_and_dependencies
                    target_pkg = args[0] if args and not args[0].startswith("-") else None
                    root_dir = "."
                    if "--root" in args:
                        ri = args.index("--root")
                        if ri + 1 < len(args):
                            root_dir = args[ri + 1]
                    res = scan_imports_and_dependencies(root=root_dir, target=target_pkg)
                    BOLD  = "\033[1m"
                    CYAN  = "\033[96m"
                    GREEN = "\033[92m"
                    DIM   = "\033[2m"
                    RST   = "\033[0m"
                    print(f"\n{BOLD}{CYAN}━━  Dependency & Import Scanner  {RST}")
                    if target_pkg:
                        print(f"{DIM}Target: '{target_pkg}' | Files scanned: {res['files_scanned']}{RST}")
                    else:
                        print(f"{DIM}Files scanned: {res['files_scanned']}{RST}")
                    print(f"{DIM}{'─' * 60}{RST}")

                    if res.get("manifest_dependencies"):
                        print(f"\n{BOLD}Manifest Declared Packages:{RST}")
                        for dep, info in sorted(res["manifest_dependencies"].items()):
                            if not target_pkg or target_pkg.lower() in dep.lower():
                                print(f"  {GREEN}· {dep}{RST} {DIM}({info.get('version', '*')}) [{info.get('manifest', '')}]{RST}")

                    if res.get("imports_by_package"):
                        print(f"\n{BOLD}Imported Packages & Codebase Usages:{RST}")
                        for pkg, usages in sorted(res["imports_by_package"].items()):
                            print(f"\n  {BOLD}{CYAN}Package: {pkg}{RST} {DIM}({len(usages)} reference(s)){RST}")
                            for u in usages[:8]:
                                print(f"    {GREEN}→{RST} {u['file']}:{u['line']}  {DIM}{u['statement']}{RST}")
                            if len(usages) > 8:
                                print(f"    {DIM}... [{len(usages) - 8} more references omitted]{RST}")
                    else:
                        print(f"\n{DIM}No imports found matching criteria.{RST}")
                    print()
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "graphify":
                print("'graphify' isn't a pyslick subcommand by itself — did you mean one of:")
                print("  pyslick query \"<directive>\"           (fast, no-LLM smart lookup)")
                print("  pyslick graphify-query <file> <question>  (raw AST call-graph query)")
                print("  pyslick graphify-sitter \"<query>\"      (TS/TSX lookup, needs tree-sitter)")
                print("  pyslick deps                          (scan dependencies & imports)")
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
