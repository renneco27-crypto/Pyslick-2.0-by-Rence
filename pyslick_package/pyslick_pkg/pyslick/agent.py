#!/usr/bin/env python3
"""
agent.py â€” PySlick Agentic AI Session
=======================================

    pyslick agent "make the mic button 40% larger"
    pyslick agent "the flashcard bottom has too much padding"
    pyslick agent "rename the handleClick function to onMicPress"

Unlike `recon` (guided, human-driven) or `query` (read-only lookup),
the agent is given a VOCABULARY of tool calls and uses Claude to:

  1. Plan which tools to call and in what order
  2. Call them autonomously (all read-only, all local)
  3. Synthesise what it found into a concrete patch proposal
  4. Show you the diff and ask ONE confirmation question before writing

The agent never writes, commits, or modifies anything without your
explicit "yes". One human gate. Everything before that is autonomous.

â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
TOOL VOCABULARY  (PowerShell-equivalent, all local, all read-only)
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  get_file(path)
      Read a file's full content.
      PowerShell: Get-Content -Path <path> -Raw

  get_file_lines(path)
      Read with 3-digit line numbers prefixed.
      PowerShell: Get-Content | ForEach-Object numbered

  scan_names(root?)
      List every source file name recursively (skips node_modules etc).
      PowerShell: Get-ChildItem -Recurse -File | Select Name

  scan_lines(path, keyword)
      Find every line in a file that contains keyword (case-insensitive).
      Returns line numbers + text.
      PowerShell: Select-String -Path <path> -Pattern <keyword>

  grep(path, *patterns, context=2)
      Search file for one or more patterns, N lines of context each.
      PowerShell: Select-String -Path -Pattern -Context N,N

  find_blocks(path)
      Scan for named comment blocks (pyslick:start/end markers) and
      descriptive comments that sit above a code block. Returns each
      block's label, start/end lines, and a code preview â€” the AI uses
      these to locate CSS groups, JSX sections, config regions, etc.

  ast_query(path, question)
      Run graphify's AST call-graph query on a .py file: returns matched
      functions/classes + their callers/callees, scored by relevance.
      Equivalent to `pyslick graphify-query <path> <question>`

  jsx_check(path)
      Validate JSX/HTML tag matching in a file.
      Returns a list of any mismatched tags, or "OK".

  llm_expand(directive)
      If the local LLM is available, expand the directive into extra
      search terms for richer fuzzy matching. Falls back to the directive
      unchanged if no model is present.

â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
FLOW
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  1. Send the directive + tool schema to Claude (Anthropic API)
  2. Claude returns a PLAN (which tools to call, why)
  3. Execute each tool call locally (no data leaves your machine)
  4. Feed results back to Claude for synthesis
  5. Claude proposes: file, find_str, replace_str, explanation
  6. Show diff â†’ ONE confirmation â†’ patchit writes it
  7. git checkpoint

The tool calls in steps 2-4 loop up to MAX_TOOL_ROUNDS times so the
agent can react to what it finds (e.g. "that file had no className,
let me check the CSS file instead").

â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
LOCAL LLM MODE (no API key)
â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

When no ANTHROPIC_API_KEY / NVIDIA_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY
is set, the agent falls back
to _run_local_agent(). That function uses intent_vocab.json to route the
query to the right handler â€” no reasoning required from the 124M model.

Intent routing order (first match wins):
  help         â†’ print pyslick command docs
  git          â†’ show git status / push
  list_files   â†’ walk directory, show files + their main symbols
  comments     â†’ show every comment in the matched file
  nearest      â†’ find_nearest_nodes around a line or symbol
  scan_functionâ†’ print a named function end-to-end with comments
  graph        â†’ ast_query / graphify call graph
  connect      â†’ cross-file call-graph walk
  file_info    â†’ purpose + key functions + first 3 lines per function
  patch        â†’ fuzzy match â†’ LLM find/replace â†’ diff â†’ confirm

The vocab file lives next to this file: intent_vocab.json
"""

from __future__ import annotations

import os
import ast
import sys
import json
import re
import io
import difflib
import argparse
import subprocess
import time
import shutil
import urllib.request
from pathlib import Path

# Set UTF-8 encoding for stdout/stderr (Windows PowerShell safe)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

# â”€â”€ colours â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

def hdr(phase: str, title: str):
    print(f"\n{BOLD}{CYAN}â”â”  {phase}  {RST}{BOLD}{title}{RST}")
    print(f"{DIM}{'â”€' * 60}{RST}")
    if "--show-cwd" in (title or "") or os.environ.get("PYSLICK_SHOW_CWD") == "1":
        print(f"{DIM}  cwd: {os.getcwd()}{RST}")
    print(f"{DIM}{'â”€' * 60}{RST}")

def ok(msg):   print(f"{GREEN}  âœ” {msg}{RST}")
def warn(msg): print(f"{YELL}  âš  {msg}{RST}")
def err(msg):  print(f"{RED}  âœ– {msg}{RST}")

# â”€â”€ API providers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
PROVIDERS = {
    "anthropic": {
        "api_url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-6",
        "key_env": "ANTHROPIC_API_KEY",
        "headers": {"anthropic-version": "2023-06-01"},
    },
    "nvidia": {
        "api_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "meta/llama-3.1-70b-instruct",
        "key_env": "NVIDIA_API_KEY",
        "headers": {},
        "openai_compatible": True,
    },
    "groq": {
        "api_url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "key_env": "GROQ_API_KEY",
        "headers": {},
        "openai_compatible": True,
    },
    "openrouter": {
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "key_env": "OPENROUTER_API_KEY",
        "headers": {},
        "openai_compatible": True,
    },
}

DEFAULT_PROVIDER = "anthropic"
MAX_TOOL_ROUNDS  = 6

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out",
}
CODE_EXTS = {
    # Python
    ".py", ".pyw", ".ipynb",
    # JavaScript / TypeScript / Web
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    # Systems / Compiled
    ".java", ".kt", ".kts", ".scala",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp",
    ".cs", ".rs", ".go", ".swift", ".m", ".mm",
    # Shell / Scripts
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    # Config / Data
    ".json", ".yaml", ".yml", ".toml", ".sql", ".md", ".env", ".xml",
}


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# LOCAL TOOL IMPLEMENTATIONS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def tool_get_file(path: str) -> str:
    """Read a file's full content."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR reading {path}: {e}"


def tool_get_file_lines(path: str) -> str:
    """Read a file with 3-digit numbered lines."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(f"{i+1:3d}\t{line}" for i, line in enumerate(lines))
    except Exception as e:
        return f"ERROR reading {path}: {e}"


def tool_scan_names(root: str = ".") -> str:
    """List source files recursively."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            if Path(fn).suffix in CODE_EXTS or Path(fn).suffix == "":
                rel = os.path.normpath(os.path.join(dirpath, fn))
                files.append(rel)
    if not files:
        return f"(no source files found under '{root}')"
    return "\n".join(files)


def tool_scan_lines(path: str, keyword: str) -> str:
    """Find lines containing keyword (case-insensitive)."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        hits = [
            f"  {i+1:4d}: {line}"
            for i, line in enumerate(lines)
            if keyword.lower() in line.lower()
        ]
        if not hits:
            return f"(no lines matching '{keyword}' in {path})"
        return f"Lines matching '{keyword}' in {path}:\n" + "\n".join(hits)
    except Exception as e:
        return f"ERROR: {e}"


_RG_BIN = None  # cached: resolved ripgrep binary path, or "" if unavailable


def _ripgrep_binary() -> str:
    global _RG_BIN
    if _RG_BIN is None:
        _RG_BIN = shutil.which("rg") or ""
    return _RG_BIN


def _grep_via_ripgrep(path: str, patterns: list[str], context: int) -> list[tuple] | None:
    """Runs one `rg --json` invocation per pattern (rg's JSON stream doesn't
    say which -e matched, and tool_grep needs to tag each hit with its
    pattern, same as the pure-Python loop below). Returns a list of
    (line_idx0, matched_pattern, {line_idx0: text, ...}) tuples â€” the dict
    is the context window rg already computed for that match â€” or None if
    `rg` isn't installed or anything about the call goes wrong, in which
    case the caller falls back to the pure-Python scan unchanged."""
    binary = _ripgrep_binary()
    if not binary:
        return None
    hits: list[tuple] = []
    try:
        for pat in patterns:
            cmd = [binary, "--json", "-i", f"-C{context}", "-e", pat, "--", path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode not in (0, 1):  # 1 == "no matches", not an error
                return None
            window: dict[int, str] = {}
            match_lines: list[int] = []
            for line in res.stdout.splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") not in ("match", "context"):
                    continue
                data = obj["data"]
                lno = data["line_number"] - 1
                text = data["lines"]["text"].rstrip("\n")
                window[lno] = text
                if obj["type"] == "match":
                    match_lines.append(lno)
            for lno in match_lines:
                hits.append((lno, pat, window))
        return hits
    except (subprocess.TimeoutExpired, OSError, Exception):
        return None


def tool_grep(path: str, patterns: list[str], context: int = 2) -> str:
    """Search file for patterns with N lines of context.

    Tries ripgrep first (fast, respects .gitignore-style conventions even
    though this is a single-file search) and falls back to the pure-Python
    regex scan below if `rg` isn't installed or the call fails for any
    reason â€” this fallback path is unchanged from before ripgrep support
    was added, so tool_grep keeps working with zero extra dependencies."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"

    rg_hits = _grep_via_ripgrep(path, patterns, context)
    if rg_hits is not None:
        if not rg_hits:
            return f"(no matches for {patterns} in {path})"
        out = ["  (via ripgrep)"]
        seen_ranges: set[int] = set()
        for i, pat, window in rg_hits:
            lo, hi = min(window), max(window)
            if any(j in seen_ranges for j in range(lo, hi + 1)):
                continue
            out.append(f"  --- match: line {i+1} ---")
            for j in range(lo, hi + 1):
                marker = ">>>" if j == i else "   "
                out.append(f"  {marker} {j+1:4d}: {window.get(j, '')}")
            seen_ranges.update(range(lo, hi + 1))
        return "\n".join(out)

    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        out = []
        seen_ranges: set[int] = set()
        for pat in patterns:
            try:
                rx = re.compile(pat, re.IGNORECASE)
            except re.error:
                rx = re.compile(re.escape(pat), re.IGNORECASE)
            for i, line in enumerate(lines):
                if rx.search(line):
                    lo = max(0, i - context)
                    hi = min(len(lines) - 1, i + context)
                    if any(j in seen_ranges for j in range(lo, hi + 1)):
                        continue
                    out.append(f"  --- match: line {i+1} ---")
                    for j in range(lo, hi + 1):
                        marker = ">>>" if j == i else "   "
                        out.append(f"  {marker} {j+1:4d}: {lines[j]}")
                    seen_ranges.update(range(lo, hi + 1))
        return "\n".join(out) if out else f"(no matches for {patterns} in {path})"
    except Exception as e:
        return f"ERROR: {e}"


def tool_find_blocks(path: str) -> str:
    """Find named comment blocks and descriptive comment anchors."""
    try:
        from comment_blocks import scan_file_for_comment_blocks
        nodes = scan_file_for_comment_blocks(path)
        if not nodes:
            return f"(no comment blocks found in {path})"
        lines = []
        for n in nodes:
            lines.append(
                f"  [{n.type}]  L{n.start_line}-{n.end_line}  "
                f"label=\"{n.label[:70]}\"  id={n.id}"
            )
            if n.code_preview:
                preview = n.code_preview[:120].replace("\n", " â†µ ")
                lines.append(f"    code: {preview}")
        return f"Comment blocks in {path}:\n" + "\n".join(lines)
    except ImportError:
        return "(comment_blocks module not available)"
    except Exception as e:
        return f"ERROR finding blocks in {path}: {e}"


def tool_ast_query(path: str, question: str) -> str:
    """AST call-graph query on a .py file."""
    if not path.endswith(".py"):
        return f"(ast_query only works on .py files; got {path})"
    try:
        from graphify import query as graphify_query
        results = graphify_query(path, question, top_k=4, depth=2, direction="both")
        if not results:
            return f"(no AST matches for '{question}' in {path})"
        out = []
        for r in results:
            sym = r.symbol
            out.append(
                f"  [{sym['type']}] {sym['name']}  "
                f"L{sym['start_line']}-{sym['end_line']}  score={r.score:.1f}"
            )
            if sym.get("docstring"):
                out.append(f"    doc: {sym['docstring'][:100]}")
            callees = sorted(sym.get("callees", set()))
            callers = sorted(sym.get("callers", set()))
            if callees:
                out.append(f"    calls â†’ {', '.join(callees)}")
            if callers:
                out.append(f"    called by â† {', '.join(callers)}")
            # First 20 lines of source
            src_lines = sym.get("code", "").splitlines()[:20]
            if src_lines:
                out.append("    source (first 20 lines):")
                for ln in src_lines:
                    out.append(f"      {ln}")
        return f"AST query '{question}' on {path}:\n" + "\n".join(out)
    except FileNotFoundError:
        return f"ERROR: file not found: {path}"
    except SyntaxError as e:
        return f"ERROR: syntax error in {path}: {e}"
    except Exception as e:
        return f"ERROR: {e}"


def tool_jsx_check(path: str) -> str:
    """Validate JSX/HTML tag matching."""
    try:
        from jsx_tag_checker import check_jsx_tags
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            check_jsx_tags(path)
        output = buf.getvalue().strip()
        return output if output else "OK â€” no JSX tag mismatches found."
    except ImportError:
        return "(jsx_tag_checker not available)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_llm_expand(directive: str) -> str:
    """Expand directive using local LLM (if available)."""
    try:
        from llm import maybe_expand_query, is_available
        if not is_available():
            return f"(LLM not available â€” using directive as-is: {directive})"
        expanded = maybe_expand_query(directive)
        if expanded == directive:
            return f"(LLM returned no expansion: {directive})"
        return f"Expanded: {expanded}"
    except ImportError:
        return f"(llm module unavailable: {directive})"
    except Exception as e:
        return f"ERROR: {e}"


def tool_llm_patch(directive: str, code_context: str) -> str:
    """Use local LLM to extract find/replace blocks from code context."""
    try:
        from llm import maybe_extract_find_replace, is_available
        if not is_available():
            return f"(LLM not available â€” cannot auto-extract patch)"
        find_str, replace_str = maybe_extract_find_replace(directive, code_context)
        if find_str and replace_str:
            return json.dumps({"find": find_str, "replace": replace_str})
        return f"(LLM could not extract patch from context)"
    except ImportError:
        return f"(llm module unavailable)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_pyslick_help() -> str:
    """Show pyslick help information."""
    try:
        from pyslick import print_help
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_help()
        return buf.getvalue()
    except Exception as e:
        return f"ERROR: {e}"


def tool_pyslick_checkpoint(message: str = None) -> str:
    """Create a git checkpoint with optional commit message."""
    try:
        from pyslick import git_checkpoint
        result = git_checkpoint(message)
        return "Checkpoint created successfully" if result else "Checkpoint failed"
    except Exception as e:
        return f"ERROR: {e}"


def tool_pyslick_status() -> str:
    """Show git status."""
    try:
        from pyslick import git_status
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            git_status()
        return buf.getvalue()
    except Exception as e:
        return f"ERROR: {e}"


def _get_repo_name() -> str:
    """Best-effort repo name from the git root directory. Empty string if not a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return os.path.basename(result.stdout.strip())
    except Exception:
        pass
    return ""


def _resolve_commit_reference(directive: str):
    """
    Parse a commit reference out of a rollback/revert/undo directive.

    Handles three distinct phrasings, which must NOT be conflated:
      - "N commits ago"      -> HEAD~N (relative to current HEAD)
      - "commit 3" / "#3"    -> the 3rd entry in `git log --oneline`,
                                 1-indexed from HEAD (NOT the same target
                                 as "3 commits ago" whenever any commits
                                 have been made since â€” one counts back
                                 from now, the other picks a fixed position
                                 in the visible log)
      - "the fix commit" / "initial commit" / etc.
                              -> word-overlap fuzzy match against the last
                                 30 commit messages, same approach as
                                 _fuzzy_match_files (no new dependency)

    Returns one of:
      ("resolved", commit_hash, commit_msg, label)
      ("ambiguous", [(commit_hash, commit_msg), ...])   # top-scoring ties
      ("none", None)                                    # no confident match
    Never guesses silently â€” an ambiguous or absent match is reported as
    such so the caller can ask the user rather than picking for them.
    """
    dl = directive.lower()

    # "N commits ago" -> HEAD~N
    m = re.search(r"(\d+)\s+commits?\s+ago", dl)
    if m:
        n = int(m.group(1))
        rp = subprocess.run(["git", "rev-parse", f"HEAD~{n}"], capture_output=True, text=True, timeout=5)
        if rp.returncode != 0:
            return ("none", None)
        commit_hash = rp.stdout.strip()
        msg_res = subprocess.run(["git", "log", "-1", "--pretty=%s", commit_hash],
                                  capture_output=True, text=True, timeout=5)
        commit_msg = msg_res.stdout.strip() if msg_res.returncode == 0 else ""
        return ("resolved", commit_hash, commit_msg, f"HEAD~{n} ({n} commit(s) ago)")

    # "commit #3" / "commit number 3" / "commit 3" -> 3rd entry in the log
    m = re.search(r"commit\s*(?:#|number|num)?\s*(\d+)\b", dl)
    if m:
        n = int(m.group(1))
        if n < 1:
            return ("none", None)
        lg = subprocess.run(["git", "log", "--oneline", f"-{n}"], capture_output=True, text=True, timeout=5)
        if lg.returncode != 0:
            return ("none", None)
        lines = [l for l in lg.stdout.splitlines() if l.strip()]
        if len(lines) < n:
            return ("none", None)
        target = lines[n - 1].split(" ", 1)
        commit_hash = target[0]
        commit_msg = target[1] if len(target) > 1 else ""
        return ("resolved", commit_hash, commit_msg, f"commit #{n}")

    # Keyword / fuzzy match against recent commit messages, e.g.
    # "rollback to the fix commit", "revert to initial commit".
    lg = subprocess.run(["git", "log", "--oneline", "-30"], capture_output=True, text=True, timeout=5)
    if lg.returncode != 0:
        return ("none", None)
    lines = [l for l in lg.stdout.splitlines() if l.strip()]
    if not lines:
        return ("none", None)

    phrase_match = re.search(r"\b(?:to|commit)\b\s+(.*)", dl)
    search_text = phrase_match.group(1) if phrase_match else dl
    stop_words = {"the", "a", "an", "commit", "to", "of", "that", "one", "please", "pls"}
    search_tokens = {
        w for w in re.findall(r"[a-z0-9]+", search_text)
        if w not in stop_words and len(w) > 2
    }
    if not search_tokens:
        return ("none", None)

    scored = []
    for line in lines:
        parts = line.split(" ", 1)
        commit_hash = parts[0]
        commit_msg = parts[1] if len(parts) > 1 else ""
        msg_tokens = set(re.findall(r"[a-z0-9]+", commit_msg.lower()))
        score = len(search_tokens & msg_tokens)
        if score > 0:
            scored.append((score, commit_hash, commit_msg))

    if not scored:
        return ("none", None)

    best_score = max(s[0] for s in scored)
    top = [(h, m) for s, h, m in scored if s == best_score]
    if len(top) > 1:
        return ("ambiguous", top)
    commit_hash, commit_msg = top[0]
    return ("resolved", commit_hash, commit_msg, commit_msg)


def tool_pyslick_add_commit_push(message: str = None) -> tuple:
    """
    Full auto flow for a plain 'push' request:
      1. Detect an unresolved merge from a previous pull (.git/MERGE_HEAD
         exists). If found, the merge must be committed (or aborted) before
         anything else can safely happen â€” this was the exact state
         reported: 'error: You have not concluded your merge'.
      2. git add . -> git commit -m <message>
      3. git push -> on rejection (remote has commits we don't), git pull
         -> retry push once.
    Runs unconditionally when a repo is found â€” no per-step confirmation,
    since 'push' is already the user's explicit instruction, not an
    ambiguous checkpoint. Returns (ok: bool, log: str).
    """
    log_lines = []
    try:
        git_dir_res = subprocess.run(
            ["git", "rev-parse", "--git-dir"], capture_output=True, text=True, timeout=5
        )
        if git_dir_res.returncode == 0:
            git_dir = git_dir_res.stdout.strip()
            merge_head_path = os.path.join(git_dir, "MERGE_HEAD")
            if os.path.exists(merge_head_path):
                # A previous 'git pull' left an unresolved merge. Check
                # whether there are still real conflict markers (unmerged
                # paths) â€” if so, this can't be auto-resolved safely and
                # must stop here. If not (the merge was actually resolved,
                # just never committed â€” exactly the reported case), commit
                # it now so the flow can proceed.
                status_res = subprocess.run(
                    ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=10
                )
                has_conflict_markers = any(
                    line.startswith(("UU ", "AA ", "DD ", "AU ", "UA ", "UD ", "DU "))
                    for line in status_res.stdout.splitlines()
                )
                if has_conflict_markers:
                    log_lines.append("Unresolved merge conflicts found (.git/MERGE_HEAD + conflicted files).")
                    log_lines.append("pyslick will not auto-resolve conflicts â€” resolve them manually, then commit, then push.")
                    return False, "\n".join(log_lines)
                else:
                    log_lines.append("Found a concluded-but-uncommitted merge from a previous pull â€” committing it now.")
                    merge_commit_res = subprocess.run(
                        ["git", "commit", "--no-edit"], capture_output=True, text=True, timeout=10
                    )
                    if merge_commit_res.returncode == 0:
                        log_lines.append("git commit (merge) â€” succeeded")
                    else:
                        err = (merge_commit_res.stderr or merge_commit_res.stdout or "").strip()
                        log_lines.append(f"git commit (merge) â€” FAILED: {err[:300]}")
                        return False, "\n".join(log_lines)

        add_res = subprocess.run(["git", "add", "."], capture_output=True, text=True, timeout=10)
        log_lines.append("git add .")

        if message is None:
            try:
                from pyslick import generate_smart_commit_message
                message = generate_smart_commit_message(None)
            except Exception:
                message = "pyslick auto-commit"

        commit_res = subprocess.run(
            ["git", "commit", "-m", message], capture_output=True, text=True, timeout=10
        )
        if commit_res.returncode == 0:
            log_lines.append(f'git commit -m "{message}"')
        else:
            # Nothing to commit is not a failure for a push request â€” the
            # existing ahead-by-N commit(s) may already be exactly what
            # needs pushing (this is precisely the case in the reported
            # terminal output: 1 commit ahead, nothing new staged).
            log_lines.append("(nothing new to commit â€” pushing existing commits)")

        push_res = subprocess.run(["git", "push"], capture_output=True, text=True, timeout=15)
        if push_res.returncode == 0:
            log_lines.append("git push â€” succeeded")
            return True, "\n".join(log_lines)

        err = (push_res.stderr or push_res.stdout or "").strip()

        # "fetch first" / non-fast-forward rejection: the remote has commits
        # this clone doesn't. A plain retry can never succeed here â€” try a
        # pull (merge) first, same as the hint git itself prints, then
        # retry the push once. If the pull itself hits a conflict, stop and
        # report it honestly rather than guessing how to resolve it.
        if "fetch first" in err.lower() or "non-fast-forward" in err.lower() or "rejected" in err.lower():
            log_lines.append(f"git push â€” REJECTED (remote has commits you don't have locally)")
            log_lines.append("Attempting git pull to merge remote changes...")
            pull_res = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
            pull_out = (pull_res.stdout or "") + (pull_res.stderr or "")
            if pull_res.returncode != 0 or "CONFLICT" in pull_out:
                log_lines.append("git pull â€” FAILED or produced conflicts:")
                log_lines.append(pull_out.strip()[:500])
                log_lines.append("Resolve the conflict manually, then push again â€” pyslick will not auto-resolve merge conflicts.")
                return False, "\n".join(log_lines)
            log_lines.append("git pull â€” merged successfully")

            retry_res = subprocess.run(["git", "push"], capture_output=True, text=True, timeout=15)
            if retry_res.returncode == 0:
                log_lines.append("git push (retry) â€” succeeded")
                return True, "\n".join(log_lines)
            else:
                retry_err = (retry_res.stderr or retry_res.stdout or "").strip()
                log_lines.append(f"git push (retry) â€” FAILED: {retry_err[:300]}")
                return False, "\n".join(log_lines)

        log_lines.append(f"git push â€” FAILED: {err[:300]}")
        return False, "\n".join(log_lines)
    except Exception as e:
        log_lines.append(f"ERROR: {e}")
        return False, "\n".join(log_lines)


def tool_pyslick_log() -> str:
    """Show git commit history log."""
    try:
        from pyslick import git_log
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            git_log()
        out = buf.getvalue()
        return out if out.strip() else "(no git commit history found)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_pyslick_diff() -> str:
    """Show git diff."""
    try:
        import subprocess
        res = subprocess.run(["git", "diff"], capture_output=True, text=True)
        return res.stdout if res.stdout.strip() else "(working tree clean â€” no unstaged changes)"
    except Exception as e:
        return f"ERROR: {e}"


# â”€â”€ dispatcher â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

TOOL_IMPLEMENTATIONS = {
    "get_file":       lambda args: tool_get_file(args["path"]),
    "get_file_lines": lambda args: tool_get_file_lines(args["path"]),
    "scan_names":     lambda args: tool_scan_names(args.get("root", ".")),
    "scan_lines":     lambda args: tool_scan_lines(args["path"], args["keyword"]),
    "grep":           lambda args: tool_grep(
                          args["path"],
                          args["patterns"] if isinstance(args["patterns"], list)
                          else [args["patterns"]],
                          int(args.get("context", 2))
                      ),
    "find_blocks":    lambda args: tool_find_blocks(args["path"]),
    "ast_query":      lambda args: tool_ast_query(args["path"], args["question"]),
    "jsx_check":      lambda args: tool_jsx_check(args["path"]),
    "llm_expand":     lambda args: tool_llm_expand(args["directive"]),
    "llm_patch":      lambda args: tool_llm_patch(args["directive"], args["code_context"]),
    "pyslick_help":   lambda args: tool_pyslick_help(),
    "pyslick_checkpoint": lambda args: tool_pyslick_checkpoint(args.get("message")),
    "pyslick_status": lambda args: tool_pyslick_status(),
    "pyslick_log":    lambda args: tool_pyslick_log(),
    "pyslick_diff":   lambda args: tool_pyslick_diff(),
}


def dispatch_tool(name: str, args: dict) -> str:
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        result = impl(args)
        # Truncate very large outputs so they don't blow the context window
        if isinstance(result, str) and len(result) > 12_000:
            result = result[:12_000] + f"\nâ€¦ [truncated â€” {len(result)} chars total]"
        return result
    except Exception as e:
        return f"ERROR executing {name}: {e}"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ANTHROPIC API  (tool-use / function-calling)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# Schema sent to the API so Claude knows what tools exist
TOOL_SCHEMA = [
    {
        "name": "get_file",
        "description": (
            "Read a file's full content. Use this to see the complete source "
            "of any file in the project. Equivalent to Get-Content -Raw."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative or absolute path to the file."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_file_lines",
        "description": (
            "Read a file with 3-digit line numbers prefixed to every line. "
            "Use this when you need to reference exact line numbers for a patch. "
            "Equivalent to Get-Content | ForEach-Object numbered."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "scan_names",
        "description": (
            "List every source file name recursively under a root directory, "
            "skipping build artifacts (node_modules, .git, dist, etc). "
            "Use this first to get oriented in an unfamiliar project. "
            "Equivalent to Get-ChildItem -Recurse -File."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Root directory to scan (default: '.')"}
            },
            "required": [],
        },
    },
    {
        "name": "scan_lines",
        "description": (
            "Find every line in a file that contains a keyword (case-insensitive). "
            "Use this to quickly locate all occurrences of a class name, function "
            "name, CSS property, or any token across a file. "
            "Equivalent to Select-String -Path -Pattern."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File to search."},
                "keyword": {"type": "string", "description": "Text to search for (case-insensitive)."},
            },
            "required": ["path", "keyword"],
        },
    },
    {
        "name": "grep",
        "description": (
            "Search a file for one or more regex/literal patterns, returning "
            "each match with N lines of surrounding context. More powerful "
            "than scan_lines when you need context around the match. "
            "Equivalent to Select-String -Context N,N."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":     {"type": "string",  "description": "File to search."},
                "patterns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of regex or literal patterns to search for.",
                },
                "context":  {"type": "integer", "description": "Lines of context around each match (default 2)."},
            },
            "required": ["path", "patterns"],
        },
    },
    {
        "name": "find_blocks",
        "description": (
            "Scan a file for named comment blocks (pyslick:start/end markers) "
            "and descriptive comments that sit directly above code regions. "
            "Returns each block's label, line range, and a code preview. "
            "Use this to locate CSS rule groups, HTML sections, config regions, "
            "or any code region described by a comment rather than a function name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to scan for blocks."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "ast_query",
        "description": (
            "Run an AST call-graph query on a Python (.py) file. Given a natural "
            "language question, returns the best-matching functions/classes with "
            "their line ranges, docstrings, callers, callees, and source code. "
            "Use this for Python files when you need to understand the call graph "
            "around the code you want to change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":     {"type": "string", "description": "Path to the .py file."},
                "question": {"type": "string", "description": "Natural language description of what you're looking for."},
            },
            "required": ["path", "question"],
        },
    },
    {
        "name": "jsx_check",
        "description": (
            "Validate JSX/HTML tag matching in a file. Returns a list of "
            "mismatched or unclosed tags, or 'OK' if the file is clean. "
            "Run this before proposing a patch to a .tsx/.jsx/.html file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the JSX/HTML file."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "llm_expand",
        "description": (
            "If a local LLM model is installed (~/.pyslick/models/), expand "
            "the user's directive into extra search terms for richer fuzzy "
            "matching. Falls back silently if no model is present. "
            "Call this first when the directive uses informal/imprecise language."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "directive": {"type": "string", "description": "The user's original directive."}
            },
            "required": ["directive"],
        },
    },
    {
        "name": "llm_patch",
        "description": (
            "If a local LLM model is installed, use it to extract find/replace "
            "blocks from code context. Takes the directive and code context, "
            "returns JSON with 'find' and 'replace' keys. Use this to auto-generate "
            "patch proposals when the local LLM is available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "directive":    {"type": "string", "description": "The user's original directive."},
                "code_context": {"type": "string", "description": "The code context to analyze."},
            },
            "required": ["directive", "code_context"],
        },
    },
    {
        "name": "pyslick_help",
        "description": (
            "Show the full pyslick help documentation with all available commands "
            "and their usage. Use this when the user asks about pyslick capabilities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "pyslick_checkpoint",
        "description": (
            "Create a git checkpoint (commit) with an optional commit message. "
            "If no git repository exists, creates a local backup. Attempts to push "
            "to remote if configured. Use this after making changes to save progress."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Optional commit message."},
            },
            "required": [],
        },
    },
    {
        "name": "pyslick_status",
        "description": (
            "Show the current git status of the repository. Use this to see "
            "what files have been modified or staged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

SYSTEM_PROMPT = """\
You are PySlick Agent, an autonomous coding assistant that locates and \
proposes changes in a local codebase. You have a set of read-only tool \
calls to explore the project. You NEVER write files directly â€” you always \
end by producing a JSON patch proposal that the human reviews.

TOOL-USE RULES:
- Start with scan_names to see what files exist.
- Use llm_expand first if the directive is informal.
- Use scan_lines or grep to find where specific tokens appear.
- Use get_file_lines (not get_file) when you will reference line numbers.
- Use find_blocks for CSS/HTML/config files where there are no function names.
- Use ast_query only for .py files.
- Use jsx_check before patching any .tsx/.jsx/.html file.
- Use llm_patch to auto-generate find/replace from code context if local LLM is available.
- Use pyslick_help when user asks about pyslick commands.
- Use pyslick_checkpoint to save progress after changes.
- Use pyslick_status to check git state.
- You may call tools multiple times and in any order.
- Stop calling tools once you have enough to make a precise patch.

PATCH PROPOSAL FORMAT:
When you are confident about the change, output ONLY a JSON object (no \
markdown fences, no extra text) with these exact keys:
{
  "file": "<relative path to the file to patch>",
  "find": "<exact substring currently in the file â€” must be unique enough to locate it>",
  "replace": "<the new text that replaces it>",
  "explanation": "<1-3 sentences explaining what you found and why this change achieves the directive>"
}

If you cannot find where to make the change, output:
{
  "file": null,
  "find": null,
  "replace": null,
  "explanation": "<what you searched for and why you couldn't locate it>"
}
"""


def _get_provider():
    """Detect which API provider to use based on available keys."""
    provider_name = os.environ.get("PYSLICK_API_PROVIDER", DEFAULT_PROVIDER).lower()

    if provider_name in PROVIDERS:
        provider = PROVIDERS[provider_name]
        if os.environ.get(provider["key_env"]):
            return provider_name, provider

    for name, provider in PROVIDERS.items():
        if os.environ.get(provider["key_env"]):
            return name, provider

    return None, None


def _relay_active() -> bool:
    """Return True if the relay server (browser extension bridge) is listening."""
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        return s.connect_ex(("127.0.0.1", 27182)) == 0
    except Exception:
        return False
    finally:
        try: s.close()
        except: pass

def _call_api(messages: list[dict], use_tools: bool = True, provider_name: str = None) -> dict:
    provider_name, provider = _get_provider()
    if not provider:
        raise RuntimeError(
            "No API key found. Set ANTHROPIC_API_KEY, NVIDIA_API_KEY, "
            "GROQ_API_KEY, or OPENROUTER_API_KEY, "
            "or use local LLM mode by unsetting all API keys."
        )

    api_url = provider["api_url"]
    model   = provider["model"]
    key_env = provider["key_env"]
    api_key = os.environ.get(key_env)

    if provider.get("openai_compatible"):
        openai_messages = [{"role": m["role"], "content": m["content"]} for m in messages]
        openai_tools = []
        if use_tools:
            for tool in TOOL_SCHEMA:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                })
        payload = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": 2048,
            "temperature": 0.7,
        }
        if openai_tools:
            payload["tools"] = openai_tools
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **provider["headers"],
        }
        # OpenRouter asks OpenAI-compatible callers to identify their app â€”
        # harmless no-op for Groq/NVIDIA, but keeps OpenRouter's dashboard useful.
        if provider_name == "openrouter":
            headers.setdefault("HTTP-Referer", "https://github.com/pyslick")
            headers.setdefault("X-Title", "pyslick")
    else:
        payload = {
            "model": model,
            "max_tokens": 2048,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        }
        if use_tools:
            payload["tools"] = TOOL_SCHEMA
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            **provider["headers"],
        }

    data = json.dumps(payload).encode()
    req  = urllib.request.Request(api_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read())
            if provider.get("openai_compatible"):
                return _convert_openai_to_anthropic(response)
            return response
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"API HTTP {e.code}: {body[:400]}") from e
    except Exception as e:
        raise RuntimeError(f"API call failed: {e}") from e


def _convert_openai_to_anthropic(response: dict) -> dict:
    """Convert OpenAI-format response (NVIDIA) to Anthropic format."""
    choice  = response.get("choices", [{}])[0]
    message = choice.get("message", {})

    content      = []
    text_content = message.get("content", "")
    tool_calls   = message.get("tool_calls", [])

    for tc in tool_calls:
        content.append({
            "type":  "tool_use",
            "id":    tc.get("id", ""),
            "name":  tc.get("function", {}).get("name", ""),
            "input": json.loads(tc.get("function", {}).get("arguments", "{}")),
        })

    if text_content:
        if isinstance(text_content, str):
            content.append({"type": "text", "text": text_content})
        elif isinstance(text_content, list):
            for item in text_content:
                if item.get("type") == "text":
                    content.append({"type": "text", "text": item.get("text", "")})

    return {
        "content":     content,
        "stop_reason": "end_turn" if not tool_calls else "tool_use",
    }


def _extract_text(content: list[dict]) -> str:
    return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def _extract_tool_uses(content: list[dict]) -> list[dict]:
    return [b for b in content if b.get("type") == "tool_use"]


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# INTENT VOCAB â€” loads intent_vocab.json once at import time
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _load_vocab() -> dict:
    """Load intent_vocab.json from the same directory as this file."""
    vocab_path = Path(THIS_DIR) / "intent_vocab.json"
    if not vocab_path.exists():
        # Graceful degradation â€” return minimal inline vocab
        return {
            "intents": {
                "help":          {"require_any": ["pyslick help", "pyslick commands", "pyslick usage"], "exclude_if": []},
                "git":           {"require_any": ["git push", "git status", "git commit", "push to github", "checkpoint"], "exclude_if": []},
                "list_files":    {"require_any": ["list all files", "list files", "all files in", "files in directory", "cd into", "main methods", "main objects"], "exclude_if": []},
                "comments":      {"require_any": ["show me the comments", "show comments", "comments in", "comments of the code", "print the comments"], "exclude_if": []},
                "nearest":       {"require_any": ["nearest", "closest to", "closest function", "function near", "name of the function closest"], "exclude_if": []},
                "scan_function": {"require_any": ["scan the entire", "entire function", "end to end", "full function", "print it including", "including the comments"], "exclude_if": []},
                "graph":         {"require_any": ["call graph", "ast graph", "graphify", "dependency graph", "who calls who"], "exclude_if": []},
                "connect":       {"require_any": ["connect", "linked to", "calls", "called by", "imports", "imported by", "relationship between", "how it connects"], "exclude_if": []},
                "file_info":     {"require_any": ["what does", "whats the use", "purpose of", "explain this file", "how does this file", "summarize this file", "contents of"], "exclude_if": []},
                "patch":         {"require_any": ["fix", "change", "rename", "resize", "remove", "add", "update", "refactor", "replace", "delete", "make the", "patch", "rewrite", "edit", "modify"], "exclude_if": []},
            },
            "file_extract": {
                "strip_words": ["file", "the", "a", "an", "this", "that", "my", "of", "in", "about", "use", "purpose", "whats", "what", "is", "does", "how", "show", "tell", "explain", "describe", "summarize"],
                "min_len": 3,
                "min_fuzzy_score": 58,
                "max_results": 3,
            },
        }
    try:
        return json.loads(vocab_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_VOCAB = _load_vocab()


_LEARNED_PATH = Path(THIS_DIR) / "learned_intents.json"
_USER_WEIGHTS_PATH = Path(THIS_DIR) / "user_weights.json"
_FRUSTRATION_PATH = Path(THIS_DIR) / "frustration_vocab.json"

INTENT_LABELS = {
    "help":          "pyslick help / commands / usage",
    "git":           "git push / commit / checkpoint",
    "run_info":      "how to run / start / launch project",
    "list_files":    "list / show files in a directory",
    "comments":      "show comments in a file",
    "nearest":       "nearest / closest function or node",
    "scan_function": "scan / show full function body",
    "graph":         "call graph / AST graph / dependency graph",
    "connect":       "how files / functions connect / link",
    "file_info":     "open file / cat / scan / snippet / lines",
    "patch":         "fix / change / edit / modify code",
}


def _load_user_weights() -> dict:
    """Load persistent user weight deltas from user_weights.json."""
    if not _USER_WEIGHTS_PATH.exists():
        return {}
    try:
        return json.loads(_USER_WEIGHTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_user_weights(weights: dict) -> None:
    """Persist user weight adjustments."""
    try:
        _USER_WEIGHTS_PATH.write_text(
            json.dumps(weights, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _adjust_intent_weight(intent_name: str, delta: float) -> float:
    """Adjust an intent's weight multiplier dynamically based on user feedback."""
    weights = _load_user_weights()
    cfg = _VOCAB.get("intents", {}).get(intent_name, {})
    base_w = float(cfg.get("weight", 1.0))
    current_w = float(weights.get(intent_name, base_w))
    new_w = max(0.35, min(1.45, current_w + delta))
    weights[intent_name] = round(new_w, 2)
    _save_user_weights(weights)
    return new_w


def _get_session_path() -> Path:
    """Session state stored in local project .pyslick or user home."""
    pyslick_dir = Path.cwd() / ".pyslick"
    pyslick_dir.mkdir(parents=True, exist_ok=True)
    return pyslick_dir / "session.json"


def _load_session() -> dict:
    p = _get_session_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_session(last_directive: str, last_intent: str, calm_streak: int = 0) -> None:
    p = _get_session_path()
    try:
        data = {
            "last_directive": last_directive,
            "last_intent": last_intent,
            "calm_streak": calm_streak,
            "timestamp": datetime.now().isoformat()
        }
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_frustration_vocab() -> dict:
    if not _FRUSTRATION_PATH.exists():
        return {}
    try:
        return json.loads(_FRUSTRATION_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _detect_frustration_and_correction(directive: str) -> tuple[str, bool, str | None]:
    """
    Check if the user query contains frustration/correction signals.
    - If frustrated:
        1. Penalize the last session's intent weight (-0.08)
        2. Extract clean intended query
        3. Save query mapping
    - If calm:
        1. Increment calm streak
        2. Boost weight (+0.02) after calm streak >= 3
    """
    f_vocab = _load_frustration_vocab()
    f_signals = f_vocab.get("frustration", {}).get("signals", [
        "you are wrong", "thats wrong", "that's wrong", "not what i meant",
        "i meant", "i mean", "you stupid", "you dumb", "wrong intent", "its wrong", "it's wrong"
    ])
    dl = directive.lower().strip()

    matched_signal = None
    for sig in f_signals:
        if sig in dl:
            matched_signal = sig
            break

    session = _load_session()
    last_intent = session.get("last_intent")
    calm_streak = session.get("calm_streak", 0)

    if matched_signal:
        # Frustration detected
        if last_intent and last_intent in INTENT_LABELS:
            new_w = _adjust_intent_weight(last_intent, -0.08)
            warn(f"Feedback noted ('{matched_signal}') â†’ Reduced '{last_intent}' weight to {new_w:.2f}.")

        # Extract correction portion
        clean = directive
        m = re.search(
            r"(?:what\s+i\s+meant\s+is|i\s+meant|i\s+mean|instead|actually|it\s+should\s+be|what\s+i\s+want\s+is)\s*[:\s]+(.+)$",
            directive,
            re.IGNORECASE
        )
        if m:
            clean = m.group(1).strip()
        else:
            clean = re.sub(re.escape(matched_signal), "", directive, flags=re.IGNORECASE).strip()
            clean = re.sub(r"^(?:no|its wrong|it's wrong|that's wrong|wrong|stupid|dumb|no no)[,\s:]*", "", clean, flags=re.IGNORECASE).strip()

        _save_session(directive, last_intent or "unknown", calm_streak=0)
        return clean or directive, True, last_intent
    else:
        # Calm interaction
        calm_streak += 1
        if calm_streak >= 3 and last_intent and last_intent in INTENT_LABELS:
            _adjust_intent_weight(last_intent, +0.02)
        _save_session(directive, last_intent or "unknown", calm_streak=calm_streak)
        return directive, False, None


def summarize_intent_keywords(directive: str, intent: str, target_file: str | None = None, line_range: str | None = None) -> str:
    """Summarizes intent into clean simple keywords like 'cat scan', 'file view', 'lines 30-70'."""
    parts = []
    if intent == "file_info":
        parts.append("file view / cat scan")
    elif intent == "run_info":
        parts.append("project run / scripts")
    elif intent == "connect":
        parts.append("cross-file connections")
    elif intent == "comments":
        parts.append("comments inspection")
    elif intent == "nearest":
        parts.append("nearest symbol lookup")
    elif intent == "scan_function":
        parts.append("function scan")
    elif intent == "git":
        parts.append("git status / checkpoint")
    elif intent == "list_files":
        parts.append("directory file list")
    else:
        parts.append(intent)

    if target_file:
        parts.append(os.path.basename(target_file))
    if line_range:
        parts.append(line_range)
    return " â€¢ ".join(parts)


def _load_learned() -> dict:
    """Load learned_intents.json â€” maps exact query strings to intent names."""
    if not _LEARNED_PATH.exists():
        return {}
    try:
        return json.loads(_LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_learned_intent(query: str, intent: str) -> None:
    """
    Persist a user-confirmed query â†’ intent mapping to learned_intents.json.
    Next time this exact query is seen it routes with 100% confidence.
    """
    learned = _load_learned()
    learned[query.lower().strip()] = intent
    try:
        _LEARNED_PATH.write_text(
            json.dumps(learned, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _classify_intent(directive: str) -> str:
    """
    Confidence-scored intent routing using rapidfuzz + exact phrase matching.
      - Exact multi-word substring match  â†’ 100% confidence (fastest path)
      - Single-word patterns scored word-by-word so a short word like 'patch'
        cannot outscore a typo of a longer phrase like 'how it connects'.
    """
    intent, _, _ = _classify_intent_with_confidence(directive)
    return intent


def _classify_intent_with_confidence(directive: str) -> tuple:
    """
    Hybrid three-tier intent classification. Returns (intent, confidence, top3).

    Tier 0 â€” learned_intents.json  (user-confirmed corrections, 100% confidence)
    Tier 1 â€” Exact vocab substring  (intent_vocab.json, 100% confidence)
    Tier 2 â€” Python fuzzy           (rapidfuzz word-level, 0-100%)
    Tier 3 â€” LLM arbitration        (called only when Tier 2 gives 55-84%)

    Returns:
        intent     : str    â€” winning intent name
        confidence : float  â€” 0-100, 100 = exact match
        top3       : list   â€” [(intent_name, score), ...] top candidates for did-you-mean
    """
    dl = directive.lower().strip()
    intents = _VOCAB.get("intents", {})

    priority = [
        "help", "git", "run_info", "list_files", "comments", "nearest",
        "scan_function", "graph", "connect", "file_info",
        "stray_symbols", "syntax_check", "indentation", "smoketest",
        "patch",
    ]

    # â”€â”€ Tier 0: learned corrections â€” fastest possible win â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    learned = _load_learned()
    if dl in learned and learned[dl] in priority:
        return learned[dl], 100.0, [(learned[dl], 100.0)]

    # â”€â”€ Tier 0.1: explicit "/filename" or bare "filename.ext" reference â”€â”€â”€
    # If the directive names a file explicitly â€” either leading-slash
    # syntax, or just the bare filename as long as it actually exists in
    # the project â€” and doesn't also contain a clear git/patch/comment/
    # graph keyword, the file reference itself is the strongest signal we
    # have â€” route to file_info rather than falling through to whatever
    # the classifier's default happens to be. (Previously: generic verbs
    # like "explain" had no keyword weight anywhere and could misroute to
    # an unrelated intent such as "git" at low confidence; separately, a
    # directive that named a real file by its bare name â€” e.g. "why is
    # patchit.py so huge" â€” got no special treatment at all and could
    # misroute just as easily as one with no file mentioned.)
    has_explicit_file_ref = bool(
        re.search(r"(?<!\S)/[a-zA-Z0-9_./\\\-]+\.[a-zA-Z0-9]+", directive)
    )

    # Bare filename check: only counts as a signal if the token actually
    # names a real file in the project â€” otherwise words that merely
    # happen to contain a dot (version numbers, "e.g.", etc.) would
    # false-positive. Deliberately conservative: exact basename match only.
    has_bare_file_ref = False
    if not has_explicit_file_ref:
        bare_tokens = re.findall(r"\b([a-zA-Z0-9_\-]+\.[a-zA-Z0-9]{1,5})\b", directive)
        if bare_tokens:
            try:
                _known_basenames = {
                    os.path.basename(f).lower() for f in _collect_all_files()
                }
                has_bare_file_ref = any(
                    tok.lower() in _known_basenames for tok in bare_tokens
                )
            except Exception:
                has_bare_file_ref = False

    _STRONG_OVERRIDE_KEYWORDS = [
        r"\bcommit\b", r"\bpush\b", r"\bcheckpoint\b", r"\bgit\b",
        r"\bdiff\b", r"\blog\b", r"\bhistory\b",
        r"\bpatch\b", r"\breplace\b", r"\bfix\b", r"\bchange\b", r"\bmake\b",
        r"\bcomment\b", r"\bcomments\b",
        r"\bgraph\b", r"\bconnect\b", r"\bcall(?:s|ed by|er)?\b",
        r"\bfunction\b", r"\bmethod\b", r"\bline\b", r"\blines\b",
        r"\bundo\b", r"\brevert\b", r"\brollback\b",
        r"\bbroken\b", r"\berrors?\b", r"\bbugs?\b", r"\bsyntax\s*error\b",
        r"\bcrash(?:ing|es)?\b", r"\bfail(?:ing|s)?\b",
    ]
    has_strong_override = any(re.search(kw, dl) for kw in _STRONG_OVERRIDE_KEYWORDS)
    if (has_explicit_file_ref or has_bare_file_ref) and not has_strong_override:
        return "file_info", 92.0, [("file_info", 92.0)]

    # â”€â”€ Tier 0.145: "rollback/revert/undo to <target>" â†’ git, with a target â”€â”€
    # Distinct from bare Tier 0.15 below: this variant names a specific
    # commit to land on ("rollback to commit 3", "revert to the fix commit",
    # "undo to before the merge"), which the git handler needs to route to
    # the confirm-then-reset flow rather than the generic status fallback.
    # Must be checked BEFORE Tier 0.15, since Tier 0.15's bare keyword match
    # would otherwise catch these same directives first (same words) and
    # return before the "to <target>" wording is ever noticed. Confidence is
    # slightly higher than Tier 0.15 since the directive is more specific,
    # but this only decides confidence/tier bucketing â€” the git handler
    # still requires an explicit "yes" before running anything destructive.
    if re.search(r"\b(?:rollback|revert|undo|go\s+back)\b.*(?:\bto\b|\bcommits?\s+ago\b|\bcommit\s*#?\d+\b)", dl):
        return "git", 96.0, [("git", 96.0)]

    # â”€â”€ Tier 0.15: "undo / revert / rollback / go back" â†’ git, not patch â”€â”€
    # These words describe reversing a change that already happened, which
    # is git's job (checkout/reset/revert), not patch's (which proposes a
    # NEW forward-looking edit). Previously "undo my last change" matched
    # "patch" at 100% confidence with no did-you-mean gate at all, since
    # nothing in the vocab connected "undo" to git.
    #
    # "go back (to before...)" was a later-discovered gap in this same
    # family â€” smoke-testing found it landing on "patch" at 100% confidence
    # (worse than the original bug: no did-you-mean gate at that score).
    # Added here rather than as a new tier since it's the same intent.
    if re.search(r"\b(?:undo|revert|rollback|roll\s+back|discard|go\s+back)\b", dl):
        return "git", 95.0, [("git", 95.0)]

    # â”€â”€ Tier 0.165: bare "commit/push/checkpoint [file]" â†’ git â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # The vocab's git require_any needs the exact phrase "git commit" etc.
    # A bare "commit patchit.py please" correctly gets BLOCKED from the
    # Tier 0.1 file_info shortcut (via _STRONG_OVERRIDE_KEYWORDS above,
    # which is real and working) but was never actually caught anywhere
    # afterward â€” it fell through every tier and landed on "patch" at 0%
    # confidence. This was previously assumed fixed in the log ("correctly
    # stays out of the file_info shortcut... should route to git via the
    # commit keyword") but that was never actually verified by running it;
    # smoke-testing caught the gap.
    if re.search(r"\b(?:commit|push|checkpoint)\b", dl) and not re.search(r"\bdid\s+i\b", dl):
        return "git", 88.0, [("git", 88.0)]

    # â”€â”€ Tier 0.166: bare "commits" / "show commits" / "commit history" â†’ git â”€â”€
    # NOT already covered by Tier 0.165 above: that tier's regex is
    # `\bcommit\b`, and \b requires a word boundary immediately after
    # "commit" â€” which never exists in "commits" (the "s" is still a word
    # character, so there's no boundary between "commit" and "s"). Verified
    # directly: re.search(r"\bcommit\b", "show commits") does not match.
    # So "commits" / "show commits" / "commit history" fell through every
    # tier with no keyword path at all, landing wherever Tier 2's fuzzy
    # fallback happened to guess. This tier catches the plural/noun form
    # explicitly. ("did i commit" etc. is excluded â€” that's Tier 0.17's
    # state-check phrasing, not a request to see the log.)
    if re.search(r"\b(?:commits?|commit\s+history)\b", dl) and not re.search(r"\bdid\s+i\b", dl):
        return "git", 90.0, [("git", 90.0)]

    # â”€â”€ Tier 0.17: "did I already [X]" â†’ state-check â†’ git status â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # "did I save", "did I already run this") â€” informational, never an
    # edit. Smoke-testing found ALL of these landing on "patch" at 0%
    # confidence (a random no-match guess), the worst kind of miss since
    # it's silent and gives no useful signal to the did-you-mean gate.
    # Route to git status: it's the closest honest answer pyslick can give
    # without actually knowing what "this" refers to for save/run.
    if re.search(r"\bdid\s+i\s+(?:already\s+)?(?:commit|save|push|checkpoint)\b", dl):
        return "git", 90.0, [("git", 90.0)]

    # â”€â”€ Tier 0.18: "what happens when I [X]" â†’ behavioral question â”€â”€â”€â”€â”€â”€â”€â”€
    # Smoke-testing found these landing on "patch" at 0% confidence two out
    # of three times â€” a generic-phrasing gap, same root cause as the
    # original #3 log case (no keyword path recognizes this phrasing at
    # all). This does not solve true behavioral-semantics matching (that's
    # the same fragile-semantic-search ceiling documented elsewhere) â€” it
    # only makes sure the question routes to run_info's app-summary/file
    # lookup instead of a silent 0%-confidence "patch" guess, which is a
    # strictly more honest failure mode even when it can't fully answer.
    if re.search(r"\bwhat\s+happens\s+(?:when|if)\s+i\b", dl) and not (has_bare_file_ref or has_explicit_file_ref):
        return "run_info", 70.0, [("run_info", 70.0)]

    # â”€â”€ Tier 0.19: "what's the deal with [X]" â†’ nearest / concept lookup â”€â”€
    # Previously this phrase only reached "nearest" by accident (vocab
    # overlap with "closest"/"nearest" words in some cases) â€” smoke-testing
    # showed "what's the deal with watch mode" falling to "patch" at 0%
    # instead. Route the phrase itself to "nearest" explicitly so the
    # concept-summary fallback (folder/file comment scan) always gets a
    # chance to run, regardless of what follows "the deal with".
    if re.search(r"what'?s?\s+the\s+deal\s+with\b", dl):
        return "nearest", 80.0, [("nearest", 80.0)]

    # â”€â”€ Tier 0.20: "why is [this file/it] so big/huge" with NO filename â”€â”€
    # Tier 0.1 already routes "why is patchit.py so huge" to file_info when
    # a real filename is present. This covers the no-filename case ("why
    # is this file so big") which previously fell through everything and
    # landed on "patch" at 0% confidence. file_info's handler falls back
    # to the project's single most-connected file when nothing is matched.
    if re.search(r"\bwhy\b.*\b(?:so\s+)?(?:big|huge|large|long)\b", dl) and not (has_bare_file_ref or has_explicit_file_ref):
        return "file_info", 75.0, [("file_info", 75.0)]

    # â”€â”€ Tier 0.16: "broken / errors / bugs" â†’ syntax_check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # syntax_check actually runs ast.parse() (or jsx_tag_checker for
    # JS/HTML) and reports real line-numbered errors â€” a genuinely correct
    # answer to "is there anything broken in X". Previously this had no
    # explicit keyword weight and lost to scan_function's looser fuzzy
    # match, which then searched for a function literally named "is there
    # anything broken" and returned an unrelated coincidental match
    # instead of actually checking the file for errors.
    #
    # NOTE: "broken"/"errors"/etc. are ALSO in _STRONG_OVERRIDE_KEYWORDS
    # above, specifically so Tier 0.1's bare-filename shortcut doesn't
    # claim these directives first â€” that ordering bug was caught by
    # simulation before shipping (a directive like "is there anything
    # broken in query.py" has a bare file ref AND the word "broken"; if
    # "broken" weren't an override keyword, Tier 0.1 would return
    # file_info before this tier ever ran).
    if has_bare_file_ref or has_explicit_file_ref:
        if re.search(r"\b(?:broken|errors?|bugs?|syntax\s*error|crash(?:ing|es)?|fail(?:ing|s)?)\b", dl):
            return "syntax_check", 90.0, [("syntax_check", 90.0)]

    # â”€â”€ Tier 0.5: Rule â€” "show me / list / all <filetype/lang> files" always looks for filetype first â”€
    import re as _re_t1
    _FT_KEYWORDS = {
        "js", "javascript", "script", "scripts",
        "py", "python",
        "ts", "typescript", "tsx", "jsx",
        "c", "cpp", "c++", "cs", "csharp",
        "env", ".env", "environment",
        "html", "css", "scss", "sass", "vue", "svelte",
        "java", "kt", "kotlin", "scala",
        "rs", "rust", "go", "golang", "swift",
        "rb", "ruby", "php", "lua", "dart", "flutter", "zig",
        "r", "julia", "jl", "elixir", "ex", "clojure", "clj", "haskell", "hs",
        "sh", "bash", "ps1", "powershell", "bat", "cmd",
        "json", "yaml", "yml", "toml", "sql", "md", "markdown", "graphql", "proto"
    }
    _FT_PREFIXES = ("show me", "show", "list", "all", "find", "get", "display")
    dl_words = set(_re_t1.findall(r"[a-zA-Z0-9+#.]+", dl))
    has_ft_prefix = any(p in dl for p in _FT_PREFIXES)
    has_ft_word = bool(dl_words & _FT_KEYWORDS)
    _LINE_FUNC_KEYWORDS = [
        r"\bline\b", r"\blines\b", r"\bcomment\b", r"\bcomments\b",
        r"\bfunction\b", r"\bdef\b", r"\bmethod\b", r"\bclass\b",
        r"\bcommit\b", r"\bpush\b", r"\bgraph\b", r"\bcheckpoint\b",
        r"\bdesign\b", r"\bwebpage\b", r"\barchitecture\b",
        r"\bui\b", r"\bcomponent\b", r"\bcomponents\b", r"\blayout\b",
        r"\bappearance\b", r"\binterface\b", r"\bstyle\b", r"\bstyles\b", r"\bvisual\b",
    ]
    is_line_or_func = any(_re_t1.search(kw, dl) for kw in _LINE_FUNC_KEYWORDS)

    # Dynamic regex: 'show me all kotlin files', 'list zig files', 'all lua scripts', etc.
    _STOP_WORDS_DYNAMIC = {
        "me", "the", "a", "an", "all", "some", "our", "my", "this", "that",
        "code", "design", "page", "webpage", "entire", "show", "get", "find", "list",
        "ui", "component", "components", "layout", "appearance", "interface", "style", "styles", "visual"
    }
    dynamic_match = _re_t1.search(
        r'(?:show\s+me|show|list|all|find|display|get)\s+(?:all\s+)?(?:the\s+)?([a-zA-Z0-9+#.]+)\s+(?:files|scripts|sources|docs)\b',
        dl
    )
    dyn_lang = dynamic_match.group(1).lower().lstrip(".") if dynamic_match else ""
    dynamic_files_match = bool(dyn_lang and dyn_lang not in _STOP_WORDS_DYNAMIC)

    # Dot extension syntax: 'show .kt', 'show *.kt', 'list .env', 'show .js', 'all .tsx'
    dot_ext_match = bool(_re_t1.search(
        r'(?:show\s+me|show|list|all|find|display|get)\s+(?:\*\s*)?\.([a-zA-Z0-9_\-]+)',
        dl
    ))

    if (has_ft_prefix and has_ft_word and not is_line_or_func) or (dynamic_files_match and not is_line_or_func) or (dot_ext_match and not is_line_or_func):
        return "list_files", 100.0, [("list_files", 100.0)]

    # â”€â”€ Tier 0.53: Web / UI / Visual Design Early Route â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _DESIGN_EARLY_TRIGGERS = {
        "ui components", "ui component", "visual design", "digital layout",
        "appearance", "user interface", "web design", "website design",
        "webpage design", "page design", "design code", "code design",
        "design architecture", "ui architecture", "component design",
        "design system", "visual styling", "component styles", "css styles",
    }
    if any(t in dl for t in _DESIGN_EARLY_TRIGGERS):
        return "graph", 100.0, [("graph", 100.0)]

    # â”€â”€ Tier 0.54: General Recon / Multi-Target Early Route â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _RECON_EARLY_TRIGGERS = {
        "find functions and files", "functions and files", "files and functions",
        "nearest file names", "nearest functions", "find nearest file names",
        "find nearest functions", "graphify the nodes", "graphify nodes",
        "general recon", "find files and functions"
    }
    if any(t in dl for t in _RECON_EARLY_TRIGGERS):
        return "nearest", 100.0, [("nearest", 100.0)]

    # â”€â”€ Tier 0.55: App-Summary Early Exit â€” wins before file_info "what does" can steal it â”€
    _APP_SUMMARY_EARLY = {
        "what does this app", "what does this project", "what does this repo",
        "what does this program", "what does this codebase", "what does this do",
        "what does the app", "what does the project", "what does the program",
        "what does the repo", "what does the codebase",
        "what is this app", "what is this project", "what is this repo",
        "what is this program", "what is this codebase",
        "what is this for", "what does it do",
        "describe the app", "describe the project", "describe this project",
        "describe this app", "describe this program", "describe this repo",
        "overview of the app", "overview of the project", "overview of this",
        "app overview", "project overview", "app summary", "project summary",
        "summarize this project", "summarize this app", "summarize this program",
        "purpose of this app", "purpose of this project", "purpose of this program",
        "whats this", "what is this", "whats this project", "whats this app",
        "whats this repo", "whats this codebase", "whats this program",
        "what's this", "what's this project", "what's this app", "what's this program",
    }
    if any(t in dl for t in _APP_SUMMARY_EARLY):
        return "run_info", 100.0, [("run_info", 100.0)]

    # â”€â”€ Tier 0.6: File-First Rule â€” "show me / where is / find <filename>" matches project files first â”€
    _SHOW_FILE_PREFIXES = (
        "show me", "show", "cat", "scan", "open", "read", "view", "display",
        "what is in", "contents of",
        "where is", "where's", "locate", "find me",
    )
    _WHERE_IS_PREFIXES = ("where is", "where's", "locate", "find me")
    has_show_prefix = any(p in dl for p in _SHOW_FILE_PREFIXES)
    has_where_prefix = any(p in dl for p in _WHERE_IS_PREFIXES)

    # Strip navigation prefixes to isolate the target filename
    _prefix_strip_re = _re_t1.compile(
        r'^(?:show\s+me|show|cat|scan|open|read|view|display'
        r'|what\s+is\s+in|contents\s+of'
        r'|where\s+is|where\'s|locate|find\s+me)'
        r'\s+(?:the\s+)?'
    )
    # Also strip trailing location words like "located", "stored", "saved"
    _suffix_strip_re = _re_t1.compile(r'\s+(?:located|stored|saved|at|in|found)\s*$')

    if has_show_prefix and not is_line_or_func:
        clean_target = _prefix_strip_re.sub('', dl).strip()
        clean_target = _suffix_strip_re.sub('', clean_target).strip()
        clean_target_no_space = clean_target.replace(" ", "").replace(".", "").lower()

        _KNOWN_FILE_NAMES = {
            "gitignore", ".gitignore", "packagejson", "package.json", "package-lock", "package-lock.json",
            "pnpm-lock", "pnpm-lock.yaml", "yarn.lock", "tsconfig", "tsconfig.json",
            "pyproject", "pyproject.toml", "requirements", "requirements.txt", "setup.py",
            "cargo.toml", "go.mod", "go.sum", "gemfile", "dockerfile", "makefile",
            "license", "readme", "readme.md", "changelog", "contributing",
            "env", ".env", "dockerignore", ".dockerignore", "editorconfig", ".editorconfig",
            "eslintrc", ".eslintrc", "prettierrc", ".prettierrc", "babelrc", ".babelrc",
            # pyslick-specific known files
            "graphify.md", "graphify", "graphifymd",
            "agent.py", "intentvocabjson", "intent_vocab.json",
        }

        if clean_target in _KNOWN_FILE_NAMES or clean_target_no_space in _KNOWN_FILE_NAMES:
            return "file_info", 100.0, [("file_info", 100.0)]

        if clean_target:
            all_proj_files = _collect_all_files(".")
            for pf in all_proj_files:
                bname = os.path.basename(pf).lower()
                stem = Path(pf).stem.lower()
                bname_no_dot = bname.lstrip(".").lower()
                bname_clean = bname.replace(".", "").replace("-", "").replace("_", "")

                if (clean_target == bname or
                    clean_target == bname_no_dot or
                    clean_target == stem or
                    clean_target_no_space == bname_clean or
                    (len(clean_target) >= 4 and clean_target == bname_clean)):
                    return "file_info", 100.0, [("file_info", 100.0)]

    # â”€â”€ Tier 1: exact vocab match â€” word-boundary check for patterns â”€
    for intent_name in priority:
        cfg = intents.get(intent_name, {})
        patterns = cfg.get("require_any", [])
        excludes = cfg.get("exclude_if", [])
        # Check excludes first (still plain substring â€” exclusions are always multi-word)
        if any(ex in dl for ex in excludes):
            continue
        for p in patterns:
            pattern_regex = r'\b' + r'\s+'.join(_re_t1.escape(w) for w in p.split()) + r'\b'
            matched = bool(_re_t1.search(pattern_regex, dl))
            if matched:
                return intent_name, 100.0, [(intent_name, 100.0)]

    # â”€â”€ Tier 2: Python fuzzy â€” word overlap & token scoring Ã— intent weight â”€
    all_scores: list[tuple[str, float]] = []
    try:
        from rapidfuzz import fuzz
        import re as _re
        dl_words = _re.findall(r'\b\w+\b', dl)
        user_weights = _load_user_weights()

        for intent_name in priority:
            cfg = intents.get(intent_name, {})
            patterns = cfg.get("require_any", [])
            excludes = cfg.get("exclude_if", [])
            base_w = float(cfg.get("weight", 1.0))
            weight = float(user_weights.get(intent_name, base_w))

            if any(ex in dl for ex in excludes):
                continue

            best_raw = 0.0
            for pat in patterns:
                pat_l = pat.lower()
                if " " in pat_l:
                    pat_words = set(_re.findall(r'\b\w+\b', pat_l))
                    dl_word_set = set(dl_words)
                    matched_words = sum(
                        1 for pw in pat_words
                        if any(fuzz.ratio(pw, dw) >= 80 for dw in dl_word_set)
                    )
                    overlap = matched_words / max(1, len(pat_words))
                    if overlap >= 0.5:
                        token_score = fuzz.token_sort_ratio(pat_l, dl)
                        score = (overlap * 100 * 0.7) + (token_score * 0.3)
                    else:
                        score = 0.0
                else:
                    score = max((fuzz.ratio(pat_l, w) for w in dl_words), default=0.0) if dl_words else fuzz.ratio(pat_l, dl)

                if score > best_raw:
                    best_raw = score

            # Cap weighted score at 100 so callers can treat it as a percentage
            all_scores.append((intent_name, min(100.0, best_raw * weight)))

        all_scores.sort(key=lambda x: x[1], reverse=True)
        top3 = all_scores[:3]
        best_intent, best_score = all_scores[0]

        # High confidence â€” trust Python, no LLM needed
        if best_score >= 85.0:
            return best_intent, best_score, top3

        # â”€â”€ Tier 3: LLM arbitration â€” only in the uncertain 55-84% zone â”€â”€â”€â”€â”€â”€
        if 55.0 <= best_score < 85.0:
            try:
                from llm import maybe_classify_intent
                # Send top-3 intent candidates + their vocab examples to the LLM
                # (not all 11 â€” keep the prompt small for the 124M model)
                top_names = [name for name, _ in top3]
                intent_examples = {
                    name: intents.get(name, {}).get("require_any", [])[:4]
                    for name in top_names
                }
                llm_result = maybe_classify_intent(directive, intent_examples)
                if llm_result:
                    llm_intent, llm_conf = llm_result
                    # LLM wins if it agrees or scores higher than Python's fuzzy
                    if llm_conf >= best_score:
                        return llm_intent, float(llm_conf), top3

                # Binary yes/no verification for borderline candidates
                try:
                    from llm import verify_intent_yes_no
                    for cand_name, cand_score in top3[:2]:
                        if cand_score >= 50.0:
                            cand_examples = intents.get(cand_name, {}).get("require_any", [])[:4]
                            if cand_examples and verify_intent_yes_no(directive, cand_name, cand_examples):
                                return cand_name, 90.0, top3
                except (ImportError, Exception):
                    pass
            except ImportError:
                pass

        return best_intent, best_score, top3

    except ImportError:
        pass

    return "patch", 0.0, [("patch", 0.0)]


def _collect_all_files(root: str = ".") -> list[str]:
    """Walk project tree and return all source file paths (skips binary, build, and backup files)."""
    _KNOWN_PROJECT_FILES = {
        ".gitignore", ".env", ".env.local", ".env.example", ".env.development",
        ".dockerignore", "dockerfile", "makefile", "license", "readme", "readme.md",
        ".editorconfig", ".prettierrc", ".eslintrc", ".eslintrc.json", ".babelrc",
        "package.json", "tsconfig.json", "pyproject.toml", "cargo.toml", "go.mod"
    }
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith(".")
            and not d.startswith("pyslick_backup")
            and d != "graphify-out"
        ]
        for fn in sorted(filenames):
            fn_l = fn.lower()
            ext = Path(fn).suffix.lower()
            is_dotfile = fn_l.startswith(".")
            is_known = fn_l in _KNOWN_PROJECT_FILES or any(fn_l.startswith(k) for k in [".env", ".git", "dockerfile", "makefile", "license", "readme"])
            if ext in CODE_EXTS or is_known or (ext == "" and not is_dotfile):
                files.append(os.path.normpath(os.path.join(dirpath, fn)))
    return files


def _first_comment_lines(filepath: str, max_lines: int = 5) -> list[str]:
    """Return the first meaningful comment/docstring lines from a file (up to max_lines)."""
    try:
        raw = Path(filepath).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out: list[str] = []
    in_docstring = False
    docstring_quote = None
    for ln in raw[:120]:
        s = ln.strip()

        # Shebang lines ("#!/usr/bin/env python3") are never a description â€”
        # skip entirely rather than cleaning them into "usr/bin/env python3".
        # This was the actual bug: '/' was in the strip-charset, so the
        # leading '/' of '/usr/bin/env' got stripped along with '#!', and
        # the cleaned shebang text got kept as if it were the file's
        # opening comment â€” before the real docstring below it was ever read.
        if s.startswith("#!"):
            continue

        if in_docstring:
            if docstring_quote in s:
                # Closing line â€” take any text before the closing quote
                before = s.split(docstring_quote)[0].strip()
                if before and len(before) >= 5:
                    out.append(before)
                in_docstring = False
                if len(out) >= max_lines:
                    break
                continue
            if len(s) >= 5 and not all(c in "-=_/*~#â”‚â”€" for c in s):
                out.append(s)
                if len(out) >= max_lines:
                    break
            continue

        if not s:
            if out:
                break
            continue

        if s.startswith(('"""', "'''")):
            docstring_quote = s[:3]
            rest = s[3:]
            if docstring_quote in rest:
                # Single-line docstring: """like this"""
                text = rest.split(docstring_quote)[0].strip()
                if len(text) >= 5:
                    out.append(text)
                    if len(out) >= max_lines:
                        break
                continue
            rest = rest.strip()
            if len(rest) >= 5:
                out.append(rest)
                if len(out) >= max_lines:
                    break
            in_docstring = True
            continue

        if s.startswith(("//", "#", "/*", "*", "<!--")):
            cleaned = s.lstrip("/*#<>-= ").strip()
            if len(cleaned) < 5:
                continue
            if all(c in "-=_/*~#â”‚â”€" for c in cleaned):
                continue
            out.append(cleaned)
            if len(out) >= max_lines:
                break
        elif out:
            break  # first non-comment line after comments â†’ stop
    return out


def _extract_folder_or_file_target(directive: str, all_files: list[str]) -> tuple:
    """
    Best-effort: does the directive name a real folder or a real file that
    exists in the project? Returns (folder_path_or_None, file_path_or_None).
    Deliberately conservative â€” only returns a hit when something on disk
    actually matches, never a guess.
    """
    tokens = re.findall(r"[a-zA-Z0-9_\-./\\]+", directive)
    known_dirs = sorted({os.path.dirname(f) for f in all_files if os.path.dirname(f)})

    # Folder match: a token equals (or path-matches) a real directory name
    for tok in tokens:
        tok_clean = tok.strip("/\\").rstrip("/\\")
        if not tok_clean or len(tok_clean) < 2:
            continue
        for d in known_dirs:
            if d == tok_clean or os.path.basename(d) == tok_clean:
                return d, None

    # File match: a token's basename matches a real file exactly
    known_basenames = {os.path.basename(f).lower(): f for f in all_files}
    for tok in tokens:
        base = os.path.basename(tok).lower()
        if base in known_basenames:
            return None, known_basenames[base]

    return None, None


def _extract_exact_path(directive: str, all_files: list[str]) -> str | None:
    """If the directive contains a token that resolves to a real file on
    disk (relative to cwd), return that path. Handles:
      - src/app/api/publish/route.ts
      - ./src/app/... and .\\src\\app\\...
      - /src/app/... (leading slash, Windows-safe: strip it)
      - bare filename if unique across the project
    Returns None if nothing on disk matches. Deliberately conservative —
    never guesses. If multiple paths are named, returns the first one."""
    if not directive:
        return None

    # Normalise all known files to forward-slash relative form for comparison.
    root = os.getcwd()
    known: dict[str, str] = {}
    for f in all_files:
        rel = os.path.relpath(f, root).replace("\\", "/")
        known[rel.lower()] = f
        # also index by suffix so src/app/api/publish/route.ts matches
        # when the user typed that exact tail
        known.setdefault(rel.lower(), f)

    # Pull every token that looks like a path or filename.
    tokens = re.findall(r"[A-Za-z0-9_\-./\\]+", directive)
    # Sort by descending length so the longest (most specific) token wins
    # before shorter substrings.
    tokens.sort(key=len, reverse=True)

    for tok in tokens:
        if not tok or not any(c in tok for c in "./\\"):
            continue
        norm = tok.replace("\\", "/").lstrip("./").lstrip("/")
        if not norm:
            continue
        # exact relative match
        if norm.lower() in known:
            return known[norm.lower()]
        # suffix match: user typed the tail of the path
        for rel_lower, real in known.items():
            if rel_lower.endswith("/" + norm.lower()) or rel_lower == norm.lower():
                return real

    # Bare-filename fallback: only if unique.
    basenames: dict[str, list[str]] = {}
    for f in all_files:
        basenames.setdefault(os.path.basename(f).lower(), []).append(f)
    for tok in tokens:
        base = tok.lower()
        if base in basenames and len(basenames[base]) == 1:
            return basenames[base][0]

    return None


def _top_connected_files_in(folder: str, top_n: int = 3) -> list[str]:
    """
    Rank files within `folder` by connectivity. Prefers repomap's real
    import/reference ranking; falls back to plain file count if repomap
    isn't importable or the folder is too small to rank meaningfully.
    """
    try:
        from repomap import rank_top_files
        ranked = rank_top_files(folder, top_n=top_n)
        files = [f for f, _score in ranked]
        if files:
            return files
    except Exception:
        pass
    # Fallback: just take the first top_n source files found, largest first
    # (a rough proxy for "more going on" when real ranking isn't available)
    candidates = _collect_all_files(folder)
    try:
        candidates.sort(key=lambda f: os.path.getsize(f), reverse=True)
    except Exception:
        pass
    return candidates[:top_n]


def _print_concept_summary_for_folder(folder: str, all_files: list[str]) -> None:
    """Top-3 most-connected files in `folder`, first 10 comment lines each."""
    top_files = _top_connected_files_in(folder, top_n=3)
    if not top_files:
        print(f"  {DIM}No source files found under '{folder}'.{RST}")
        return
    print(f"  {CYAN}Top {len(top_files)} most-connected files in {folder}:{RST}\n")
    for f in top_files:
        print(f"  {BOLD}{f}{RST}")
        comments = _first_comment_lines(f, max_lines=10)
        if comments:
            for c in comments:
                print(f"    {DIM}â†’ {c}{RST}")
        else:
            print(f"    {DIM}(no opening comments){RST}")
        print()


def _print_concept_summary_for_file(filepath: str) -> None:
    """A single file's first 5 comment lines."""
    print(f"  {BOLD}{filepath}{RST}")
    comments = _first_comment_lines(filepath, max_lines=5)
    if comments:
        for c in comments:
            print(f"    {DIM}â†’ {c}{RST}")
    else:
        print(f"    {DIM}(no opening comments){RST}")


def _graph_functions_with_comments(filepath: str) -> list[dict]:
    """
    Stdlib-only (ast module) function/class inventory for a .py file, each
    with its size and the comment (docstring, or a leading '#' comment
    directly above it) that explains it. No external deps (graphify /
    rapidfuzz not required) â€” this is the whole point: answer "why is this
    file so big" by showing its structure and comments, not its full body.
    Non-Python files return an empty list; caller falls back to
    _first_comment_lines for those.
    """
    if not filepath.endswith(".py"):
        return []
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=filepath)
    except Exception:
        return []

    raw_lines = source.splitlines()
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            size = end - start + 1
            doc = ast.get_docstring(node)
            comment = None
            if doc:
                comment = doc.strip().splitlines()[0][:100]
            else:
                # Look at the line(s) directly above the def/class for a
                # leading '#' comment, same convention as _first_comment_lines.
                above = start - 2  # -1 for 0-index, -1 again for "line above"
                if 0 <= above < len(raw_lines):
                    s = raw_lines[above].strip()
                    if s.startswith("#"):
                        comment = s.lstrip("# ").strip()
            nodes.append({
                "name": node.name,
                "kind": type(node).__name__.replace("Def", "").replace("Async", "async "),
                "start": start,
                "end": end,
                "size": size,
                "comment": comment,
            })
    nodes.sort(key=lambda n: -n["size"])
    return nodes


def _rank_functions_by_connectivity(filepath: str, top_n: int = 3) -> list[dict]:
    """
    Stdlib-only (ast module) in-file call-graph ranking: how many times each
    function/method defined in this file is called by other code in the
    SAME file. This is a real count of ast.Call nodes whose callee name
    matches a def in this file â€” not a size proxy, not a guess. Cross-file
    calls aren't tracked (that needs project-wide symbol resolution, which
    is exactly the graphify/rapidfuzz territory that isn't available in
    every environment) â€” this is deliberately scoped to what stdlib ast
    can answer honestly on its own.
    """
    if not filepath.endswith(".py"):
        return []
    try:
        source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=filepath)
    except Exception:
        return []

    defined_names = set()
    def_nodes = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_names.add(node.name)
            def_nodes[node.name] = node

    call_counts = {name: 0 for name in defined_names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = None
            if isinstance(node.func, ast.Name):
                callee = node.func.id
            elif isinstance(node.func, ast.Attribute):
                callee = node.func.attr
            if callee in call_counts:
                call_counts[callee] += 1

    ranked = []
    for name, count in call_counts.items():
        fn_node = def_nodes[name]
        # A function calling itself (recursion) still counts, but subtract
        # 1 self-call from "called by others" so a purely-recursive helper
        # with no external callers doesn't look falsely well-connected.
        self_calls = sum(
            1 for n in ast.walk(fn_node)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == name)
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == name))
        )
        external_count = max(0, count - self_calls)
        doc = ast.get_docstring(fn_node)
        comment = doc.strip().splitlines()[0][:100] if doc else None
        ranked.append({
            "name": name,
            "start": fn_node.lineno,
            "end": getattr(fn_node, "end_lineno", fn_node.lineno),
            "callers_in_file": external_count,
            "comment": comment,
        })

    ranked.sort(key=lambda n: -n["callers_in_file"])
    return [r for r in ranked if r["callers_in_file"] > 0][:top_n]


def _print_file_size_graph(filepath: str) -> None:
    """
    Answer "why is X so big": function/class breakdown by size, largest
    first, with each one's comment/docstring â€” not a full-file dump.
    """
    total_lines = 0
    try:
        total_lines = len(Path(filepath).read_text(encoding="utf-8", errors="replace").splitlines())
    except Exception:
        pass

    print(f"  {BOLD}{filepath}{RST}  {DIM}({total_lines} lines total){RST}\n")

    nodes = _graph_functions_with_comments(filepath)
    if nodes:
        print(f"  {CYAN}Functions/classes by size (largest first):{RST}\n")
        for n in nodes[:15]:
            print(f"  {BOLD}{n['kind']:5s}{RST} {n['name']:<30s} "
                  f"{DIM}L{n['start']}-{n['end']} ({n['size']} lines){RST}")
            if n["comment"]:
                print(f"        {DIM}â†’ {n['comment']}{RST}")
        if len(nodes) > 15:
            print(f"\n  {DIM}...and {len(nodes) - 15} more.{RST}")
    else:
        # Non-Python file, or nothing parsed â€” fall back to opening comments
        print(f"  {DIM}(no Python function/class structure to graph â€” showing opening comments){RST}\n")
        comments = _first_comment_lines(filepath, max_lines=10)
        for c in comments:
            print(f"    {DIM}â†’ {c}{RST}")


def _load_graphify_semantic_index() -> dict:
    """
    Parse graphify.md (or graphify-out/) in current directory to build an active semantic index.
    Returns a dict with:
      - 'symbols': list of (symbol_label, source_file, refs, line_info, community)
      - 'file_communities': dict mapping filename -> list of community tags
      - 'god_nodes': dict mapping filename -> degree
    """
    index = {
        "symbols": [],
        "file_communities": {},
        "god_nodes": {},
    }

    graphify_md = "graphify.md"
    if os.path.exists(graphify_md):
        try:
            content = Path(graphify_md).read_text(encoding="utf-8", errors="replace")
            curr_comm = ""
            for line in content.splitlines():
                # Community header: ### Community 0 â€” `package.json`  *(cohesion 0.15)*
                comm_m = re.search(r"###\s+(Community\s+\w+)\s+â€”\s+`([^`]+)`(?:\s+\*\(cohesion\s+([\d.]+)\)\*)?", line)
                if comm_m:
                    c_id = comm_m.group(1)
                    c_file = comm_m.group(2)
                    c_coh = comm_m.group(3) or ""
                    tag = f"{c_id} ({c_file})" + (f" Â· cohesion {c_coh}" if c_coh else "")
                    curr_comm = tag
                    base_f = os.path.basename(c_file).lower()
                    index["file_communities"].setdefault(base_f, []).append(curr_comm)
                    continue

                # Member node: - **setContentProtection()** (3 refs) Â· `electron-main.js` L11
                node_m = re.search(r"^-\s+\*\*([^*]+)\*\*(?:\s+\((\d+)\s+refs\))?(?:\s+Â·\s+`([^`]+)`(?:\s+L(\d+))?)?", line)
                if node_m:
                    label = node_m.group(1).strip()
                    refs = int(node_m.group(2) or "1")
                    src_file = node_m.group(3) or ""
                    line_num = int(node_m.group(4) or "0") if node_m.group(4) else None
                    if src_file:
                        index["symbols"].append((label, src_file, refs, line_num, curr_comm))
                        base_sf = os.path.basename(src_file).lower()
                        if curr_comm and curr_comm not in index["file_communities"].get(base_sf, []):
                            index["file_communities"].setdefault(base_sf, []).append(curr_comm)

                # God nodes table: | `package_build` | build | 8 | `package.json` |
                god_m = re.search(r"^\|\s*`[^`]+`\s*\|\s*([^|]+)\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|", line)
                if god_m:
                    g_lbl = god_m.group(1).strip()
                    g_deg = int(god_m.group(2).strip())
                    g_file = god_m.group(3).strip()
                    base_gf = os.path.basename(g_file).lower()
                    index["god_nodes"][base_gf] = max(index["god_nodes"].get(base_gf, 0), g_deg)
                    index["symbols"].append((g_lbl, g_file, g_deg, None, "God Node"))
        except Exception:
            pass

    return index


def _find_encapsulating_scope(content: str, line_num: int, filepath: str) -> dict:
    """
    Finds the enclosing function, class, struct, method, or code block that encapsulates
    a given 1-based line number in a file.
    """
    lines = content.splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return {"scope_name": os.path.basename(filepath), "scope_type": "file", "start_line": 1, "end_line": 1, "snippet": []}

    line_num = max(1, min(line_num, total_lines))
    idx = line_num - 1
    ext = Path(filepath).suffix.lower()

    # 1. Python scope resolution (indentation-based)
    if ext == ".py":
        scope_idx = None
        scope_indent = 0
        scope_name = None
        scope_type = "function"

        for i in range(idx, -1, -1):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r"^(\s*)(?:async\s+)?(def|class)\s+([a-zA-Z0-9_]+)", line)
            if m:
                indent = len(m.group(1))
                cur_line_indent = len(lines[idx]) - len(lines[idx].lstrip())
                if i == idx or cur_line_indent > indent or line_num == i + 1:
                    scope_idx = i
                    scope_indent = indent
                    scope_type = m.group(2)
                    scope_name = f"{m.group(2)} {m.group(3)}"
                    break

        if scope_idx is not None:
            start_l = scope_idx + 1
            end_l = total_lines
            for j in range(scope_idx + 1, total_lines):
                cur_l = lines[j]
                cur_str = cur_l.strip()
                if not cur_str or cur_str.startswith("#"):
                    continue
                cur_ind = len(cur_l) - len(cur_l.lstrip())
                if cur_ind <= scope_indent:
                    end_l = j
                    break
            return {
                "scope_name": scope_name,
                "scope_type": scope_type,
                "start_line": start_l,
                "end_line": end_l,
                "snippet": lines[start_l - 1:end_l],
            }

    # 2. C / C++ / Java / Kotlin / JS / TS / Rust / Go / C# / PHP (brace & keyword scope)
    scope_idx = None
    scope_name = None
    scope_type = "function"

    func_re = re.compile(
        r"^\s*(?:(?:public|private|protected|static|final|native|synchronized|abstract|export|async|default)\s+)*"
        r"(?:def|class|function|fun|func|struct|interface|impl|enum|trait|void|int|bool|string|auto|const)\s+([a-zA-Z0-9_$]+)"
        r"|^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>"
        r"|^\s*(?:fun|func)\s+([a-zA-Z0-9_$]+)"
        r"|^\s*([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*\{"
    )

    for i in range(idx, -1, -1):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "#")):
            continue
        m = func_re.search(line)
        if m:
            matched_sym = next((g for g in m.groups() if g), "block")
            scope_idx = i
            scope_name = stripped.split("{")[0].strip() or matched_sym
            if "class" in line:
                scope_type = "class"
            elif "struct" in line:
                scope_type = "struct"
            else:
                scope_type = "function"
            break

    if scope_idx is not None:
        start_l = scope_idx + 1
        brace_count = 0
        saw_open = False
        end_l = min(total_lines, start_l + 45)
        for j in range(scope_idx, total_lines):
            for ch in lines[j]:
                if ch == "{":
                    brace_count += 1
                    saw_open = True
                elif ch == "}":
                    brace_count -= 1
                    if saw_open and brace_count == 0:
                        end_l = j + 1
                        break
            if saw_open and brace_count == 0:
                break
        return {
            "scope_name": scope_name,
            "scope_type": scope_type,
            "start_line": start_l,
            "end_line": max(start_l, end_l),
            "snippet": lines[start_l - 1:max(start_l, end_l)],
        }

    # Fallback to local region around target line
    s = max(1, line_num - 10)
    e = min(total_lines, line_num + 20)
    return {
        "scope_name": os.path.basename(filepath),
        "scope_type": "file",
        "start_line": s,
        "end_line": e,
        "snippet": lines[s - 1:e],
    }


def _collect_all_project_nodes(all_files: list[str] | None = None) -> list[dict]:
    """
    Builds a unified inventory of all graph, AST, comment, and symbol nodes in the project.
    """
    if all_files is None:
        all_files = _collect_all_files()

    nodes: list[dict] = []

    # 1. Try graphify graph.json
    try:
        from find_nearest_nodes import load_graph_nodes
        gnodes = load_graph_nodes()
        if gnodes:
            for gn in gnodes:
                nid = gn.get("id", "")
                fp = gn.get("file") or (nid.split(":")[0] if ":" in nid else "")
                ln = gn.get("start_line", 1)
                if ":" in nid and not gn.get("start_line"):
                    try:
                        ln = int(nid.split(":")[1])
                    except Exception:
                        pass
                nodes.append({
                    "id": nid,
                    "label": gn.get("label", nid),
                    "file": fp,
                    "start_line": ln,
                    "type": gn.get("type", "node"),
                })
    except Exception:
        pass

    # 2. Try graphify.md semantic index
    sem_index = _load_graphify_semantic_index()
    for lbl, sf, refs, l_num, comm in sem_index.get("symbols", []):
        nodes.append({
            "id": f"{sf}:{l_num or 1}",
            "label": lbl,
            "file": sf,
            "start_line": l_num or 1,
            "type": comm or "symbol",
            "refs": refs,
        })

    # 3. Try comment block nodes
    try:
        from comment_blocks import scan_project_for_comment_blocks, comment_nodes_as_graph_nodes
        comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
        cnodes = comment_nodes_as_graph_nodes(comment_nodes_raw)
        if cnodes:
            for cn in cnodes:
                fp = cn.get("file") or (cn["_comment_node"].file if "_comment_node" in cn else "")
                sl = cn.get("start_line") or (cn["_comment_node"].start_line if "_comment_node" in cn else 1)
                el = cn.get("end_line") or (cn["_comment_node"].end_line if "_comment_node" in cn else sl + 10)
                nodes.append({
                    "id": cn.get("id", f"{fp}:{sl}"),
                    "label": cn.get("label", "comment"),
                    "file": fp,
                    "start_line": sl,
                    "end_line": el,
                    "type": "comment_block",
                })
    except Exception:
        pass

    # 4. AST / Regex symbol scan across candidate source files
    if len(nodes) < 20:
        sym_pattern = re.compile(
            r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|fun|func|struct|interface|impl|enum)\s+([a-zA-Z0-9_$]+)"
            r"|^\s*(?:export\s+)?(?:const|let|var|val)\s+([a-zA-Z0-9_$]{3,})\s*[:=]"
        )
        for fp in all_files[:80]:
            content = tool_get_file(fp)
            if content.startswith("ERROR"):
                continue
            for idx, line in enumerate(content.splitlines()):
                m = sym_pattern.search(line)
                if m:
                    sym_lbl = next((g for g in m.groups() if g), None)
                    if sym_lbl and len(sym_lbl) >= 3:
                        nodes.append({
                            "id": f"{fp}:{idx+1}",
                            "label": sym_lbl,
                            "file": fp,
                            "start_line": idx + 1,
                            "type": "symbol",
                        })

    return nodes


def _find_nearest_nodes_with_encapsulation(query: str, all_files: list[str], top_k: int = 3) -> list[dict]:
    """
    Finds nearest nodes to the query across the project, then resolves the encapsulating
    function/class/block for each node with line numbers and preview.
    """
    try:
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio
    except ImportError:
        return []

    nodes = _collect_all_project_nodes(all_files)
    if not nodes:
        return []

    labels = [n.get("label", n.get("id", "")) for n in nodes]
    results = process.extract(query, labels, scorer=WRatio, limit=top_k * 4)

    seen_scopes = set()
    enriched_nodes = []

    for match, score, index in results:
        if score < 45.0:
            continue
        node = nodes[index]
        fp = node.get("file", "")
        # Resolve relative / absolute file
        if fp and not os.path.isabs(fp) and not os.path.exists(fp):
            for real_f in all_files:
                if os.path.basename(real_f).lower() == os.path.basename(fp).lower():
                    fp = real_f
                    break

        if not fp or not os.path.exists(fp):
            continue

        target_l = node.get("start_line", 1)
        content = tool_get_file(fp)
        if content.startswith("ERROR"):
            continue

        scope = _find_encapsulating_scope(content, target_l, fp)
        scope_key = (fp, scope["start_line"], scope["end_line"])
        if scope_key in seen_scopes:
            continue
        seen_scopes.add(scope_key)

        enriched_nodes.append({
            "node": node,
            "score": score,
            "label": node.get("label", match),
            "file": fp,
            "target_line": target_l,
            "scope": scope,
        })
        if len(enriched_nodes) >= top_k:
            break

    return enriched_nodes


def _find_symbol_callers(sym_name: str, current_file: str, all_files: list[str]) -> list[dict]:
    """
    Finds all functions/methods and line numbers end-to-end that call `sym_name` across the codebase.
    """
    clean_sym = re.sub(r"^(?:def|class|function|fun|func|struct|async)\s+", "", sym_name).split("(")[0].strip()
    if not clean_sym or len(clean_sym) < 3:
        return []

    callers: list[dict] = []
    seen = set()
    call_pattern = re.compile(r'\b' + re.escape(clean_sym) + r'\s*(?:\(|\b)')

    for fp in all_files[:60]:
        content = tool_get_file(fp)
        if content.startswith("ERROR"):
            continue
        for idx, line in enumerate(content.splitlines()):
            if re.search(r'^\s*(?:async\s+)?(?:def|class|function|fun|func|struct)\s+' + re.escape(clean_sym), line):
                continue
            if call_pattern.search(line):
                ln = idx + 1
                scope = _find_encapsulating_scope(content, ln, fp)
                key = (fp, scope["scope_name"], scope["start_line"])
                if key not in seen:
                    seen.add(key)
                    callers.append({
                        "caller_name": scope["scope_name"],
                        "file": fp,
                        "call_line": ln,
                        "start_line": scope["start_line"],
                        "end_line": scope["end_line"],
                    })
                    if len(callers) >= 6:
                        break
        if len(callers) >= 6:
            break
    return callers


def _print_encapsulated_node_view(
    item: dict,
    all_files: list[str],
    directive_lower: str = "",
    max_compact_lines: int = 20
) -> None:
    """
    Renders an encapsulated node view. If line count > 20 and not explicitly asking
    for 'end to end' / 'all lines', it compacts the body and displays all callers
    and line numbers end-to-end.
    """
    score = item["score"]
    label = item["label"]
    fp = item["file"]
    scope = item["scope"]
    s_line = scope["start_line"]
    e_line = scope["end_line"]
    s_name = scope["scope_name"]
    s_type = scope["scope_type"]

    is_explicit_all = any(p in directive_lower for p in [
        "end to end", "all lines", "full function", "entire function",
        "whole function", "scan all", "show all lines", "all code"
    ])

    display_scope = s_name if s_name.startswith((s_type, "def ", "class ", "function ", "fun ", "func ", "struct ")) else f"{s_type} {s_name}"
    print(f"\n  {BOLD}[{score:5.1f}%]{RST} {CYAN}{label}{RST} {DIM}â†’ {BOLD}{display_scope}{RST} {DIM}({fp} L{s_line}-L{e_line}){RST}")

    if not fp or not os.path.exists(fp):
        return

    content = tool_get_file(fp)
    if content.startswith("ERROR"):
        return

    raw = content.splitlines()
    actual_end = min(e_line, len(raw))
    total_lines_in_scope = actual_end - s_line + 1
    ps_cmd = f"Get-Content '{fp}' | Select-Object -Skip {max(0, s_line - 1)} -First {total_lines_in_scope}"
    print(f"  {CYAN}PowerShell:{RST} {BOLD}{ps_cmd}{RST}\n")

    if total_lines_in_scope <= max_compact_lines or is_explicit_all:
        for ln_idx in range(max(0, s_line - 1), actual_end):
            ln = ln_idx + 1
            line = raw[ln_idx]
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                print(f"  {DIM}{ln:4d}â”‚{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}â”‚{RST} {line}")
    else:
        # Compact mode for > 20 lines
        for ln_idx in range(max(0, s_line - 1), min(s_line + 4, actual_end)):
            ln = ln_idx + 1
            line = raw[ln_idx]
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                print(f"  {DIM}{ln:4d}â”‚{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}â”‚{RST} {line}")

        hidden_count = max(0, total_lines_in_scope - 7)
        print(f"  {DIM}      â”‚ ... [{hidden_count} lines omitted â€” run with 'end to end' to view all] ...{RST}")

        # Show last 2 lines
        for ln_idx in range(max(s_line + 4, actual_end - 2), actual_end):
            ln = ln_idx + 1
            line = raw[ln_idx]
            print(f"  {DIM}{ln:4d}â”‚{RST} {line}")

    # Display callers end-to-end with line numbers
    callers = _find_symbol_callers(s_name or label, fp, all_files)
    if callers:
        print(f"\n  {DIM}Called by (functions & line numbers end-to-end):{RST}")
        for c in callers:
            c_name = c["caller_name"]
            c_file = os.path.basename(c["file"])
            c_line = c["call_line"]
            c_start = c["start_line"]
            c_end = c["end_line"]
            print(f"    â† {CYAN}{c_name}{RST} in {BOLD}{c_file}{RST} {DIM}(called at L{c_line}, function scope L{c_start}-L{c_end}){RST}")



def _fuzzy_match_files(directive: str, all_files: list[str]) -> list[str]:
    """
    Extract candidate file names from the directive and fuzzy-match them
    against the real file list.
    Prioritizes:
      1. Semantic Graphify Lookup: matches concept/symbol to its source file from graphify.md
      2. Prepositional phrases: 'of <file>', 'in <file>', 'file <file>'
      3. Tokens near the end of the sentence (right-to-left)
      4. Exact and root file matches
    """
    if not all_files:
        return []

    # Tier 0 â€” explicit "/filename" or "/relative/path" token. This is an
    # unambiguous "I mean this exact file" signal from the user, so it
    # skips fuzzy scoring entirely: resolve by exact path/basename match
    # first, then substring-in-path as a fallback, and return immediately
    # if anything resolves. This exists specifically so a directive like
    # "explain /relay/server.js and /electron-main.js" doesn't fall
    # through to fuzzy matching (which can pick the wrong file, or force
    # a picker prompt) when the user already told you exactly which
    # file(s) they mean.
    explicit_refs = re.findall(r"(?<!\S)/([a-zA-Z0-9_./\\\-]+\.[a-zA-Z0-9]+)", directive)
    if explicit_refs:
        resolved = []
        norm_files = {os.path.normpath(f).replace("\\", "/"): f for f in all_files}
        for ref in explicit_refs:
            ref_norm = ref.replace("\\", "/").lstrip("/")
            hit = None
            # exact relative-path match first
            for norm_path, orig in norm_files.items():
                if norm_path == ref_norm or norm_path.endswith("/" + ref_norm):
                    hit = orig
                    break
            # fall back to basename match if no path-level hit
            if hit is None:
                base = os.path.basename(ref_norm)
                for f in all_files:
                    if os.path.basename(f).lower() == base.lower():
                        hit = f
                        break
            if hit and hit not in resolved:
                resolved.append(hit)
        if resolved:
            return resolved

    try:
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio
    except ImportError:
        words = directive.lower().split()
        matched = []
        for w in reversed(words):
            if len(w) < 3:
                continue
            for f in all_files:
                if w in os.path.basename(f).lower() and f not in matched:
                    matched.append(f)
        return matched

    cfg         = _VOCAB.get("file_extract", {})
    strip_words = set(cfg.get("strip_words", []))
    min_len     = cfg.get("min_len", 3)
    min_score   = cfg.get("min_fuzzy_score", 58)
    max_results = cfg.get("max_results", 4)

    # 1. Prepositional extraction â€” matches 'of llmpy', 'in package.json', 'file agent.py'
    prep_matches = re.findall(
        r"(?:of|in|file|from|for|cat|scan|show|open|into|inside|inspect|read|view|with|between|and)\s+([a-zA-Z0-9_.\-\\/]+)",
        directive,
        re.IGNORECASE
    )

    # 2. General tokens
    tokens = [
        w for w in re.split(r"[\s\-_./\\]+", directive.lower())
        if len(w) >= min_len and w not in strip_words
    ]

    candidates: list[tuple[str, float]] = []
    for pm in prep_matches:
        candidates.append((pm.lower(), 30.0))  # strong preposition bonus

    # Tokens ordered right-to-left so end of sentence has higher weight
    for i, t in enumerate(reversed(tokens)):
        pos_bonus = float((i + 1) * 3)
        candidates.append((t, pos_bonus))

    file_scores: dict[str, float] = {}
    basenames = [os.path.basename(f) for f in all_files]

    for cand, bonus in candidates:
        hits = process.extract(cand, basenames, scorer=WRatio, limit=max_results)
        for base_match, score, idx in hits:
            if score >= min_score:
                full_path = all_files[idx]
                total = score + bonus
                # Extra bonus if basename is an exact substring of the directive
                if os.path.basename(full_path).lower() in directive.lower():
                    total += 20.0
                if full_path not in file_scores or total > file_scores[full_path]:
                    file_scores[full_path] = total

    # 3. Semantic Graphify Lookup â€” if graphify.md exists, match query concepts to nodes
    sem_index = _load_graphify_semantic_index()
    for lbl, sf, refs, line_num, comm in sem_index.get("symbols", []):
        lbl_clean = lbl.strip("()").strip("{}").strip().lower()
        if len(lbl_clean) < 3 or lbl_clean in strip_words:
            continue
        if lbl_clean in directive.lower():
            # Find matching file path
            for f in all_files:
                if os.path.basename(f).lower() == os.path.basename(sf).lower():
                    sem_bonus = 50.0 + min(refs * 3.0, 40.0)
                    file_scores[f] = max(file_scores.get(f, 0.0), 75.0 + sem_bonus)
                    break

    # Sort files by calculated score descending, then by shortest path
    sorted_files = sorted(
        file_scores.keys(),
        key=lambda p: (
            file_scores[p],
            -len(Path(p).parts),
            -len(p)
        ),
        reverse=True
    )
    return sorted_files


def _print_file_cat_and_snippet(
    filepath: str,
    directive_lower: str = "",
    start_line: int | None = None,
    end_line: int | None = None
) -> None:
    """
    Print the actual content / lines of a file (cat scan / snippet),
    with PowerShell command recommendations.
    """
    content = tool_get_file(filepath)
    if content.startswith("ERROR"):
        print(f"  {RED}{content}{RST}")
        return

    raw_lines = content.splitlines()
    total_lines = len(raw_lines)

    # 1. Line range requested (e.g. line 30 to 70)
    if start_line is not None and end_line is not None:
        s = max(1, min(start_line, total_lines))
        e = max(s, min(end_line, total_lines))
        count = e - s + 1
        skip = s - 1
        ps_cmd = f"Get-Content '{filepath}' | Select-Object -Skip {skip} -First {count}"

        print(f"  {DIM}Lines: {total_lines}  â”‚  Showing: L{s}-L{e}{RST}")
        print(f"  {CYAN}PowerShell:{RST} {BOLD}{ps_cmd}{RST}\n")

        for idx in range(s - 1, e):
            ln = idx + 1
            line = raw_lines[idx]
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                print(f"  {DIM}{ln:4d}â”‚{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}â”‚{RST} {line}")
        return

    # 2. General file cat scan
    is_json_or_config = filepath.endswith((".json", ".toml", ".yaml", ".yml", ".md", ".txt"))
    is_cat_requested = any(kw in directive_lower for kw in ["cat", "snippet", "scan", "open", "show me", "print", "display", "part of", "packagejson", "package.json"])

    if is_json_or_config or is_cat_requested or total_lines <= 100:
        ps_cmd = f"Get-Content '{filepath}'"
        print(f"  {DIM}Lines: {total_lines}{RST}")
        print(f"  {CYAN}PowerShell:{RST} {BOLD}{ps_cmd}{RST}\n")

        max_show = total_lines if (total_lines <= 100 or "all" in directive_lower or "full" in directive_lower) else 60
        for idx in range(min(max_show, total_lines)):
            ln = idx + 1
            line = raw_lines[idx]
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                print(f"  {DIM}{ln:4d}â”‚{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}â”‚{RST} {line}")

        if total_lines > max_show:
            print(f"\n  {DIM}... ({total_lines - max_show} more lines. Run: Get-Content '{filepath}' or pyslick lines '{filepath}'){RST}")
        return

    # 3. Structured summary for large code files (functions / classes)
    _print_file_summary(filepath, directive_lower)


def _print_file_summary(filepath: str, directive_lower: str = "") -> None:
    """
    Print a structured summary of one file:
      â€¢ total lines
      â€¢ key functions/classes with first 3 lines of body + inline comments
      â€¢ relevant comment blocks (filtered by directive words)
    """
    content = tool_get_file(filepath)
    if content.startswith("ERROR"):
        print(f"  {RED}{content}{RST}")
        return

    raw_lines = content.splitlines()
    print(f"  {DIM}Lines: {len(raw_lines)}{RST}")
    print(f"  {CYAN}PowerShell:{RST} {BOLD}Get-Content '{filepath}'{RST}")

    # â”€â”€ Semantic community info from graphify.md â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    sem_idx = _load_graphify_semantic_index()
    base_f = os.path.basename(filepath).lower()
    comms = sem_idx.get("file_communities", {}).get(base_f, [])
    god_deg = sem_idx.get("god_nodes", {}).get(base_f, 0)
    if comms or god_deg:
        comm_tag = f"{CYAN}{comms[0]}{RST}" if comms else ""
        god_tag = f"  {BOLD}â˜… God Node ({god_deg} refs){RST}" if god_deg else ""
        print(f"  {DIM}Graph Topology:{RST} {comm_tag}{god_tag}")

    # â”€â”€ functions and classes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    funcs: list[dict] = []
    if filepath.endswith(".py"):
        try:
            import ast
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node) or ""
                    funcs.append({
                        "name":       node.name,
                        "type":       "class" if isinstance(node, ast.ClassDef) else "def",
                        "start_line": node.lineno,
                        "end_line":   getattr(node, "end_lineno", node.lineno),
                        "docstring":  doc,
                    })
        except SyntaxError:
            pass

    if not funcs:
        for i, line in enumerate(raw_lines):
            m = re.match(
                r"^\s*(export\s+)?(async\s+)?function\s+(\w+)"
                r"|^\s*(export\s+)?(default\s+)?class\s+(\w+)"
                r"|^\s*def\s+(\w+)"
                r"|^\s*class\s+(\w+)",
                line,
            )
            if m:
                name = next(g for g in m.groups() if g and re.match(r"^\w", g))
                funcs.append({
                    "name":       name,
                    "type":       "class" if "class" in line else "def",
                    "start_line": i + 1,
                    "end_line":   None,
                    "docstring":  "",
                })

    if funcs:
        print(f"\n  {BOLD}Functions / Classes:{RST}")
        for fn in funcs:
            tag  = "class" if fn["type"] == "class" else "def"
            lnum = fn["start_line"]
            end  = fn["end_line"]
            span = f"L{lnum}-{end}" if end else f"L{lnum}"
            print(f"    {CYAN}{tag} {fn['name']}{RST}  {DIM}{span}{RST}")

            if fn["docstring"]:
                doc_preview = fn["docstring"].splitlines()[0][:80]
                print(f"      {DIM}Â» {doc_preview}{RST}")

            body_start = lnum
            body_lines = raw_lines[body_start : body_start + 3]
            for bl in body_lines:
                stripped = bl.strip()
                if stripped:
                    print(f"      {DIM}{stripped[:100]}{RST}")
    else:
        # If no functions/classes found, show the first 30 lines
        print(f"\n  {BOLD}Preview:{RST}")
        for idx in range(min(30, len(raw_lines))):
            print(f"  {DIM}{idx+1:4d}â”‚{RST} {raw_lines[idx]}")


def _print_all_comments(
    filepath: str,
    start_line: "int | None" = None,
    end_line: "int | None" = None,
) -> None:
    """
    Print every comment line in a file â€” inline (#//) and block (''' / /* */).
    Also prints the first 3 lines of any function immediately following
    a comment block.

    When start_line/end_line are provided, prints ALL lines in that slice but
    highlights comment lines in cyan (focused scan â€” not a pure comment filter).
    """
    content = tool_get_file(filepath)
    if content.startswith("ERROR"):
        print(f"  {RED}{content}{RST}")
        return

    raw_lines = content.splitlines()
    total = len(raw_lines)

    if start_line is not None and end_line is not None:
        # â”€â”€ Focused slice mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        s = max(1, start_line)
        e = min(total, end_line)
        print(
            f"\n{BOLD}Comments in {filepath}{RST}  "
            f"{DIM}({total} lines total  â”‚  Showing L{s}-L{e}){RST}\n"
        )
        print(
            f"  {DIM}PowerShell: Get-Content '{filepath}' | "
            f"Select-Object -Skip {s - 1} -First {e - s + 1}{RST}\n"
        )
        for i in range(s, e + 1):
            raw = raw_lines[i - 1]
            stripped = raw.strip()
            is_comment = (
                stripped.startswith("#")
                or stripped.startswith("//")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
                or stripped.startswith("/*")
                or stripped.startswith("*")
            )
            if is_comment:
                print(f"  {DIM}L{i}:{RST}  {CYAN}{raw}{RST}")
            else:
                print(f"  {DIM}L{i}:{RST}  {raw}")
        return

    # â”€â”€ Full-file mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{BOLD}Comments in {filepath}{RST}  {DIM}({total} lines total){RST}\n")

    in_block = False
    block_delim = None
    block_buf: list[str] = []
    block_start = 0

    def flush_block(end_ln: int):
        if block_buf:
            print(f"  {DIM}L{block_start}-{end_ln}  block comment:{RST}")
            for bl in block_buf:
                print(f"    {CYAN}{bl}{RST}")
            block_buf.clear()

    for i, line in enumerate(raw_lines, 1):
        stripped = line.strip()

        # Python triple-quote block
        if not in_block and (stripped.startswith('"""') or stripped.startswith("'''")):
            delim = stripped[:3]
            rest  = stripped[3:]
            if rest.endswith(delim) and len(rest) > 3:
                # Single-line docstring
                print(f"  {DIM}L{i}:{RST}  {CYAN}{stripped}{RST}")
            else:
                in_block    = True
                block_delim = delim
                block_start = i
                block_buf.append(stripped)
            continue

        if in_block:
            block_buf.append(stripped)
            if block_delim and block_delim in stripped:
                flush_block(i)
                in_block = False
                block_delim = None
            continue

        # C-style block comment
        if not in_block and stripped.startswith("/*"):
            in_block    = True
            block_delim = "*/"
            block_start = i
            block_buf.append(stripped)
            if "*/" in stripped:
                flush_block(i)
                in_block = False
                block_delim = None
            continue

        if in_block and block_delim == "*/":
            block_buf.append(stripped)
            if "*/" in stripped:
                flush_block(i)
                in_block = False
                block_delim = None
            continue

        # Inline comment: Python # or JS //
        if stripped.startswith("#") or stripped.startswith("//"):
            print(f"  {DIM}L{i}:{RST}  {CYAN}{stripped}{RST}")
            # If the next line defines a function, show its first 3 body lines
            for j in range(i, min(i + 3, len(raw_lines))):
                next_line = raw_lines[j].strip()
                if re.match(r"(async\s+)?def\s+|class\s+|function\s+", next_line):
                    print(f"    {DIM}â†’ {next_line[:90]}{RST}")
                    for k in range(j + 1, min(j + 4, len(raw_lines))):
                        body = raw_lines[k].strip()
                        if body:
                            print(f"      {DIM}{body[:90]}{RST}")
                    break


def _ask_did_you_mean(directive: str, intent: str, confidence: float, top3: list) -> str:
    """
    Interactive 'did you mean?' shown when intent confidence is uncertain (55-84%).
    On 'no', shows a numbered list of top-3 alternatives.
    Saves confirmed choice to learned_intents.json for future exact matches.
    Returns the confirmed intent name.
    """
    label = INTENT_LABELS.get(intent, intent)
    print(f"\n{YELL}  âš¡ Did you mean: {BOLD}{label}{RST}{YELL}? ({intent}, {confidence:.0f}% confident){RST}")
    answer = input(f"  {DIM}(y/n): {RST}").strip().lower()

    if answer in ("y", "yes", ""):
        _save_learned_intent(directive, intent)
        return intent

    # User said no â€” show numbered top-3
    print(f"\n{CYAN}  Pick what you meant:{RST}")
    choices = top3[:3]
    for i, (name, score) in enumerate(choices, 1):
        lbl = INTENT_LABELS.get(name, name)
        print(f"  {BOLD}[{i}]{RST} {lbl}  {DIM}({score:.0f}%){RST}")
    print(f"  {BOLD}[0]{RST} None of these â€” skip")

    while True:
        pick = input(f"  {DIM}Enter number: {RST}").strip()
        if pick == "0":
            print(f"  {DIM}Skipping â€” try rephrasing your query.{RST}")
            return intent
        if pick.isdigit() and 1 <= int(pick) <= len(choices):
            chosen = choices[int(pick) - 1][0]
            _save_learned_intent(directive, chosen)
            ok(f"Got it â€” saved '{directive}' â†’ {chosen} for next time.")
            return chosen
        print(f"  {YELL}Enter a number between 0 and {len(choices)}.{RST}")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# LOCAL LLM AGENT (no API key required)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _run_relay_agent(directive: str) -> None:
    """
    Relay path: package context into relay.json so the browser extension
    can inject it into claude.ai, then block until /claude-response fires.
    """
    import json as _json, time as _time

    STATE_DIR = Path.home() / ".pyslick"
    RELAY_FILE = STATE_DIR / "relay.json"
    RESPONSE_FILE = STATE_DIR / "claude_response.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Collect context the same way recon-pack does
    all_files = _collect_all_files()
    matched   = _fuzzy_match_files(directive, all_files)[:3]
    blocks    = []
    for f in matched:
        try:
            content = Path(f).read_text(encoding="utf-8", errors="replace")
            blocks.append({"file": f, "content": content})
        except Exception:
            pass

    payload = {"directive": directive, "files": blocks}
    RELAY_FILE.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    print(f"{DIM}  [relay] Context written Ã¢â€ ' waiting for extension to POST responseâ€¦{RST}")

    # Clear any stale response
    if RESPONSE_FILE.exists():
        RESPONSE_FILE.unlink()

    # Poll for response (extension POSTs to /claude-response Ã¢â€ ' relay_server saves it)
    timeout, interval = 120, 1
    for _ in range(timeout):
        _time.sleep(interval)
        if RESPONSE_FILE.exists():
            try:
                data = _json.loads(RESPONSE_FILE.read_text(encoding="utf-8"))
                print(f"\n{BOLD}Claude (via relay):{RST}")
                for block in data.get("blocks", []):
                    print(block.get("content", ""))
            except Exception as exc:
                print(f"{YELL}  [relay] Could not parse response: {exc}{RST}")
            return

    print(f"{YELL}  [relay] Timed out after {timeout}s â€” no response received.{RST}")
    print(f"{DIM}  Is the browser extension running and connected to claude.ai?{RST}")
def _ensure_graph(verbose: bool = False) -> bool:
    """Build graphify-out/graph.json via `graphify extract . --code-only` if missing.
    Returns True if the graph exists afterwards, False otherwise."""
    graph_json = os.path.join("graphify-out", "graph.json")
    if os.path.exists(graph_json):
        return True
    if verbose:
        print(f"  {CYAN}No graph found. Generating codebase graph via Graphify...{RST}")
    try:
        import graphify  # noqa: F401
    except ImportError:
        if verbose:
            print(f"  {DIM}Graphify not installed. Installing via pip...{RST}")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "graphify", "--user", "--quiet"],
            capture_output=True,
        )
    if verbose:
        print(f"  {DIM}Extracting code dependencies and building graph.json...{RST}")
    for argv in (
        [sys.executable, "-m", "graphify", "extract", ".", "--code-only"],
        ["graphify", "extract", ".", "--code-only"],
        [sys.executable, "-m", "graphify", "update", "."],
        ["graphify", "update", "."],
    ):
        try:
            subprocess.run(argv, capture_output=True, text=True)
        except Exception:
            pass
        if os.path.exists(graph_json):
            break
    if not os.path.exists(graph_json):
        if verbose:
            warn("Could not generate graph. Ensure graphify is installed: pip install graphify")
        return False
    if verbose:
        ok("Graph generated successfully in graphify-out/")
    return True

def _god_recon(directive: str) -> bool:
    """God-node-driven recon. Returns True if it printed output.

    Pipeline:
      1. Load graph.json from cwd (graphify-out/graph.json).
      2. Score every node against the query (label + source_file).
      3. Seed = top scored nodes, weighted by degree.
      4. Expand one hop via edges (calls/imports/references/etc).
      5. Rank neighbors by relation weight * degree * label similarity.
      6. Print: FILES -> COMMENTS -> FUNCTIONS -> CONNECTIONS.

    Returns False (no print) when the graph is missing/empty or nothing
    scores above threshold, so the caller can fall back to the existing
    per-file loop.
    """
    import os as _os
    import json as _json
    import re as _re

    graph_path = _os.path.join(".", "graphify-out", "graph.json")
    if not _os.path.exists(graph_path):
        return False
    try:
        with open(graph_path, "r", encoding="utf-8", errors="replace") as _fh:
            _g = _json.load(_fh)
    except Exception:
        return False

    nodes = _g.get("nodes") or []
    links = _g.get("links") or []
    if not nodes:
        return False

    # --- degree map (undirected, for weighting) ---
    from collections import defaultdict as _dd
    _deg = _dd(int)
    for _l in links:
        _deg[_l.get("source")] += 1
        _deg[_l.get("target")] += 1

    # --- relation weights: how strongly an edge implies "related code" ---
    _REL_W = {
        "calls": 3.0,
        "indirect_call": 2.5,
        "imports": 2.0,
        "imports_from": 2.0,
        "defines": 1.8,
        "contains": 1.0,
        "method": 2.0,
        "references": 1.5,
        "inherits": 2.0,
        "dynamic_import": 1.5,
    }

    # --- query tokens ---
    _stop = {
        "where", "is", "the", "a", "an", "of", "to", "for", "what", "does",
        "how", "defined", "define", "definition", "find", "show", "me", "in",
        "on", "at", "and", "or", "referenced", "used", "called", "use", "call",
        "work", "works", "working", "code", "file", "files", "function",
        "functions", "all", "does", "do",
    }
    _toks = [
        t for t in _re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", (directive or "").lower())
        if t not in _stop and len(t) > 2
    ]
    if not _toks:
        return False

    def _score(text: str) -> float:
        """Substring/token overlap score of text vs query tokens."""
        if not text:
            return 0.0
        t = text.lower()
        s = 0.0
        for tok in _toks:
            if tok in t:
                s += 2.0 if t.startswith(tok) else 1.0
        return s

    # --- score nodes ---
    _node_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    _scored = []
    for n in nodes:
        lab = n.get("label") or n.get("norm_label") or ""
        src = n.get("source_file") or ""
        sc = _score(lab) * 3.0 + _score(src)
        if sc <= 0:
            continue
        _scored.append((sc, _deg.get(n.get("id"), 0), n))
    if not _scored:
        return False

    # seed: score * log(degree+1), so hubs matching the query float up
    import math as _math
    _scored.sort(key=lambda x: x[0] * (1.0 + _math.log1p(x[1])), reverse=True)
    seeds = [n for _sc, _d, n in _scored[:8]]
    seed_ids = {n.get("id") for n in seeds}

    # --- one-hop expansion ---
    neighbors = {}
    conns = []  # (from_id, relation, to_id, source_file)
    for _l in links:
        s_id, t_id = _l.get("source"), _l.get("target")
        rel = _l.get("relation") or ""
        w = _REL_W.get(rel, 1.0)
        if s_id in seed_ids:
            n2 = _node_by_id.get(t_id)
            if n2 is not None:
                prev = neighbors.get(t_id)
                sc2 = _score(n2.get("label") or "") * 3.0 + _score(n2.get("source_file") or "")
                cur = w * (1.0 + _math.log1p(_deg.get(t_id, 0))) * (1.0 + sc2)
                if prev is None or cur > prev[0]:
                    neighbors[t_id] = (cur, n2)
                conns.append((s_id, rel, t_id, _l.get("source_file") or ""))
        if t_id in seed_ids:
            n2 = _node_by_id.get(s_id)
            if n2 is not None:
                prev = neighbors.get(s_id)
                sc2 = _score(n2.get("label") or "") * 3.0 + _score(n2.get("source_file") or "")
                cur = w * (1.0 + _math.log1p(_deg.get(s_id, 0))) * (1.0 + sc2)
                if prev is None or cur > prev[0]:
                    neighbors[s_id] = (cur, n2)
                conns.append((s_id, rel, t_id, _l.get("source_file") or ""))

    ranked_nbrs = [n2 for _c, n2 in sorted(neighbors.values(), key=lambda x: x[0], reverse=True)[:15]]

    # --- collect files ---
    def _file_of(n):
        return n.get("source_file") or n.get("file") or ""
    files_seen = []
    files_set = set()
    for n in seeds + ranked_nbrs:
        f = _file_of(n)
        if f and f not in files_set:
            files_set.add(f)
            files_seen.append(f)
    if not files_seen:
        return False

    # --- get comments for those files via recon index ---
    comments_by_file = {}
    try:
        from recon_semantic import load_or_build_index, _comments_for_file
        _idx = load_or_build_index(".")
        for f in files_seen:
            try:
                comments_by_file[f] = _comments_for_file(f, _idx) or []
            except Exception:
                comments_by_file[f] = []
    except Exception:
        comments_by_file = {}

    # ============ PRINT ============
    def _fmt_node(n):
        lab = n.get("label") or n.get("id") or "?"
        src = n.get("source_file") or ""
        loc = n.get("source_location") or ""
        return lab, src, loc

    print(f"\n{BOLD}=== GOD RECON ==={RST}  {DIM}{directive}{RST}")

    # 1. FILES
    print(f"\n{BOLD}FILES{RST}  {DIM}(ranked by relevance){RST}")
    for f in files_seen[:20]:
        print(f"  {CYAN}{f}{RST}")

    # 2. COMMENTS
    _any_comments = False
    for f in files_seen[:12]:
        cms = comments_by_file.get(f) or []
        if not cms:
            continue
        if not _any_comments:
            print(f"\n{BOLD}COMMENTS{RST}  {DIM}(from promising files){RST}")
            _any_comments = True
        print(f"  {DIM}{f}{RST}")
        for _cm in cms[:6]:
            _ct = (_cm.get("text") or "").strip().splitlines()
            _ct = _ct[0][:160] if _ct else ""
            _cl = _cm.get("line") or ""
            if _ct:
                print(f"    {CYAN}L{_cl}{RST} {_ct}")

    # 3. FUNCTIONS
    print(f"\n{BOLD}FUNCTIONS{RST}  {DIM}(seeds + one-hop neighbors){RST}")
    for n in seeds:
        lab, src, loc = _fmt_node(n)
        print(f"  {CYAN}{lab}{RST}  {DIM}{src} {loc}  [seed]{RST}")
    for n in ranked_nbrs:
        lab, src, loc = _fmt_node(n)
        print(f"  {lab}  {DIM}{src} {loc}{RST}")

    # 4. CONNECTIONS
    if conns:
        print(f"\n{BOLD}CONNECTIONS{RST}  {DIM}(how these functions reach other files){RST}")
        shown = set()
        for s_id, rel, t_id, src_f in conns:
            key = (s_id, rel, t_id)
            if key in shown:
                continue
            shown.add(key)
            sn = _node_by_id.get(s_id) or {}
            tn = _node_by_id.get(t_id) or {}
            sl = sn.get("label") or s_id or "?"
            tl = tn.get("label") or t_id or "?"
            sf = sn.get("source_file") or ""
            tf = tn.get("source_file") or ""
            if sf == tf and rel in ("contains", "method", "defines"):
                continue  # intra-file structural noise
            arrow = f"{DIM}--{rel}-->{RST}"
            print(f"  {sl} {arrow} {tl}   {DIM}[{sf} -> {tf}]{RST}")
            if len(shown) >= 25:
                break

    return True


def _run_local_agent(directive: str) -> None:
    """
    Vocab-driven local agent for the 124M-param model.

    Intent routing is a three-tier hybrid pipeline in _classify_intent_with_confidence():
      Tier 0 â€” learned_intents.json  (user-corrected queries, 100% confidence)
      Tier 1 â€” Exact vocab substring  (intent_vocab.json, 100% confidence)
      Tier 2 â€” Python rapidfuzz fuzzy (word-level scoring, 0-100%)
      Tier 3 â€” LLM arbitration        (only in 55-84% uncertain zone)

    When confidence is 55-84%, the agent asks 'Did you mean X?' before running.
    If the user corrects it, the choice is saved to learned_intents.json.

      help          â†’ pyslick command docs
      git           â†’ git status + optional push prompt
      run_info      â†’ how to run / start / launch project
      list_files    â†’ directory walk + main symbols per file
      comments      â†’ all comments in matched file
      nearest       â†’ find_nearest_nodes around a symbol/line
      scan_function â†’ full function body printed with comments
      graph         â†’ ast_query call graph
      connect       â†’ cross-file call-graph walk
      file_info     â†’ purpose + key functions + first 3 lines
      patch         â†’ fuzzy match â†’ LLM find/replace â†’ diff â†’ confirm
    """
    # Detect frustration / correction from user
    cleaned_directive, was_frustrated, prev_intent = _detect_frustration_and_correction(directive)
    active_directive = cleaned_directive

    # ── Repo-overview shortcut ────────────────────────────────────────────
    # Overview questions ("what does this codebase do", "app overview", …)
    # should reach the existing App Overview block (God Nodes + first
    # comments) further down, not the universal-recon ranker. Skip the
    # router for these so intent falls through to "run_info".
    _OVERVIEW_HINTS = (
        "what does this app", "what does this project", "what does this do",
        "what does the app", "what does the project", "what does this repo",
        "what does this program", "what does the program", "what does this codebase",
        "what is this app", "what is this project", "what is this repo",
        "what is this program", "what is this codebase",
        "what is this for", "what does it do", "describe the app",
        "describe the project", "describe this", "describe this program",
        "overview of", "app overview", "project overview", "app summary",
        "project summary", "summarize this project", "summarize this app",
        "summarize this program", "purpose of this app", "purpose of this project",
        "purpose of this program", "whats this", "what is this",
        "whats this project", "whats this app", "whats this repo",
        "whats this codebase", "whats this program",
        "what's this", "what's this project", "what's this app",
        "what's this program",
    )
    _dl = (active_directive or "").lower()
    if any(_h in _dl for _h in _OVERVIEW_HINTS):
        intent = "run_info"
        # Jump straight to the run_info branch below; skip the recon router.
        # (See the `if intent == "run_info":` handler further down.)
        _skip_router = True
    else:
        _skip_router = False

    # ── Exact-path fast path ──────────────────────────────────────────────
    # If the user named a real file on disk, go straight to file_info on
    # that file. Bypasses fuzzy matching entirely, which was shredding
    # paths down to their basename and returning siblings alphabetically.
    _exact_path: str | None = None
    if not _skip_router:
        try:
            _all_files_for_path = _collect_all_files()
            _exact_path = _extract_exact_path(active_directive, _all_files_for_path)
        except Exception:
            _exact_path = None
        if _exact_path:
            intent = "file_info"
            _skip_router = True

    # ── Rule/LLM router: broad Q&A → universal recon ──────────────────────
    try:
        from router import route as _route
        if _skip_router:
            _r = None
        else:
            _r = _route(active_directive)
        if _r is None:
            pass  # overview shortcut: fall through to the run_info branch below
        elif _r == "comments":
            # Router decided this is a "show comments in <file>" query.
            # Set the intent and skip the classifier so the existing
            # `if intent == "comments":` handler below fires with the
            # directive intact. We do NOT return here — the handler is
            # further down and does the actual work.
            intent = "comments"
            _skip_router = True
        elif _r == "find_symbol":
            # "where is X defined" / "find X" / "definition of X".
            # Set intent and skip classifier; handler is further down.
            intent = "find_symbol"
            _skip_router = True
        elif _r == "find_references":
            # "where is X referenced/used/called". Handler is further down.
            intent = "find_references"
            _skip_router = True
        elif _r == "what_columns":
            # "what columns does T have". Handler is further down.
            intent = "what_columns"
            _skip_router = True

        elif _r == "recon_full":
            from recon_semantic import run_full_recon
            from recon_pack import write_pack
            pack = run_full_recon(active_directive)
            write_pack(pack)
            print(f"{DIM}  routed -> recon_full  "
                  f"(files={len(pack.get('files', []))}, "
                  f"centrality={len(pack.get('centrality', []))}, "
                  f"git_log={len(pack.get('git_log', []))}){RST}")
            snap = pack.get("snap") or {}
            if snap.get("name"):
                print(f"{DIM}  snap: {snap['name']}  "
                      f"(restore: pyslick snap-restore {snap['name']}){RST}")
            return
        if _r == "recon":
            from recon_semantic import run_full_recon
            from recon_pack import write_pack
            pack = run_full_recon(active_directive)
            _rel = pack.get("relation") or {}
            _rel_answered = bool(_rel.get("is_relation_query") and _rel.get("paths"))
            _rel_failed = bool(_rel.get("is_relation_query") and not _rel.get("paths"))
            if not pack.get("files") and not _rel.get("is_relation_query"):
                print(f"{DIM}  no strong matches for: {active_directive}{RST}")
                print(f"{DIM}  try: pyslick find <symbol>  |  pyslick grep <file> <term>{RST}")
                return
            if _rel_failed:
                print(f"\n{BOLD}RELATION{RST}  {_rel.get('entities')}")
                print(f"{DIM}  {_rel.get('note') or 'no path found'}{RST}")
                print(f"{DIM}  try: pyslick find <symbol>  |  pyslick grep <file> <term>{RST}")
                return
            _pack_path = write_pack(pack)
            # Try god-node-driven recon first. If the graph exists and the
            # query matches nodes, it prints FILES/COMMENTS/FUNCTIONS/
            # CONNECTIONS and replaces the per-file snippet loop. If it
            # returns False (no graph, no matches, RELATION query), we fall
            # through to the existing loop below.
            if not _rel.get("is_relation_query"):
                try:
                    if _god_recon(active_directive):
                        return
                except Exception as _gre:
                    print(f"{DIM}  [god_recon skipped: {type(_gre).__name__}: {_gre}]{RST}")
            if _rel_answered:
                print(f"\n{BOLD}RELATION{RST}  {_rel.get('entities')}")
                print(f"{DIM}  confidence: {_rel.get('confidence')}  —  {_rel.get('note')}{RST}")
                for _ev in _rel.get("evidence", []):
                    print(f"  {_ev['step']}. {_ev['from']} --{_ev['relation']}--> {_ev['to']}   {DIM}({_ev['file']} {_ev['loc']}){RST}")
                _src_file = _rel.get("evidence", [{}])[0].get("file") if _rel.get("evidence") else None
                if _src_file:
                    print(f"\n{BOLD}SOURCE{RST}  {_src_file}")
                    try:
                        with open(_src_file, "r", encoding="utf-8", errors="replace") as _fh:
                            _src_lines = _fh.readlines()
                        _loc = _rel.get("evidence", [{}])[0].get("loc") or ""
                        _ln = int("".join(ch for ch in _loc if ch.isdigit()) or "1")
                        _lo = max(1, _ln - 6)
                        _hi = min(len(_src_lines), _ln + 6)
                        for _i in range(_lo, _hi + 1):
                            print(f"  {_i:>4}: {_src_lines[_i-1].rstrip()}")
                    except Exception as _e:
                        print(f"  {DIM}(could not read source: {_e}){RST}")
                print(f"\n{DIM}  for broader context run: pyslick recon-pack '{active_directive}'{RST}")
                return

            _cent = pack.get("centrality") or []
            if _cent:
                print(f"\n{BOLD}CENTRALITY{RST}  {DIM}(most-connected symbols){RST}")
                for _c in _cent[:10]:
                    _cid = _c.get("label") or _c.get("name") or _c.get("id") or "?"
                    _cl = str(_cid)
                    if _cl.startswith("src_"):
                        _cl = _cl[4:]
                    _cl = _cl.replace("_", ".")
                    _cf = _c.get("file") or _c.get("source_file") or ""
                    _cs = _c.get("degree") or _c.get("score") or _c.get("centrality") or ""
                    _cstr = f"  {DIM}deg={_cs}{RST}" if _cs != "" else ""
                    _cfile = f"  {DIM}{_cf}{RST}" if _cf else ""
                    print(f"  {CYAN}{_cl}{RST}{_cstr}{_cfile}")

            for _f in pack.get("files", []):
                print(f"\n{BOLD}{_f.get('path')}{RST}  {DIM}({_f.get('mode')}, {_f.get('line_count')} lines){RST}")
                for _cm in (_f.get("comments") or [])[:8]:
                    _ct = (_cm.get("text") or _cm.get("body") or "").strip()
                    _cl = _cm.get("line") or _cm.get("start_line") or ""
                    if _ct:
                        _ct1 = _ct.splitlines()[0][:200]
                        print(f"  {DIM}#{RST} {CYAN}L{_cl}{RST} {_ct1}")
                for _sym in (_f.get("symbols") or [])[:20]:
                    _sn = _sym.get("name") or _sym.get("label") or "?"
                    _sk = _sym.get("kind") or _sym.get("type") or ""
                    _sr = _sym.get("range") or (
                        f"L{_sym.get('start_line')}-{_sym.get('end_line')}"
                        if _sym.get("start_line") else f"L{_sym.get('line')}"
                    )
                    print(f"  {CYAN}{_sn}{RST}  {DIM}{_sk}  {_sr}{RST}")
                for _h in _f.get("hits", []):
                    print(f"  {CYAN}L{_h.get('line')}{RST} {_h.get('name') or _h.get('kind') or ''}  {DIM}{_h.get('kind')}{RST}")
                    _txt = (_h.get("text") or "").strip()
                    if _txt:
                        print(f"    {_txt[:800]}")
                for _s in _f.get("snippets", []):
                    print(f"  {CYAN}L{_s.get('start_line')}-{_s.get('end_line')}{RST} {DIM}({_s.get('matched_term')}){RST}")
                    _txt = (_s.get("text") or "").strip()
                    if _txt:
                        print(f"    {_txt[:800]}")
                _c = _f.get("content")
                if _c:
                    print(f"  {DIM}[full file: {len(_c)} chars]{RST}")
            return
    except Exception as _router_err:
        print(f"  [router block error: {type(_router_err).__name__}: {_router_err}]")
    # ── Fall through to the classifier below ──────────────────────────────
    try:
        from llm import is_available, maybe_expand_query
        _llm_ready = is_available()
    except ImportError:
        _llm_ready = False
        def maybe_expand_query(d): return d  # no-op fallback

    if _skip_router:
        # Overview shortcut already decided the intent; don't let the
        # classifier clobber it.
        confidence = 100.0
        top3 = [(intent, confidence)]
    else:
        intent, confidence, top3 = _classify_intent_with_confidence(active_directive)
    dl = active_directive.lower()
    print(f"{DIM}  intent â†’ {intent}  ({confidence:.0f}%){RST}")

    if was_frustrated:
        _adjust_intent_weight(intent, +0.08)
        _save_learned_intent(directive, intent)
        _save_learned_intent(active_directive, intent)
        ok(f"Learned correction: '{active_directive}' â†’ {intent}")

    # Show 'did you mean?' when confidence is in the uncertain zone (55-84%)
    # Skip if user already gave an explicit line number â€” context is unambiguous
    has_line_range = bool(re.search(r'(?:line|lines|l)\s*\d+', active_directive, re.IGNORECASE))
    if 55.0 <= confidence < 85.0 and not was_frustrated and not has_line_range:
        intent = _ask_did_you_mean(active_directive, intent, confidence, top3)
        dl = active_directive.lower()

    # Save session for context tracking
    _save_session(directive, intent)

    # â”€â”€ HELP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "help":
        result = tool_pyslick_help()
        print(result)
        return

    # â”€â”€ GIT â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "git":
        # Rollback/revert/undo must be checked FIRST, before the log and
        # push/commit/checkpoint branches below â€” a directive like
        # "rollback to commit 3" or "undo 3 commits ago" contains the
        # substrings "commit"/"commits", which would otherwise get
        # swallowed by those branches (plain `in dl` substring checks)
        # before ever reaching this one.
        if re.search(r"\b(?:undo|revert|rollback|roll\s+back|discard|go\s+back)\b", dl):
            repo_name = _get_repo_name()
            if not repo_name:
                warn("Not inside a git repository â€” nothing to roll back.")
                return

            has_target = bool(re.search(r"\b(?:rollback|revert|undo|go\s+back)\b.*(?:\bto\b|\bcommits?\s+ago\b|\bcommit\s*#?\d+\b)", dl))
            if not has_target:
                # Bare "rollback"/"undo" with no named target. Previously
                # this silently fell through to a plain `git status` print
                # with no indication anything related to the rollback
                # request had even been noticed. Show recent commits and
                # ask for an explicit target instead of guessing one.
                hdr("Git", "Rollback â€” target needed")
                warn("Rollback needs a target. Recent commits:")
                print(tool_pyslick_log())
                print("\n  Try: \"rollback to commit 2\", \"rollback 2 commits ago\", or \"rollback to <message keywords>\".")
                return

            result = _resolve_commit_reference(dl)
            status = result[0]

            if status == "none":
                hdr("Git", "Rollback â€” no match")
                warn("Couldn't confidently resolve that commit reference. Recent commits:")
                print(tool_pyslick_log())
                return

            if status == "ambiguous":
                hdr("Git", "Rollback â€” ambiguous target")
                warn("More than one commit matches that description â€” be more specific:")
                for h, m in result[1]:
                    print(f"    {h}  {m}")
                return

            _, commit_hash, commit_msg, label = result

            # Show exactly what would be lost before asking for confirmation.
            hdr("Git", f"Rollback to {label}")
            print(f"  Repo: {YELL}{repo_name}{RST}")
            show_res = subprocess.run(
                ["git", "log", "-1", "--pretty=format:%H%n%an%n%ad%n%s", commit_hash],
                capture_output=True, text=True, timeout=5,
            )
            if show_res.returncode == 0:
                full_hash, author, date, subject = (show_res.stdout.split("\n", 3) + ["", "", "", ""])[:4]
                print(f"  Target commit: {full_hash[:10]}  \"{subject}\"")
                print(f"  Author: {author}    Date: {date}")

            lost_res = subprocess.run(
                ["git", "log", "--oneline", f"{commit_hash}..HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            lost_lines = [l for l in lost_res.stdout.splitlines() if l.strip()]
            print(f"\n  This will discard {len(lost_lines)} commit(s) above the target:")
            for l in lost_lines:
                print(f"    {l}")

            diff_res = subprocess.run(
                ["git", "diff", commit_hash, "--stat"], capture_output=True, text=True, timeout=10
            )
            if diff_res.stdout.strip():
                print(f"\n  Files that would change:\n{diff_res.stdout}")

            confirm = input(
                f"\n{BOLD}  â¸  Rollback to {commit_hash[:10]} \"{commit_msg[:60]}\"? "
                f"This discards {len(lost_lines)} commit(s) above it. (yes/no): {RST}"
            ).strip().lower()
            if confirm not in ("y", "yes"):
                print("  Rollback cancelled.")
                return

            # Safety net: tag current HEAD with a recoverable backup branch
            # before doing anything destructive. `git reset --hard` is the
            # only thing that actually delivers "rollback to X" as asked â€”
            # --soft/--mixed leave the discarded commits' changes sitting in
            # the working tree, which isn't a rollback, it's an undo-staging.
            # A backup branch makes --hard fully recoverable without
            # changing that behavior.
            head_res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
            short_head = head_res.stdout.strip() if head_res.returncode == 0 else "backup"
            backup_branch = f"backup/pre-rollback-{short_head}-{int(time.time())}"
            branch_res = subprocess.run(["git", "branch", backup_branch], capture_output=True, text=True, timeout=5)
            if branch_res.returncode != 0:
                warn(f"Could not create backup branch ({(branch_res.stderr or '').strip()[:200]}) â€” aborting rollback for safety.")
                return
            ok(f"Backup created: {backup_branch} (points at current HEAD, for recovery)")

            reset_res = subprocess.run(["git", "reset", "--hard", commit_hash], capture_output=True, text=True, timeout=10)
            if reset_res.returncode == 0:
                ok(f"Rolled back to {commit_hash[:10]} \"{commit_msg[:60]}\"")
                print(f"  If this was a mistake: git reset --hard {backup_branch}")
            else:
                warn(f"Rollback FAILED: {(reset_res.stderr or reset_res.stdout or '').strip()[:300]}")
            return

        if any(kw in dl for kw in ["log", "logs", "history", "commits"]):
            hdr("Git", "Log (Recent Commits)")
            print(tool_pyslick_log())
            return

        if any(kw in dl for kw in ["diff", "changes", "unstaged"]):
            hdr("Git", "Diff (Uncommitted Changes)")
            print(tool_pyslick_diff())
            return

        if any(kw in dl for kw in ["push"]) and not any(kw in dl for kw in ["commit", "checkpoint"]):
            # Real push-only path: auto add -> commit -> push, no per-step
            # confirmation ("push" is already an explicit instruction, not
            # an ambiguous checkpoint request). Previously "push" was
            # bucketed with "commit"/"checkpoint" and always stopped at a
            # "Create checkpoint?" prompt, never actually reaching a push â€”
            # confirmed by reproducing the exact reported terminal output.
            repo_name = _get_repo_name()
            if not repo_name:
                warn("Not inside a git repository â€” nothing to push.")
                return
            print(f"  Repo: {YELL}{repo_name}{RST}")
            hdr("Git", "Auto Add â†’ Commit â†’ Push")
            msg_match = re.search(r'(?:message|msg|with)[:\s]+["\']?(.+?)["\']?\s*$', dl)
            commit_msg = msg_match.group(1).strip() if msg_match else None
            success, log = tool_pyslick_add_commit_push(commit_msg)
            print(log)
            if success:
                ok(f"Pushed to {repo_name}")
            else:
                warn(f"Push to {repo_name} did not complete â€” see log above.")
            return

        if any(kw in dl for kw in ["push", "commit", "checkpoint"]):
            hdr("Git", "Status")
            status = tool_pyslick_status()
            print(status)
            msg_match = re.search(r'(?:message|msg|with)[:\s]+["\']?(.+?)["\']?\s*$', dl)
            if msg_match:
                commit_msg = msg_match.group(1).strip()
            else:
                try:
                    from pyslick import generate_smart_commit_message
                    commit_msg = generate_smart_commit_message(directive)
                except Exception:
                    commit_msg = directive
            confirm = input(
                f"\n{BOLD}  â¸  Create checkpoint '{commit_msg[:70]}'? (yes/no): {RST}"
            ).strip().lower()
            if confirm in ("y", "yes"):
                result = tool_pyslick_checkpoint(commit_msg)
                ok(result)
            return

        hdr("Git", "Status")
        status = tool_pyslick_status()
        print(status)
        return
    if intent == "find_references":
        import re as _re
        m = (
            _re.search(r"\bwhere\s+is\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:referenced|used|called)\b", active_directive, _re.IGNORECASE)
            or _re.search(r"\b(?:references?|usages?|call\s*sites?)\s+(?:of|to|for)\s+([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
        )
        if not m:
            print(f"  {DIM}Could not extract a symbol name from: {active_directive}{RST}")
            return
        sym = m.group(1)

        SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".turbo",
                     ".venv", "venv", "__pycache__", ".pyslick", ".pyslick_context",
                     "graphify-out", "coverage", ".cache"}
        exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart",
                ".java", ".kt", ".go", ".rs", ".cs", ".rb", ".php", ".swift"}
        root = os.getcwd()
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    files.append(os.path.join(dirpath, fn))

        use_pat = _re.compile(
            r"(?<![A-Za-z0-9_])" + _re.escape(sym) +
            r"(?=\s*[\(\[\.]|\s*[,\)\]]|\s*:\s*|\s*$)"
        )
        import_pat = _re.compile(
            r"^\s*(?:import|from)\b.*\b" + _re.escape(sym) + r"\b"
        )
        decl_pat = _re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"(?:function|class|const|let|var|def|interface|type|enum)\s+" + _re.escape(sym) + r"\b"
        )

        hits: list[tuple[str, int, str, bool]] = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        if sym not in line:
                            continue
                        if decl_pat.match(line):
                            continue
                        is_imp = bool(import_pat.match(line))
                        if use_pat.search(line) or is_imp:
                            hits.append((os.path.relpath(fp, root), ln, line.rstrip(), is_imp))
            except Exception:
                continue

        hdr("Find References", sym)
        if not hits:
            print(f"  {DIM}No references to '{sym}' found.{RST}\n")
            return

        from collections import OrderedDict
        by_file: "OrderedDict[str, list[tuple[int, str, bool]]]" = OrderedDict()
        for fp, ln, line, is_imp in hits:
            by_file.setdefault(fp, []).append((ln, line, is_imp))

        for fp, entries in by_file.items():
            print(f"\n  {BOLD}{fp}{RST}  {DIM}({len(entries)}){RST}")
            for ln, line, is_imp in entries[:12]:
                tag = f"{DIM}[import]{RST} " if is_imp else ""
                trim = line.strip()
                if len(trim) > 140:
                    trim = trim[:137] + "..."
                print(f"    {CYAN}L{ln:<4}{RST} {tag}{trim}")
            if len(entries) > 12:
                print(f"    {DIM}... +{len(entries)-12} more in this file{RST}")
        print(f"\n  {DIM}{len(hits)} reference(s) across {len(by_file)} file(s){RST}\n")
        return

    if intent == "what_columns":
        import re as _re
        m = (
            _re.search(r"\bcolumns?\s+(?:does|do|has)\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
            or _re.search(r"\bcolumns?\s+(?:of|for|in)\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
            or _re.search(r"\bwhat\s+columns?\s+(?:are\s+)?in\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
        )
        if not m:
            print(f"  {DIM}Could not extract a table/collection name from: {active_directive}{RST}")
            return
        table = m.group(1)

        SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".turbo",
                     ".venv", "venv", "__pycache__", ".pyslick", ".pyslick_context",
                     "graphify-out", "coverage", ".cache"}
        exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sql"}
        root = os.getcwd()
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    files.append(os.path.join(dirpath, fn))

        from_rx = _re.compile(
            r"\.from\(\s*['\"]" + _re.escape(table) + r"['\"]\s*\)",
            _re.IGNORECASE
        )
        sql_from_rx = _re.compile(
            r"\b(?:FROM|INTO|UPDATE)\s+['\"`]?" + _re.escape(table) + r"\b",
            _re.IGNORECASE
        )
        select_rx = _re.compile(r"\.select\(\s*['\"](.+?)['\"]\s*[,)]", _re.DOTALL)
        obj_keys_rx = _re.compile(r"\.(?:insert|update|upsert)\(\s*\{([^}]+)\}", _re.DOTALL)

        # Scope every .select/.insert/.update to the .from('table') call
        # that precedes it — not the whole file. Otherwise a file that
        # queries two tables bleeds columns across them.
        from_call_rx = _re.compile(
            r"\.from\(\s*['\"]" + _re.escape(table) + r"['\"]\s*\)",
        )
        method_rx = _re.compile(
            r"\.(select|insert|update|upsert|eq|is|in|order|limit|maybeSingle|single)\s*\(",
        )
        select_local = _re.compile(r"\.select\(\s*['\"](.+?)['\"]\s*[,)]", _re.DOTALL)
        obj_local = _re.compile(r"\.(?:insert|update|upsert)\(\s*\{([^}]+)\}", _re.DOTALL)

        cols_by_file: dict[str, set[str]] = {}
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                continue
            found: set[str] = set()
            # For each .from('decks'), grab the rest of the statement: read
            # forward until we hit a line-ending semicolon or another
            # .from( call. Take .select(...) and .insert/update({...}) out
            # of that slice only.
            for mo in from_call_rx.finditer(text):
                start = mo.end()
                # find the next .from( or end of file
                next_from = from_call_rx.search(text, start)
                chunk = text[start: next_from.start() if next_from else len(text)]
                # cut the chunk at a hard statement boundary: double
                # newline that isn't followed by a continuation
                for line in chunk.splitlines():
                    stripped = line.strip()
                    if stripped.endswith(";") and not stripped.endswith(";\n"):
                        # line-based, keep going — supabase chains rarely
                        # end in ; mid-chain. Not perfect but tighter.
                        pass
                for sm in select_local.finditer(chunk):
                    raw = sm.group(1)
                    for piece in raw.split(","):
                        name = piece.strip().split(":")[0].strip().strip("'\"")
                        name = name.split(".")[-1]
                        if name and name != "*":
                            found.add(name)
                for om in obj_local.finditer(chunk):
                    for line in om.group(1).splitlines():
                        line = line.strip()
                        if not line or ":" not in line:
                            continue
                        key = line.split(":")[0].strip().strip("'\"")
                        if key and key != "...":
                            found.add(key)
            # SQL fallback for .sql files
            if sql_from_rx.search(text):
                sql_col_rx = _re.compile(
                    r"\b" + _re.escape(table) + r"\b\s*\(([^)]+)\)",
                    _re.IGNORECASE | _re.DOTALL
                )
                for sm in sql_col_rx.finditer(text):
                    for line in sm.group(1).split(","):
                        name = line.strip().split()[0].strip("'\"`")
                        if name and name.lower() not in ("primary", "foreign", "unique", "check"):
                            found.add(name)
            if found:
                cols_by_file.setdefault(os.path.relpath(fp, root), set()).update(found)
        hdr("What Columns", table)
        print(f"  {DIM}NOTE: columns are aggregated per file. If a file queries multiple tables,")
        print(f"  a column belonging to a sibling table may appear. Cross-check with the file.{RST}\n")
        if not cols_by_file:
            print(f"  {DIM}No references to table/collection '{table}' found.{RST}\n")
            return

        all_cols: set[str] = set()
        for cols in cols_by_file.values():
            all_cols.update(cols)

        print(f"  {BOLD}{len(all_cols)} distinct column(s) referenced across {len(cols_by_file)} file(s):{RST}\n")
        for c in sorted(all_cols):
            print(f"    {CYAN}{c}{RST}")
        print(f"\n  {DIM}by file:{RST}")
        for fp, cols in sorted(cols_by_file.items()):
            print(f"    {BOLD}{fp}{RST}  {DIM}({len(cols)}){RST}")
            print(f"      {DIM}{', '.join(sorted(cols))}{RST}")
        print()
        return

        import re as _re
        m = (
            _re.search(r"\bwhere\s+is\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:referenced|used|called)\b", active_directive, _re.IGNORECASE)
            or _re.search(r"\b(?:references?|usages?|call\s*sites?)\s+(?:of|to|for)\s+([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
        )
        if not m:
            print(f"  {DIM}Could not extract a symbol name from: {active_directive}{RST}")
            return
        sym = m.group(1)

        SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".turbo",
                     ".venv", "venv", "__pycache__", ".pyslick", ".pyslick_context",
                     "graphify-out", "coverage", ".cache"}
        exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart",
                ".java", ".kt", ".go", ".rs", ".cs", ".rb", ".php", ".swift"}
        root = os.getcwd()
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    files.append(os.path.join(dirpath, fn))

        # Match use sites: sym( ...   sym.  sym[   -> sym  :sym  import sym
        # Case-sensitive — symbol names are case-sensitive.
        use_pat = _re.compile(
            r"(?<![A-Za-z0-9_])" + _re.escape(sym) +
            r"(?=\s*[\(\[\.]|\s*[,\)\]]|\s*:\s*|\s*$)"
        )
        # also flag imports referencing the symbol
        import_pat = _re.compile(
            r"^\s*(?:import|from)\b.*\b" + _re.escape(sym) + r"\b"
        )
        # skip the line where the symbol is declared
        decl_pat = _re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"(?:function|class|const|let|var|def|interface|type|enum)\s+" + _re.escape(sym) + r"\b"
        )

        hits: list[tuple[str, int, str, bool]] = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        if sym not in line:
                            continue
                        if decl_pat.match(line):
                            continue
                        is_imp = bool(import_pat.match(line))
                        if use_pat.search(line) or is_imp:
                            hits.append((os.path.relpath(fp, root), ln, line.rstrip(), is_imp))
            except Exception:
                continue

        hdr("Find References", sym)
        if not hits:
            print(f"  {DIM}No references to '{sym}' found.{RST}\n")
            return

        # group by file
        from collections import OrderedDict
        by_file: "OrderedDict[str, list[tuple[int, str, bool]]]" = OrderedDict()
        for fp, ln, line, is_imp in hits:
            by_file.setdefault(fp, []).append((ln, line, is_imp))

        for fp, entries in by_file.items():
            print(f"\n  {BOLD}{fp}{RST}  {DIM}({len(entries)}){RST}")
            for ln, line, is_imp in entries[:12]:
                tag = f"{DIM}[import]{RST} " if is_imp else ""
                trim = line.strip()
                if len(trim) > 140:
                    trim = trim[:137] + "..."
                print(f"    {CYAN}L{ln:<4}{RST} {tag}{trim}")
            if len(entries) > 12:
                print(f"    {DIM}... +{len(entries)-12} more in this file{RST}")
        print(f"\n  {DIM}{len(hits)} reference(s) across {len(by_file)} file(s){RST}\n")
        return

    # â”€â”€ RUN INFO (how to run project, repo, directory, or file) â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "find_symbol":
        # Pure symbol search. No LLM, no graph, no fuzzy. Ripgrep-style
        # regex sweep for declarations of the named symbol, print
        # path:line hits. Answers "where is X defined" directly.
        import re as _re

        m = (
            _re.search(r"\bwhere\s+is\s+([A-Za-z_][A-Za-z0-9_]*)\s+defined\b", active_directive, _re.IGNORECASE)
            or _re.search(r"\b(?:definition|defined)\s+of\s+([A-Za-z_][A-Za-z0-9_]*)\b", active_directive, _re.IGNORECASE)
            or _re.search(r"^\s*(?:find|locate)\s+([A-Za-z_][A-Za-z0-9_]{2,})\s*$", active_directive, _re.IGNORECASE)
        )
        if not m:
            print(f"  {DIM}Could not extract a symbol name from: {active_directive}{RST}")
            return
        sym = m.group(1)

        # Declaration patterns across common languages.
        patterns = [
            rf"(?:export\s+)?(?:async\s+)?function\s+{_re.escape(sym)}\b",
            rf"(?:export\s+)?(?:const|let|var)\s+{_re.escape(sym)}\b",
            rf"(?:export\s+)?class\s+{_re.escape(sym)}\b",
            rf"(?:export\s+)?(?:interface|type|enum)\s+{_re.escape(sym)}\b",
            rf"^\s*def\s+{_re.escape(sym)}\b",
            rf"^\s*class\s+{_re.escape(sym)}\b",
            rf"(?:export\s+)?(?:default\s+)?\s*{_re.escape(sym)}\s*[:=]",
        ]

        # Collect source files to scan (skip node_modules, .git, dist, etc).
        SKIP_DIRS = {".git", "node_modules", "dist", "build", ".next", ".turbo",
                     ".venv", "venv", "__pycache__", ".pyslick", ".pyslick_context",
                     "graphify-out", "coverage", ".cache"}
        exts = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".dart",
                ".java", ".kt", ".go", ".rs", ".cs", ".rb", ".php", ".swift"}
        root = os.getcwd()
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in exts:
                    files.append(os.path.join(dirpath, fn))

        compiled = [_re.compile(p) for p in patterns]
        hits: list[tuple[str, int, str]] = []
        for fp in files:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    for ln, line in enumerate(fh, 1):
                        if sym not in line:
                            continue
                        for cp in compiled:
                            if cp.search(line):
                                hits.append((os.path.relpath(fp, root), ln, line.rstrip()))
                                break
            except Exception:
                continue

        hdr("Find Symbol", sym)
        if not hits:
            print(f"  {DIM}No declaration of '{sym}' found in source files.{RST}\n")
            return
        for fp, ln, line in hits[:40]:
            print(f"  {BOLD}{fp}{RST}:{ln}")
            print(f"    {DIM}{line.strip()[:200]}{RST}")
        if len(hits) > 40:
            print(f"  {DIM}... {len(hits) - 40} more hits omitted{RST}")
        print()
        return

    if intent == "run_info":

        # â”€â”€ APP SUMMARY (what does this app/project do?) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        _APP_SUMMARY_TRIGGERS = {
            "what does this app", "what does this project", "what does this do",
            "what does the app", "what does the project", "what does this repo",
            "what does this program", "what does the program", "what does this codebase",
            "what is this app", "what is this project", "what is this repo",
            "what is this program", "what is this codebase",
            "what is this for", "what does it do", "describe the app",
            "describe the project", "describe this", "describe this program", "overview of",
            "app overview", "project overview", "app summary", "project summary",
            "summarize this project", "summarize this app", "summarize this program",
            "purpose of this app", "purpose of this project", "purpose of this program",
            "whats this", "what is this", "whats this project", "whats this app",
            "whats this repo", "whats this codebase", "whats this program",
            "what's this", "what's this project", "what's this app", "what's this program",
        }
        is_app_summary = any(t in dl for t in _APP_SUMMARY_TRIGGERS)

        if is_app_summary:
            hdr("App Overview", "God Nodes + First Comments")

            # Load graph.json from graphify-out/ if present
            graph_path = os.path.join("graphify-out", "graph.json")
            god_files: list[tuple[int, str]] = []  # (degree, source_file)

            if os.path.exists(graph_path):
                try:
                    gdata = json.loads(Path(graph_path).read_text(encoding="utf-8"))
                    # Build idâ†’source_file map (only file-type nodes)
                    id_to_file: dict[str, str] = {}
                    for node in gdata.get("nodes", []):
                        sf = node.get("source_file", "")
                        if sf and node.get("file_type") == "code":
                            id_to_file[node["id"]] = sf
                    # Count in-degree per file (how many others import/reference it)
                    file_deg: dict[str, int] = {}
                    for link in gdata.get("links", []):
                        tgt = id_to_file.get(link.get("target", ""))
                        if tgt:
                            file_deg[tgt] = file_deg.get(tgt, 0) + 1
                    # Pick top 3 unique source files by degree
                    seen: set[str] = set()
                    for sf, deg in sorted(file_deg.items(), key=lambda x: -x[1]):
                        if sf not in seen and os.path.exists(sf):
                            god_files.append((deg, sf))
                            seen.add(sf)
                        if len(god_files) >= 5:
                            break
                except Exception:
                    pass

            # Fallback: common entry-point names if no graphify data
            if not god_files:
                for cand in ["electron-main.js", "main.js", "index.js", "app.js",
                             "main.py", "app.py", "server.py", "index.ts", "app.ts"]:
                    if os.path.exists(cand):
                        god_files.append((0, cand))
                    if len(god_files) >= 5:
                        break

            # Also surface package.json description / README first line
            summary_lines: list[str] = []
            if os.path.exists("package.json"):
                try:
                    pkg = json.loads(Path("package.json").read_text(encoding="utf-8"))
                    desc = pkg.get("description", "").strip()
                    name = pkg.get("name", "")
                    if desc:
                        summary_lines.append(f"{BOLD}{name}{RST}  {DIM}{desc}{RST}")
                except Exception:
                    pass
            if os.path.exists("pyproject.toml"):
                for line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("description"):
                        desc = line.split("=", 1)[-1].strip().strip('"').strip("'")
                        if desc:
                            summary_lines.append(f"{DIM}{desc}{RST}")
                        break

            if summary_lines:
                for sl in summary_lines:
                    print(f"  {sl}")
                print()

            if god_files:
                print(f"  {CYAN}Most connected files (god nodes):{RST}\n")
                for deg, sf in god_files:
                    deg_label = f"{DIM}({deg} refs){RST}" if deg else ""
                    print(f"  {BOLD}{sf}{RST}  {deg_label}")
                    comments = _first_comment_lines(sf, max_lines=3)
                    if comments:
                        for c in comments:
                            print(f"    {DIM}â†’ {c}{RST}")
                    else:
                        print(f"    {DIM}(no opening comments){RST}")

                    # Most-connected function blocks within this file â€”
                    # in-file call count via stdlib ast, 2-3 per file, not
                    # a full function dump.
                    top_fns = _rank_functions_by_connectivity(sf, top_n=3)
                    if top_fns:
                        print(f"    {CYAN}Most-connected functions:{RST}")
                        for fn in top_fns:
                            print(f"      {BOLD}{fn['name']}{RST}  "
                                  f"{DIM}L{fn['start']}-{fn['end']} "
                                  f"({fn['callers_in_file']} calls in file){RST}")
                            if fn["comment"]:
                                print(f"        {DIM}â†’ {fn['comment']}{RST}")
                    print()
            else:
                if _ensure_graph(verbose=True) and os.path.exists(os.path.join("graphify-out", "graph.json")):
                    # re-run the overview branch by reloading the graph
                    try:
                        _gdata = json.loads(Path(os.path.join("graphify-out", "graph.json")).read_text(encoding="utf-8"))
                        print(f"  {DIM}Graph built. Re-run the same directive to see the overview.{RST}\n")
                    except Exception:
                        print(f"  {DIM}No graphify-out/graph.json found â€” run graphify first for richer results.{RST}\n")
                else:
                    print(f"  {DIM}No graphify-out/graph.json found â€” run graphify first for richer results.{RST}\n")

            return

        hdr("Run Instructions", "Project Execution & Scripts")
        found_info = False

        # Detect package manager
        pm = "npm"
        if os.path.exists("pnpm-lock.yaml"):
            pm = "pnpm"
        elif os.path.exists("yarn.lock"):
            pm = "yarn"
        elif os.path.exists("bun.lockb"):
            pm = "bun"

        # Check if user mentioned a specific file to run
        all_files = _collect_all_files()
        matched_files = _fuzzy_match_files(directive, all_files)
        if matched_files:
            target_f = matched_files[0]
            ext = Path(target_f).suffix.lower()
            print(f"  {BOLD}How to run {target_f}:{RST}")
            if ext == ".py":
                print(f"    â€¢ Direct: {BOLD}python {target_f}{RST}")
                print(f"    â€¢ Module: {BOLD}python -m {Path(target_f).stem}{RST}")
            elif ext in (".js", ".mjs", ".cjs"):
                if "electron" in target_f.lower() or os.path.exists("electron-main.js"):
                    print(f"    â€¢ Electron: {BOLD}npx electron {target_f}{RST} (or {BOLD}{pm} start{RST})")
                print(f"    â€¢ Node:     {BOLD}node {target_f}{RST}")
            elif ext in (".ts", ".tsx"):
                print(f"    â€¢ TypeScript: {BOLD}npx ts-node {target_f}{RST}")
            elif ext == ".html":
                print(f"    â€¢ Open in browser or local server: {BOLD}npx serve .{RST}")
            print()
            found_info = True

        # Check for package.json (Node/Electron/Next/Vite/etc)
        if os.path.exists("package.json"):
            try:
                pkg = json.loads(Path("package.json").read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                main_file = pkg.get("main")
                pkg_name = pkg.get("name", "Project")
                print(f"  {BOLD}{pkg_name}{RST} {DIM}(Detected {pm.upper()} project){RST}")
                if main_file:
                    print(f"  {CYAN}Main entry:{RST} {main_file}")
                if scripts:
                    print(f"\n  {CYAN}Available Scripts:{RST}")
                    for s_name, s_cmd in scripts.items():
                        run_prefix = f"{pm} {s_name}" if pm != "npm" else f"npm run {s_name}"
                        if s_name in ("start", "test"):
                            run_prefix = f"{pm} {s_name}"
                        print(f"    â€¢ {BOLD}{run_prefix}{RST} â†’ {DIM}{s_cmd}{RST}")
                    found_info = True
            except Exception:
                pass

        # Check for Python projects
        py_files = [f for f in os.listdir(".") if f.endswith(".py")]
        if os.path.exists("pyproject.toml") or os.path.exists("setup.py") or os.path.exists("requirements.txt") or py_files:
            print(f"\n  {BOLD}Python Environment:{RST}")
            if os.path.exists("requirements.txt"):
                print(f"    â€¢ Install deps: {BOLD}pip install -r requirements.txt{RST}")
            if os.path.exists("setup_and_install.py"):
                print(f"    â€¢ One-shot setup: {BOLD}python setup_and_install.py{RST}")
            for main_cand in ["main.py", "app.py", "cli.py", "server.py", "index.py", "electron-main.js"]:
                if os.path.exists(main_cand):
                    cmd = f"python {main_cand}" if main_cand.endswith(".py") else f"node {main_cand}"
                    print(f"    â€¢ Run entry point: {BOLD}{cmd}{RST}")
            if os.path.exists("pytest.ini") or os.path.exists("tests"):
                print(f"    â€¢ Run tests: {BOLD}pytest{RST}")
            found_info = True

        # Check README.md for Run / Getting Started / Usage sections
        for readme_fn in ["README.md", "readme.md", "README.txt"]:
            if os.path.exists(readme_fn):
                readme_text = Path(readme_fn).read_text(encoding="utf-8", errors="replace")
                run_sections = re.findall(
                    r"(#{1,3}\s+(?:Getting\s+Started|Running|Usage|Quick\s+Start|Installation|How\s+to\s+Run|How\s+to\s+Use)[^\n]*\n(?:(?!\n#{1,3}\s).)*)",
                    readme_text,
                    re.IGNORECASE | re.DOTALL,
                )
                if run_sections:
                    print(f"\n  {CYAN}From {readme_fn}:{RST}")
                    for sec in run_sections[:2]:
                        lines = [l for l in sec.strip().splitlines()[:15]]
                        print("  " + "\n  ".join(lines))
                    found_info = True
                break

        if not found_info:
            print(f"  {DIM}No package.json, pyproject.toml, or README run instructions found in cwd.{RST}")
            print(f"  {DIM}Try: pyslick agent \"how to run <filename>\"{RST}")
        return

    # â”€â”€ LIST FILES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "list_files":
        # Determine root â€” check if user mentioned a specific directory
        root_match = re.search(
            r'(?:in|into|inside|under|directory|dir|folder)[:\s]+["\']?([./\w\-]+)["\']?',
            dl,
        )
        root = root_match.group(1).strip() if root_match else "."
        if not os.path.isdir(root):
            root = "."

        _LANG_MAP: dict[str, tuple[str, set[str]]] = {
            "js": ("JavaScript", {".js", ".mjs", ".cjs"}),
            "javascript": ("JavaScript", {".js", ".mjs", ".cjs"}),
            "script": ("Scripts", {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".sh", ".ps1", ".bat"}),
            "scripts": ("Scripts", {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".sh", ".ps1", ".bat"}),
            "ts": ("TypeScript", {".ts", ".mts", ".cts"}),
            "typescript": ("TypeScript", {".ts", ".tsx", ".mts", ".cts"}),
            "tsx": ("React TSX", {".tsx"}),
            "jsx": ("React JSX", {".jsx"}),
            "py": ("Python", {".py", ".pyw", ".ipynb"}),
            "python": ("Python", {".py", ".pyw", ".ipynb"}),
            "html": ("HTML", {".html", ".htm"}),
            "css": ("CSS/Styles", {".css", ".scss", ".sass", ".less"}),
            "vue": ("Vue", {".vue"}),
            "svelte": ("Svelte", {".svelte"}),
            "java": ("Java", {".java"}),
            "kt": ("Kotlin", {".kt", ".kts"}),
            "kotlin": ("Kotlin", {".kt", ".kts"}),
            "scala": ("Scala", {".scala"}),
            "c": ("C", {".c", ".h"}),
            "cpp": ("C++", {".cpp", ".cc", ".cxx", ".hpp"}),
            "c++": ("C++", {".cpp", ".cc", ".cxx", ".hpp"}),
            "cs": ("C#", {".cs"}),
            "csharp": ("C#", {".cs"}),
            "rs": ("Rust", {".rs"}),
            "rust": ("Rust", {".rs"}),
            "go": ("Go", {".go"}),
            "golang": ("Go", {".go"}),
            "swift": ("Swift", {".swift"}),
            "sh": ("Shell", {".sh", ".bash", ".zsh"}),
            "bash": ("Bash", {".sh", ".bash"}),
            "ps1": ("PowerShell", {".ps1"}),
            "powershell": ("PowerShell", {".ps1"}),
            "bat": ("Batch", {".bat", ".cmd"}),
            "json": ("JSON", {".json"}),
            "yaml": ("YAML", {".yaml", ".yml"}),
            "yml": ("YAML", {".yaml", ".yml"}),
            "toml": ("TOML", {".toml"}),
            "sql": ("SQL", {".sql"}),
            "md": ("Markdown", {".md", ".markdown"}),
            "markdown": ("Markdown", {".md", ".markdown"}),
            "env": ("Environment / Config", {".env"}),
            "environment": ("Environment / Config", {".env"}),
        }

        # 1. Check for explicit dot syntax: 'show .kt', 'show *.py', 'show .env', 'list .js'
        dot_match = re.search(r'(?:show\s+me|show|list|all|find|display|get)\s+(?:\*\s*)?\.([a-zA-Z0-9_\-]+)', dl)
        # 2. Check for dynamic language phrase: 'show me all kotlin files', 'list zig files', 'all lua scripts'
        dyn_match = re.search(
            r'(?:show\s+me|show|list|all|find|display|get)\s+(?:all\s+)?(?:the\s+)?([a-zA-Z0-9+#.]+)\s+(?:files|scripts|code|sources|docs)',
            dl
        )

        matched_exts: set[str] = set()
        matched_lang_name = ""

        if dot_match:
            dot_val = dot_match.group(1).lower()
            if dot_val in _LANG_MAP:
                matched_lang_name, matched_exts = _LANG_MAP[dot_val]
            else:
                matched_lang_name = f".{dot_val}"
                matched_exts = {f".{dot_val}"}
        elif dyn_match:
            lang_val = dyn_match.group(1).lower().lstrip(".")
            if lang_val in _LANG_MAP:
                matched_lang_name, matched_exts = _LANG_MAP[lang_val]
            else:
                matched_lang_name = lang_val.upper()
                matched_exts = {f".{lang_val}"}
        else:
            # Check general words against _LANG_MAP
            dl_words = set(re.findall(r"[a-zA-Z0-9+#.]+", dl))
            for kw, (lname, exts) in _LANG_MAP.items():
                if kw in dl_words or f".{kw}" in dl_words:
                    matched_exts.update(exts)
                    matched_lang_name = lname

        hdr_sub = f"{root} ({matched_lang_name})" if matched_lang_name else root
        hdr("List Files", hdr_sub)

        def _matches_ext(fp: str, exts: set[str]) -> bool:
            fn = os.path.basename(fp).lower()
            if fn.startswith(".env") and ".env" in exts:
                return True
            return Path(fp).suffix.lower() in exts

        all_files = _collect_all_files(root)
        if matched_exts:
            code_files = [f for f in all_files if _matches_ext(f, matched_exts)]
        else:
            code_files = [f for f in all_files if Path(f).suffix.lower() in CODE_EXTS or os.path.basename(f).startswith(".env")]

        if not code_files:
            # Fallback to Graphify Node Query & Encapsulation search
            enriched_nodes = _find_nearest_nodes_with_encapsulation(active_directive, all_files, top_k=3)
            if enriched_nodes:
                hdr("Node & Design Query (Graphify Fallback)", active_directive)
                for item in enriched_nodes:
                    _print_encapsulated_node_view(item, all_files, dl, max_compact_lines=20)
                return

            if matched_lang_name:
                warn(f"No {matched_lang_name} files found in {root}.")
            else:
                warn(f"No source files found in {root}.")
            return

        print(f"{DIM}  {len(code_files)} files found{RST}\n")

        wants_symbols = any(kw in dl for kw in ["method", "object", "function", "symbol", "main", "script", "scripts"])

        for fp in sorted(code_files):
            try:
                line_cnt = len(Path(fp).read_text(encoding="utf-8", errors="replace").splitlines())
                line_info = f"{DIM}({line_cnt} lines){RST}"
            except Exception:
                line_info = ""

            print(f"  {BOLD}â€¢ {fp}{RST}  {line_info}")

            if wants_symbols:
                content = tool_get_file(fp)
                if not content.startswith("ERROR"):
                    raw = content.splitlines()
                    syms: list[str] = []
                    for line in raw[:150]:
                        m = re.match(
                            r"^\s*(export\s+)?(async\s+)?(?:function|def|class)\s+([a-zA-Z0-9_$]+)"
                            r"|(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\([^\)]*\)\s*=>",
                            line,
                        )
                        if m:
                            sname = m.group(3) or m.group(4)
                            if sname and sname not in syms:
                                syms.append(sname)
                    if syms:
                        print(f"    {DIM}symbols: {', '.join(syms[:6])}{RST}")
        return

    # â”€â”€ COMMENTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "comments":
        all_files  = _collect_all_files()
        # "comment(s) on|in|of <file>" — match on the file token alone,
        # not the whole directive. Otherwise the word "comment" itself
        # pulls comment_blocks.py to the top regardless of the target.
        _cm = re.search(r"\bcomments?\s+(?:on|in|of)\s+(.+)$", dl)
        _match_target = _cm.group(1).strip() if _cm else directive
        matched    = _fuzzy_match_files(_match_target, all_files)

        if not matched:
            warn("No file matched. Try naming a file explicitly.")
            return

        # Extract line range from directive (same regex as file_info)
        range_match = re.search(
            r'(?:line|lines|l)\s*(\d+)\s*(?:to|-|through|\.\.)\s*(?:line|lines|l)?\s*(\d+)',
            active_directive, re.IGNORECASE,
        )
        start_ln = int(range_match.group(1)) if range_match else None
        end_ln   = int(range_match.group(2)) if range_match else None

        # When a line range is given, use the top match directly â€” no picker
        target = matched[0]
        targets = [target]

        # If the directive itself explicitly names multiple files, skip
        # the interactive picker entirely and show all of them â€” the user
        # already told us what they want, no need to ask again.
        explicit_file_refs = re.findall(
            r"(?<!\S)/[a-zA-Z0-9_./\\\-]+\.[a-zA-Z0-9]+", active_directive
        )
        auto_multi = len(explicit_file_refs) >= 2 and len(matched) > 1

        if auto_multi and start_ln is None:
            targets = list(matched)
        elif len(matched) > 1 and start_ln is None:
            print("Multiple files matched:")
            for i, f in enumerate(matched):
                print(f"  [{i}] {f}")
            choice = input(
                "Select (comma-separated indices, 'all', or Enter for first): "
            ).strip()

            if not choice:
                targets = [matched[0]]
            elif choice.lower() == "all":
                targets = list(matched)
            else:
                picked = []
                for part in choice.split(","):
                    part = part.strip()
                    if part.isdigit() and int(part) < len(matched):
                        picked.append(matched[int(part)])
                # fall back to first match if nothing valid was entered,
                # rather than silently printing nothing
                targets = picked or [matched[0]]

        for i, t in enumerate(targets):
            if i > 0:
                print(f"\n{DIM}{'â”€' * 60}{RST}")
            _print_all_comments(t, start_line=start_ln, end_line=end_ln)

        # "comments" is read-only. If the directive's own wording implies
        # an actual EDIT (clean up, remove, delete, strip out...), say so
        # explicitly rather than silently under-delivering â€” previously
        # "clean up the old commented-out code in X" would just print the
        # comments and stop, with nothing telling the user this view can't
        # act on what they asked for.
        _EDIT_VERBS = (
            "clean up", "cleanup", "remove", "delete", "strip out",
            "get rid of", "take out", "delete the",
        )
        if any(v in dl for v in _EDIT_VERBS):
            print(
                f"\n  {DIM}Note: this only shows comments (read-only). "
                f"To actually remove/edit them, try:{RST}\n"
                f"  {DIM}pyslick agent \"{directive}\"{RST}"
            )
        return

    # â”€â”€ NEAREST NODE / FUNCTION / METHOD / OBJECT / GENERAL RECON â”€â”€â”€â”€â”€â”€
    if intent == "nearest":
        # Extract a line number if present
        line_match = re.search(r"(?:line|l)\s*(\d+)", dl)
        line_num   = int(line_match.group(1)) if line_match else None

        # Extract a symbol name if present e.g. "closest to run_agent"
        sym_match  = re.search(r"closest to\s+[(\"]?(\w+)[)\"]?", dl)
        sym_name   = sym_match.group(1) if sym_match else None

        all_files = _collect_all_files()
        matched_files = _fuzzy_match_files(active_directive, all_files)
        target = matched_files[0] if matched_files else None

        query = sym_name if sym_name else (f"line {line_num} in {target}" if line_num and target else active_directive)
        hdr("Nearest Functions / Files / Objects", sym_name or str(line_num) or active_directive)

        enriched_nodes = _find_nearest_nodes_with_encapsulation(query, all_files, top_k=5)

        if enriched_nodes:
            print(f"  {CYAN}Nearest Functions & Symbols (Graphify Scope Encapsulation):{RST}")
            for item in enriched_nodes:
                _print_encapsulated_node_view(item, all_files, dl, max_compact_lines=20)
        else:
            print(f"  {DIM}No graph symbols matching '{query}' found.{RST}")

        if matched_files:
            print(f"\n  {CYAN}Nearest File Names:{RST}")
            for fp in matched_files[:6]:
                print(f"    â€¢ {BOLD}{fp}{RST}")

        # â”€â”€ Concept-lookup fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Nothing matched by graph symbol OR filename â€” e.g. "what's the
        # deal with the intent classifier" where no file/symbol literally
        # contains those words. Rather than leave the user with nothing,
        # fall back to a comment-based summary: if the directive names a
        # real folder, summarize its top-3 most-connected files (first 10
        # comment lines each); if it names a real file, show that file's
        # first 5 comment lines. This is a distinct, honest "here's what's
        # around that might be relevant" answer, not a claim that the
        # concept itself was found.
        if not enriched_nodes and not matched_files:
            folder_hit, file_hit = _extract_folder_or_file_target(active_directive, all_files)
            if folder_hit:
                print(f"\n  {DIM}No exact match â€” summarizing top files in '{folder_hit}' instead:{RST}\n")
                _print_concept_summary_for_folder(folder_hit, all_files)
            elif file_hit:
                print(f"\n  {DIM}No exact match â€” summarizing '{file_hit}' instead:{RST}\n")
                _print_concept_summary_for_file(file_hit)
        return

    # â”€â”€ SCAN FUNCTION (print function end-to-end with comments) â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "scan_function":
        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(active_directive, all_files)
        target    = matched[0] if matched else None

        # If the directive explicitly names multiple files, search across
        # all of them rather than collapsing to just the first match â€”
        # same "multi-file when multi-file is asked for" rule as file_info.
        explicit_file_refs = re.findall(
            r"(?<!\S)/[a-zA-Z0-9_./\\\-]+\.[a-zA-Z0-9]+", active_directive
        )
        search_across_matched = len(explicit_file_refs) >= 2 and len(matched) > 1

        # Clean search term: remove 'show me function', 'where is function', etc.
        fn_clean = re.sub(
            r"^(?:show me|show|find|scan|print|inspect|where is|which)\s+(?:the\s+)?(?:entire\s+)?(?:function|def|class|method)\s*",
            "",
            active_directive,
            flags=re.IGNORECASE
        ).strip()
        fn_clean = re.sub(r"(?:function|def|class|method|in\s+.*|of\s+.*)$", "", fn_clean, flags=re.IGNORECASE).strip()
        query_term = fn_clean or active_directive

        hdr("Scan Function", query_term)

        try:
            from rapidfuzz import fuzz
        except ImportError:
            class _FuzzFallback:
                @staticmethod
                def WRatio(s1, s2): return 100 if s1.lower() in s2.lower() or s2.lower() in s1.lower() else 0
                @staticmethod
                def token_set_ratio(s1, s2): return 100 if s1.lower() in s2.lower() else 0
                @staticmethod
                def partial_ratio(s1, s2): return 100 if s1.lower() in s2.lower() else 0
            fuzz = _FuzzFallback()

        # Search across target file first, or all project files
        search_files = matched if search_across_matched else ([target] if target else all_files)
        found_matches = []

        for fp in search_files:
            content = tool_get_file(fp)
            if content.startswith("ERROR"):
                continue
            raw_lines = content.splitlines()
            for i, line in enumerate(raw_lines):
                m = re.search(
                    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|class)\s+([a-zA-Z0-9_$]+)"
                    r"|(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\([^\)]*\)\s*=>"
                    r"|([a-zA-Z0-9_$]+)\s*:\s*(?:async\s*)?\([^\)]*\)\s*=>",
                    line
                )
                if m:
                    fname = m.group(1) or m.group(2) or m.group(3)
                    sim = max(
                        fuzz.WRatio(query_term, fname),
                        fuzz.token_set_ratio(query_term, fname),
                        fuzz.partial_ratio(query_term, line)
                    )
                    if sim >= 55:
                        found_matches.append((sim, fp, i, fname, raw_lines))

        found_matches.sort(key=lambda x: x[0], reverse=True)

        if found_matches:
            best_sim, best_fp, start_idx, best_fname, raw_lines = found_matches[0]
            end_idx = min(start_idx + 60, len(raw_lines))

            # Find logical end of function (next top-level def/class/function)
            for j in range(start_idx + 1, min(start_idx + 120, len(raw_lines))):
                if re.match(r"^(?:export\s+)?(?:async\s+)?(?:function|def|class)\s+", raw_lines[j]):
                    end_idx = j
                    break
                end_idx = j + 1

            ps_cmd = f"Get-Content '{best_fp}' | Select-Object -Skip {start_idx} -First {end_idx - start_idx}"
            print(f"  {BOLD}{best_fp}{RST}  {CYAN}{best_fname}{RST}  {DIM}L{start_idx + 1}-L{end_idx}{RST}")
            print(f"  {CYAN}PowerShell:{RST} {BOLD}{ps_cmd}{RST}\n")

            for ln_idx in range(start_idx, end_idx):
                ln = ln_idx + 1
                line = raw_lines[ln_idx]
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    print(f"  {DIM}{ln:4d}â”‚{RST} {CYAN}{line}{RST}")
                else:
                    print(f"  {DIM}{ln:4d}â”‚{RST} {line}")
            return

        # Fallback to Broad Recon Mode when single function match fails
        hdr("Broad Recon Fallback", query_term)
        enriched_nodes = _find_nearest_nodes_with_encapsulation(query_term, all_files, top_k=4)
        if enriched_nodes:
            print(f"  {CYAN}Nearest Functions / Symbols & Scope Encapsulation:{RST}")
            for item in enriched_nodes:
                _print_encapsulated_node_view(item, all_files, dl, max_compact_lines=20)

        matched_files = _fuzzy_match_files(active_directive, all_files)
        if matched_files:
            print(f"\n  {CYAN}Nearest Matched Files:{RST}")
            for fp in matched_files[:5]:
                print(f"    â€¢ {BOLD}{fp}{RST}")

        if not enriched_nodes and not matched_files:
            warn(f"Could not locate function or files matching '{query_term}'. Try specifying the function name or file.")
        return

    # â”€â”€ CALL GRAPH â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "graph":

        # â”€â”€ GRAPHIFY.MD EXPORT (create graphify.md for Claude Web paste) â”€â”€
        _EXPORT_TRIGGERS = {
            "graphify md", "graphify.md", "graphify markdown", "create graphify",
            "create graph", "export graphify", "export graph", "graphify report",
            "graph report", "graphify file", "graphify output", "graph output",
            "make graphify", "make graph", "generate graphify", "generate graph",
            "write graphify", "write graph", "graphmd", "graph.md", "graph md",
            "graph txt", "graphify notes", "graph notes", "graphify.", "graphify",
        }
        is_export = any(t in dl for t in _EXPORT_TRIGGERS)

        if is_export:
            hdr("Graphify Export", "graphify.md")

            graph_json  = os.path.join("graphify-out", "graph.json")
            analysis_json = os.path.join("graphify-out", ".graphify_analysis.json")

            if not os.path.exists(graph_json):
                if not _ensure_graph(verbose=True):
                    return

            gdata = json.loads(Path(graph_json).read_text(encoding="utf-8"))
            analysis: dict = {}
            if os.path.exists(analysis_json):
                try:
                    analysis = json.loads(Path(analysis_json).read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Build node id â†’ node map
            node_map: dict[str, dict] = {n["id"]: n for n in gdata.get("nodes", [])}

            # Build in-degree per node for community member ranking
            in_deg: dict[str, int] = {}
            for lnk in gdata.get("links", []):
                t = lnk.get("target", "")
                in_deg[t] = in_deg.get(t, 0) + 1

            communities: dict[str, list[str]] = analysis.get("communities", {})
            gods: list[dict] = analysis.get("gods", [])
            surprises: list[dict] = analysis.get("surprises", [])
            questions: list[dict] = analysis.get("questions", [])
            cohesion: dict = analysis.get("cohesion", {})

            # Derive community names from highest-degree member's source_file
            def _community_name(member_ids: list[str]) -> str:
                best = max(member_ids, key=lambda nid: in_deg.get(nid, 0))
                n = node_map.get(best, {})
                return n.get("source_file") or n.get("label") or best

            lines: list[str] = []
            lines.append("# Codebase Graph Map")
            lines.append(f"\n> Generated by pyslick from `graphify-out/graph.json`  ")
            lines.append(f"> {len(node_map)} nodes Â· {len(gdata.get('links', []))} links Â· {len(communities)} communities\n")

            # â”€â”€ Communities â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if communities:
                lines.append("## Communities\n")
                for cid, members in sorted(communities.items(), key=lambda x: int(x[0]) if x[0].isdigit() else str(x[0])):
                    cname = _community_name(members)
                    coh_val = cohesion.get(cid, cohesion.get(str(cid), None))
                    coh_str = f"  *(cohesion {coh_val:.2f})*" if coh_val is not None else ""
                    lines.append(f"### Community {cid} â€” `{cname}`{coh_str}\n")
                    for nid in sorted(members, key=lambda x: -in_deg.get(x, 0)):
                        n = node_map.get(nid, {})
                        label = n.get("label") or nid
                        sf = n.get("source_file", "")
                        loc = n.get("source_location", "")
                        sf_str = f" Â· `{sf}` {loc}" if sf else ""
                        deg = in_deg.get(nid, 0)
                        deg_str = f" ({deg} refs)" if deg else ""
                        lines.append(f"- **{label}**{deg_str}{sf_str}")
                    lines.append("")

            # â”€â”€ God Nodes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if gods:
                lines.append("## God Nodes (highest degree)\n")
                lines.append("| Node | Label | Refs | File |")
                lines.append("|------|-------|------|------|")
                for g in gods:
                    n = node_map.get(g["id"], {})
                    sf = n.get("source_file", "")
                    lines.append(f"| `{g['id']}` | {g['label']} | {g['degree']} | `{sf}` |")
                lines.append("")

            # â”€â”€ Surprises (bridge edges) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if surprises:
                lines.append("## Surprising Connections\n")
                for s in surprises:
                    src = s.get("source", "?")
                    tgt = s.get("target", "?")
                    rel = s.get("relation", "â†’")
                    note = s.get("note", "")
                    sfs = ", ".join(f"`{f}`" for f in s.get("source_files", []))
                    lines.append(f"- **{src}** {rel} **{tgt}**  ")
                    if note:
                        lines.append(f"  *{note}*  ")
                    if sfs:
                        lines.append(f"  Files: {sfs}")
                lines.append("")

            # â”€â”€ Bridge Questions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if questions:
                lines.append("## Questions for Claude\n")
                for q in questions:
                    lines.append(f"- {q.get('question', '')}")
                    why = q.get("why", "")
                    if why:
                        lines.append(f"  > {why}")
                lines.append("")

            # Write file
            out_path = "graphify.md"
            Path(out_path).write_text("\n".join(lines), encoding="utf-8")
            size_kb = Path(out_path).stat().st_size // 1024
            ok(f"Written: {out_path}  ({size_kb or '<1'} KB)  â€”  ready for AI webapp / Claude")
            print(f"  {DIM}Communities: {len(communities)}  â”‚  God nodes: {len(gods)}  â”‚  Bridge questions: {len(questions)}{RST}\n")
            print(f"  {CYAN}ðŸ’¡ Semantic Search Tip:{RST} Parse {BOLD}graphify.md{RST} with an AI (Claude / ChatGPT).")
            print(f"     pyslick uses this extracted graph topology and community semantics for smarter searching!\n")
            return

        # â”€â”€ Web Design / UI Architecture & Cross-File Communities â”€â”€â”€â”€â”€â”€
        is_design_query = any(w in dl for w in [
            "design", "webpage", "website", "ui", "component", "components",
            "layout", "appearance", "interface", "style", "styles", "styling",
            "visual", "page", "theme"
        ])
        all_files = _collect_all_files()

        has_web_files = any(f.endswith((".html", ".htm", ".css", ".scss", ".jsx", ".tsx", ".js", ".ts", ".vue", ".svelte")) for f in all_files)

        if is_design_query or has_web_files:
            ran_webdesign = False
            try:
                from webdesign import build_communities
                communities, all_nodes = build_communities(root=".", include_singletons=False)
                if communities:
                    hdr("Web Design Architecture", f"{len(communities)} UI Communities")
                    for idx, comm in enumerate(communities[:8], 1):
                        members = comm["members"]
                        # Get representative label
                        html_members = [m for m in members if m.startswith("html:")]
                        css_members  = [m for m in members if m.startswith("css:")]
                        js_members   = [m for m in members if m.startswith("js:")]

                        label = html_members[0].split(":")[-1] if html_members else (css_members[0].split(":")[-1] if css_members else members[0])
                        print(f"\n  {BOLD}Community {idx} â€” {CYAN}{label}{RST}")

                        for m in members[:8]:
                            node = all_nodes.get(m)
                            if node:
                                ntype = node.type.upper()
                                nfile = os.path.basename(node.file)
                                print(f"    â€¢ {DIM}[{ntype}]{RST} {BOLD}{node.name}{RST} {DIM}({nfile} L{node.line}){RST}")
                    ran_webdesign = True
            except Exception:
                pass

            if ran_webdesign and is_design_query:
                return

        # â”€â”€ Graphify AST Node Encapsulation Query â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        enriched_nodes = _find_nearest_nodes_with_encapsulation(active_directive, all_files, top_k=3)
        if enriched_nodes:
            hdr("Code & Design Architecture (Graphify)", active_directive)
            for item in enriched_nodes:
                _print_encapsulated_node_view(item, all_files, dl, max_compact_lines=20)
            return

        # â”€â”€ Python AST call-graph fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        matched   = _fuzzy_match_files(directive, all_files)
        py_files  = [f for f in (matched or all_files) if f.endswith(".py")]

        if py_files:
            target = py_files[0]
            hdr("Call Graph", target)
            result = tool_ast_query(target, directive)
            print(result)
            return

        warn("No graph nodes or design components matched.")
        return

    # â”€â”€ CONNECT (cross-file call-graph & node encapsulation) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "connect":
        try:
            from query import graphify_query
        except ImportError:
            warn("query module not available.")
            return

        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(directive, all_files)

        # 1. Node-First Resolution â€” find closest symbols/nodes and their encapsulating functions/classes
        enriched_nodes = _find_nearest_nodes_with_encapsulation(active_directive, all_files, top_k=3)

        if enriched_nodes:
            hdr("Connections â€¢ Node & Scope Encapsulation", active_directive)
            for item in enriched_nodes:
                _print_encapsulated_node_view(item, all_files, dl, max_compact_lines=20)

        elif matched:
            hdr("Connections", " â†” ".join(os.path.basename(f) for f in matched[:3]))
            for fp in matched[:4]:
                print(f"\n{BOLD}{fp}{RST}")
                _print_file_summary(fp, dl)
        else:
            warn("No files or symbols matched. Be more specific.")
            return

        # Cross-file relationships for every pair if multiple files matched
        target_files = [n["file"] for n in enriched_nodes if n.get("file")] + matched
        unique_files = []
        for uf in target_files:
            if uf not in unique_files and os.path.exists(uf):
                unique_files.append(uf)

        if len(unique_files) >= 2:
            print(f"\n{DIM}Cross-file call relationships:{RST}")
            for i, f1 in enumerate(unique_files[:3]):
                for f2 in unique_files[i+1:4]:
                    if not f1.endswith(".py"):
                        continue
                    try:
                        print(f"\n  {BOLD}{os.path.basename(f1)} â†” {os.path.basename(f2)}{RST}")
                        results = graphify_query(
                            f1,
                            f"functions that call or are called by {f2}",
                            top_k=3, depth=2,
                        )
                        if results:
                            for r in results:
                                sym = r.symbol
                                callees = sorted(sym.get("callees", set()))
                                callers = sorted(sym.get("callers", set()))
                                print(f"    {CYAN}{sym['name']}{RST}  L{sym['start_line']}-{sym['end_line']}")
                                if sym.get("docstring"):
                                    print(f"      {DIM}{sym['docstring'][:80]}{RST}")
                                if callees:
                                    print(f"      calls â†’ {', '.join(callees[:4])}")
                                if callers:
                                    print(f"      called by â† {', '.join(callers[:4])}")
                        else:
                            print(f"    {DIM}(no direct connections found){RST}")
                    except Exception:
                        pass
        return

    # â”€â”€ FILE INFO â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # â”€â”€ FILE INFO (Cat / Scan / Snippet / Purpose / Line Ranges) â”€â”€â”€â”€â”€â”€
    if intent == "file_info":
        if _exact_path and os.path.exists(_exact_path):
            dl_local = (active_directive or "").lower()
            _want_full = "--full" in dl_local or os.environ.get("PYSLICK_FULL") == "1"
            # --grep <pat>: print only matching lines with 2 ctx.
            _grep_m = re.search(r"--grep\s+(\S+)", active_directive or "")
            try:
                with open(_exact_path, "r", encoding="utf-8", errors="replace") as _fh:
                    _lines = _fh.readlines()
            except Exception as _e:
                hdr("File", _exact_path)
                warn(f"Could not read {_exact_path}: {_e}")
                return

            if _grep_m:
                # grep mode: narrow output to matching lines only
                _pat = _grep_m.group(1)
                hdr("Grep", f"{_exact_path}  /{_pat}/")
                try:
                    _rx = re.compile(_pat)
                except re.error:
                    _rx = re.compile(re.escape(_pat))
                _hit_ln = [i for i, ln in enumerate(_lines, 1) if _rx.search(ln)]
                if not _hit_ln:
                    print(f"  {DIM}no matches{RST}")
                    return
                for i in _hit_ln:
                    lo = max(1, i - 2)
                    hi = min(len(_lines), i + 2)
                    for j in range(lo, hi + 1):
                        mark = ">" if j == i else " "
                        print(f"  {mark} {j:>4}: {_lines[j-1].rstrip()}")
                    print(f"  {DIM}---{RST}")
                print(f"\n  {DIM}{len(_hit_ln)} match(es) in {len(_lines)} lines{RST}")
                return

            if _want_full:
                hdr("File", _exact_path)
                for _i, _ln in enumerate(_lines, 1):
                    print(f"  {_i:>4}: {_ln.rstrip()}")
                print(f"\n  {DIM}{len(_lines)} lines{RST}")
                return

            # Default: signatures only. ~90% fewer tokens than a full dump.
            # Match function/class/interface/type at any indent (nesting ok),
            # but const/let/var/def only at column 0 (module top-level), so we
            # don't list every `const x = ...` inside a function body.
            _decl_rx_top = re.compile(
                r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(?:const|let|var|def)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
            )
            _decl_rx_any = re.compile(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(?:function|class|interface|type|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
            )
            _decls: list[tuple[int, str]] = []
            for _i, _ln in enumerate(_lines, 1):
                if _decl_rx_top.match(_ln) or _decl_rx_any.match(_ln):
                    _decls.append((_i, _ln.strip()))

            print(f"\n{BOLD}{_exact_path}{RST}  {DIM}{len(_lines)} lines{RST}")
            if not _decls:
                print(f"  {DIM}(no top-level declarations detected){RST}")
            else:
                for _i, _sig in _decls[:30]:
                    _trim = _sig if len(_sig) <= 100 else _sig[:97] + "..."
                    print(f"  {CYAN}L{_i:<4}{RST} {_trim}")
                if len(_decls) > 30:
                    print(f"  {DIM}... +{len(_decls) - 30} more{RST}")
            print(f"\n  {DIM}full body:  pyslick lines \"{_exact_path}\"{RST}")
            print(f"  {DIM}one symbol: pyslick \"{_exact_path}\" --grep <pattern>{RST}")
            print()
            return
        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(active_directive, all_files)

        # â”€â”€ "why is X so big/huge/large" â†’ function/comment size graph â”€â”€â”€â”€
        # Answers with structure (functions+classes by size, with their
        # comments), never a full-file dump. Handles both "why is
        # patchit.py so huge" (filename given) and "why is this file so
        # big" (no filename â€” falls back to the single most-connected file
        # project-wide, same god-node ranking used for folder summaries).
        is_size_question = bool(re.search(r"\b(?:so\s+)?(?:big|huge|large|long)\b", dl)) and \
            bool(re.search(r"\bwhy\b", dl))
        if is_size_question:
            if matched:
                target_file = matched[0]
                hdr("File Size Graph", target_file)
                _print_file_size_graph(target_file)
            else:
                top = _top_connected_files_in(".", top_n=1)
                if top:
                    print(f"  {DIM}No filename given â€” using the most-connected file in the project:{RST}\n")
                    hdr("File Size Graph", top[0])
                    _print_file_size_graph(top[0])
                else:
                    warn("No file matched and no project files found to fall back to.")
            return

        if not matched:
            warn("No file matched. Try naming the file more explicitly.")
            return

        # Check for line range in directive (e.g. 'line 30 to 70', 'lines 30-70', 'L30-70')
        range_match = re.search(r'(?:line|lines|l)\s*(\d+)\s*(?:to|-|through|\.\.)\s*(?:line|lines|l)?\s*(\d+)', dl)
        single_line_match = re.search(r'(?:line|lines|l)\s*(\d+)\b', dl)

        start_line = None
        end_line = None
        if range_match:
            start_line = int(range_match.group(1))
            end_line = int(range_match.group(2))
            if start_line > end_line:
                start_line, end_line = end_line, start_line
        elif single_line_match:
            l_num = int(single_line_match.group(1))
            start_line = max(1, l_num - 10)
            end_line = l_num + 10

        target_file = matched[0]
        line_str = f"L{start_line}-L{end_line}" if (start_line and end_line) else None
        summary_kw = summarize_intent_keywords(active_directive, intent, target_file, line_str)
        print(f"  {CYAN}Action:{RST} {BOLD}{summary_kw}{RST}")

        # Multi-file intent: the user gets ALL matched files shown, without
        # needing a follow-up "show all", whenever the original directive
        # itself makes multi-file intent explicit â€” either by naming â‰¥2
        # files directly (e.g. "compare /a.js and /b.js", "/a.js /b.js
        # /c.js all three pls") or by using an explicit "show all"/"all
        # files"/"both files" phrase. A single named file (even with other
        # fuzzy-matched candidates lurking behind it) still shows just the
        # primary match â€” we only expand when the user's own wording asked
        # for more than one.
        explicit_file_refs = re.findall(
            r"(?<!\S)/[a-zA-Z0-9_./\\\-]+\.[a-zA-Z0-9]+", active_directive
        )
        wants_multi_phrase = any(
            p in dl for p in ("show all", "all files", "both files", "all of them")
        )
        wants_multi = wants_multi_phrase or len(explicit_file_refs) >= 2

        files_to_show = matched if wants_multi else [target_file]
        for i, fp in enumerate(files_to_show):
            if i > 0:
                print(f"\n{DIM}{'â”€' * 60}{RST}")
            hdr("File Cat / Scan", fp)
            _print_file_cat_and_snippet(fp, dl, start_line=start_line, end_line=end_line)

        # If there are other matching files and we didn't show everything,
        # list them concisely so the user knows they exist.
        if len(matched) > 1 and not wants_multi:
            other_files = [os.path.basename(f) for f in matched[1:5]]
            print(f"\n  {DIM}Other matches: {', '.join(other_files)} (use 'show all' to scan all){RST}")
        return

    # â”€â”€ STRAY SYMBOLS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "stray_symbols":
        from find_stray_symbols import find_stray_symbols
        all_files = _collect_all_files()
        matched = _fuzzy_match_files(active_directive, all_files) or all_files
        hdr("Stray Symbol Check", os.getcwd())
        total_issues = 0
        for fp in matched:
            try:
                issues = find_stray_symbols(fp)
            except Exception:
                issues = []
            for iss in issues:
                line_no = iss.get("line", "?")
                sym = iss.get("symbol", iss.get("message", str(iss)))
                print(f"  {YELLOW}âš {RST}  {os.path.basename(fp)}:{BOLD}{line_no}{RST}  {sym}")
                total_issues += 1
        if total_issues == 0:
            print(f"  {GREEN}âœ”{RST}  No stray symbols found.")
        else:
            print(f"\n  {YELLOW}{total_issues} issue(s) found.{RST}")
        return

    # â”€â”€ SYNTAX CHECK â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "syntax_check":
        import ast as _ast
        all_files = _collect_all_files()
        matched = _fuzzy_match_files(active_directive, all_files) or all_files
        hdr("Syntax Check", os.getcwd())
        total_errors = 0
        for fp in matched:
            ext = os.path.splitext(fp)[1].lower()
            if ext in (".jsx", ".tsx", ".html", ".vue", ".svelte"):
                try:
                    from jsx_tag_checker import check_jsx_tags
                    errors = check_jsx_tags(fp)
                except Exception as e:
                    errors = [{"line": "?", "message": str(e)}]
                for err in errors:
                    line_no = err.get("line", "?")
                    msg = err.get("message", str(err))
                    print(f"  {RED}âœ–{RST}  {os.path.basename(fp)}:{BOLD}{line_no}{RST}  {msg}")
                    total_errors += 1
            elif ext == ".py":
                try:
                    with open(fp, "r", encoding="utf-8", errors="replace") as _f:
                        src = _f.read()
                    _ast.parse(src, filename=fp)
                except SyntaxError as se:
                    print(f"  {RED}âœ–{RST}  {os.path.basename(fp)}:{BOLD}{se.lineno}{RST}  {se.msg}")
                    total_errors += 1
                except Exception:
                    pass
        if total_errors == 0:
            print(f"  {GREEN}âœ”{RST}  No syntax errors found.")
        else:
            print(f"\n  {RED}{total_errors} error(s) found.{RST}")
        return

    # â”€â”€ INDENTATION CHECK â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "indentation":
        from indentation import analyze_indentation_scopes
        all_files = _collect_all_files()
        matched = _fuzzy_match_files(active_directive, all_files) or all_files
        hdr("Indentation Check", os.getcwd())
        total_issues = 0
        for fp in matched:
            ext = os.path.splitext(fp)[1].lower()
            if ext not in (".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css"):
                continue
            try:
                issues = analyze_indentation_scopes(fp)
            except Exception:
                issues = []
            for iss in issues:
                line_no = iss.get("line", iss.get("lineno", "?"))
                msg = iss.get("message", iss.get("issue", str(iss)))
                print(f"  {YELLOW}âš {RST}  {os.path.basename(fp)}:{BOLD}{line_no}{RST}  {msg}")
                total_issues += 1
        if total_issues == 0:
            print(f"  {GREEN}âœ”{RST}  No indentation issues found.")
        else:
            print(f"\n  {YELLOW}{total_issues} issue(s) found.{RST}")
        return

    # â”€â”€ SMOKETEST â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if intent == "smoketest":
        hdr("Smoketest", os.getcwd())
        # Try autoloop smoketest runner first
        try:
            from autoloop import run_smoketest
            results = run_smoketest(os.getcwd())
            if isinstance(results, list):
                passed = sum(1 for r in results if r.get("passed"))
                failed = len(results) - passed
                for r in results:
                    status = f"{GREEN}PASS{RST}" if r.get("passed") else f"{RED}FAIL{RST}"
                    name = r.get("name", r.get("file", "?"))
                    print(f"  [{status}]  {name}")
                print(f"\n  {GREEN}{passed} passed{RST}  {RED}{failed} failed{RST}")
            elif isinstance(results, str):
                print(results)
            return
        except (ImportError, AttributeError):
            pass
        # Fallback: detect and run project test commands
        cwd = os.getcwd()
        ran = False
        if os.path.exists(os.path.join(cwd, "pytest.ini")) or os.path.exists(os.path.join(cwd, "pyproject.toml")):
            import subprocess
            res = subprocess.run(["python", "-m", "pytest", "--tb=short", "-q"], capture_output=True, text=True, cwd=cwd)
            print(res.stdout or res.stderr)
            ran = True
        elif os.path.exists(os.path.join(cwd, "package.json")):
            import subprocess, json as _json
            try:
                pkg = _json.loads(open(os.path.join(cwd, "package.json")).read())
                if "test" in pkg.get("scripts", {}):
                    res = subprocess.run(["npm", "test", "--", "--watchAll=false"], capture_output=True, text=True, cwd=cwd)
                    print(res.stdout or res.stderr)
                    ran = True
            except Exception:
                pass
        if not ran:
            warn("No test runner detected. Add pytest.ini or a 'test' script in package.json.")
        return

    # â”€â”€ PATCH (default) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Uses rapidfuzz graph match â†’ LLM extract â†’ diff â†’ confirm
    try:
        from find_nearest_nodes import load_graph_nodes
        from comment_blocks import scan_project_for_comment_blocks, comment_nodes_as_graph_nodes
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio
    except ImportError as ie:
        warn(f"Missing dependency: {ie}. Run: pip install pyslick[local]")
        return

    print(f"{DIM}Scanning projectâ€¦{RST}")
    nodes = load_graph_nodes() or []

    if not nodes:
        comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
        nodes = comment_nodes_as_graph_nodes(comment_nodes_raw)
        if not nodes:
            warn("No graph found. Run: graphify extract . --code-only")
            return
        print(f"{DIM}Using {len(nodes)} comment blocks{RST}")
    else:
        print(f"{DIM}Found {len(nodes)} symbols{RST}")

    expanded = maybe_expand_query(directive)
    if expanded != directive:
        print(f"{DIM}Expanded: {expanded}{RST}")

    labels      = [n["label"] for n in nodes]
    raw_results = process.extract(expanded, labels, scorer=WRatio, limit=5)

    print("\nMatches:")
    for match, score, index in raw_results:
        node = nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            cn = node["_comment_node"]
            print(f"  [{score:5.1f}%] {node['label'][:40]} â†’ {cn.file}")
        else:
            print(f"  [{score:5.1f}%] {node['label'][:40]}")

    file_paths: list[str] = []
    for _, _, index in raw_results:
        node = nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            path = node["_comment_node"].file
        else:
            path = node.get("file") or node.get("path")
        if path and os.path.isfile(path) and path not in file_paths:
            file_paths.append(path)

    if not file_paths:
        warn("No files found for patch.")
        return

    print("\nFiles:")
    for i, fp in enumerate(file_paths):
        print(f"  [{i}] {fp}")

    choice = input(f"\nSelect file (or Enter to skip): ").strip()
    if not choice:
        return

    if choice.isdigit():
        idx = int(choice)
        if idx >= len(file_paths):
            warn("Invalid selection.")
            return
        target_file = file_paths[idx]
    else:
        target_file = next((fp for fp in file_paths if choice.lower() in fp.lower()), None)
        if not target_file:
            warn(f"No match for '{choice}'")
            return

    print(f"Selected: {target_file}")

    content = tool_get_file(target_file)
    if content.startswith("ERROR"):
        err(content)
        return

    find_str = replace_str = None
    try:
        from llm import maybe_extract_find_replace
        find_str, replace_str = maybe_extract_find_replace(directive, content)
    except Exception as e:
        warn(f"LLM extraction failed: {e}")

    if find_str and replace_str:
        print(f"\n{YELL}Find:{RST}    {find_str[:150]}{'...' if len(find_str) > 150 else ''}")
        print(f"{GREEN}Replace:{RST} {replace_str[:150]}{'...' if len(replace_str) > 150 else ''}")
        confirm = input(f"\nApply patch? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            warn("Patch aborted.")
            return
    else:
        warn("LLM couldn't extract find/replace automatically. Switching to manual mode.")
        lines = content.splitlines()[:50]
        for i, line in enumerate(lines, 1):
            print(f"{i:3d}: {line}")
        find_str    = input(f"\nFind: ").strip()
        replace_str = input(f"Replace: ").strip()

    if not find_str or not replace_str:
        warn("Missing find/replace â€” no changes made.")
        return

    patch = {
        "file":        target_file,
        "find":        find_str,
        "replace":     replace_str,
        "explanation": f"Local patch for: {directive}",
    }
    _present_and_apply_patch(patch, directive)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# AGENTIC LOOP  (Anthropic / NVIDIA API path)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def run_agent(directive: str, root: str = ".") -> None:
    os.chdir(root) if root != "." else None

    print(f"{BOLD}Agent: {directive}{RST}")

    # â”€â”€ Priority: relay â†’ API â†’ local LLM â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if _relay_active():
        print(f"{DIM}Relay active â€” sending context to browser extension{RST}")
        try:
            from .response_watcher import register
            register()
        except Exception:
            pass
        _run_relay_agent(directive)
        return

    provider_name, provider = _get_provider()
    if not provider:
        print(f"{DIM}No API key â€” using local LLM{RST}")
        _run_local_agent(directive)
        return

    print(f"{DIM}Using {provider_name.upper()} ({provider['model']}){RST}")

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Directive: {directive}\n\n"
                f"Project root: {os.path.abspath('.')}\n\n"
                "Use the tools to explore the project and find exactly what needs "
                "to change. Then output your JSON patch proposal."
            ),
        }
    ]

    # â”€â”€ Agentic tool loop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        hdr(f"Round {round_num}/{MAX_TOOL_ROUNDS}", "Thinkingâ€¦")

        try:
            response = _call_api(messages, use_tools=True)
        except RuntimeError as e:
            err(str(e))
            if "401" in str(e) or "authentication" in str(e).lower():
                print(f"\n  {YELL}Tip: make sure ANTHROPIC_API_KEY is set in your environment.{RST}")
                print(f"  {YELL}Or unset it to use local LLM mode.{RST}")
            return

        content     = response.get("content", [])
        stop_reason = response.get("stop_reason", "")
        text_out    = _extract_text(content)
        tool_uses   = _extract_tool_uses(content)

        if text_out:
            print(f"\n{DIM}  Claude:{RST} {text_out[:600]}")

        # â”€â”€ No more tool calls â†’ extract patch proposal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if stop_reason == "end_turn" or not tool_uses:
            patch = _parse_patch(text_out or _extract_text(content))
            if patch:
                _present_and_apply_patch(patch, directive)
            else:
                warn("Agent finished without a parseable patch proposal.")
                print(f"\n  {DIM}Full response:{RST}\n  {text_out}")
            return

        # â”€â”€ Execute tool calls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        for tu in tool_uses:
            tool_name = tu.get("name", "")
            tool_args = tu.get("input", {})
            tool_id   = tu.get("id", "")

            print(f"\n  {CYAN}â†’ {tool_name}{RST}({_fmt_args(tool_args)})")
            result  = dispatch_tool(tool_name, tool_args)
            preview = result.replace("\n", " ")[:200]
            print(f"    {DIM}{preview}{RST}")

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tool_id,
                "content":     result,
            })

        messages.append({"role": "user", "content": tool_results})

    # â”€â”€ Max rounds reached â†’ ask for final answer without tools â”€â”€â”€â”€â”€â”€â”€â”€
    warn(f"Reached {MAX_TOOL_ROUNDS} tool rounds. Asking for final proposalâ€¦")
    messages.append({
        "role": "user",
        "content": (
            "You've completed your research. Now output your JSON patch proposal "
            "based on what you found, or explain why you can't locate the change."
        ),
    })
    try:
        response = _call_api(messages, use_tools=False)
        text_out = _extract_text(response.get("content", []))
        patch    = _parse_patch(text_out)
        if patch:
            _present_and_apply_patch(patch, directive)
        else:
            warn("No parseable patch after max rounds.")
            print(f"\n  {DIM}{text_out[:1000]}{RST}")
    except RuntimeError as e:
        err(str(e))


def _fmt_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        val = repr(v) if isinstance(v, str) else str(v)
        if len(val) > 50:
            val = val[:47] + "â€¦'"
        parts.append(f"{k}={val}")
    return ", ".join(parts)


def _parse_patch(text: str) -> dict | None:
    """Extract a JSON patch object from model text."""
    if not text:
        return None
    cleaned   = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    start     = cleaned.find("{")
    end       = cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    candidate = cleaned[start:end+1]
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict) and "file" in obj and "find" in obj and "replace" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _present_and_apply_patch(patch: dict, directive: str) -> None:
    """Show the diff, ask ONE confirmation question, then apply."""
    from patchit import read_file, write_with_safety
    from pyslick import git_checkpoint

    file_path   = patch.get("file")
    find_str    = patch.get("find")
    replace_str = patch.get("replace")
    explanation = patch.get("explanation", "")

    hdr("Proposal", "Patch ready for review")
    print(f"  {BOLD}File:{RST}       {file_path}")
    print(f"  {BOLD}Why:{RST}        {explanation}\n")

    if not file_path or not find_str or not replace_str:
        warn("Patch is incomplete (missing file / find / replace).")
        if explanation:
            print(f"  {DIM}{explanation}{RST}")
        return

    if not os.path.isfile(file_path):
        err(f"File not found: {file_path}")
        return

    content = read_file(file_path)

    if find_str not in content:
        err(f"FIND string not found in {file_path}.")
        print(f"\n  {YELL}Looking for:{RST}")
        print(f"  {DIM}{find_str[:300]}{RST}")
        lines       = content.splitlines()
        first_token = find_str.split()[0] if find_str.split() else ""
        if first_token:
            hits = [f"  L{i+1}: {l}" for i, l in enumerate(lines) if first_token in l][:5]
            if hits:
                print(f"\n  {DIM}Lines containing '{first_token}':{RST}")
                print("\n".join(hits))
        return

    modified = content.replace(find_str, replace_str, 1)

    diff = list(difflib.unified_diff(
        content.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=3,
    ))

    print(f"{DIM}{'â”€'*60}{RST}")
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line.rstrip()}{RST}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line.rstrip()}{RST}")
        else:
            print(f"{DIM}{line.rstrip()}{RST}")
    print(f"{DIM}{'â”€'*60}{RST}")

    confirm = input(
        f"\n{BOLD}  â¸  Apply this patch? (yes/no): {RST}"
    ).strip().lower()

    if confirm not in ("yes", "y"):
        warn("Patch aborted by user â€” no files changed.")
        return

    write_ok = write_with_safety(file_path, modified)
    if write_ok:
        ok(f"Patch applied to {file_path}")
        commit_msg = f"resolving: {directive}" if directive else None
        git_checkpoint(commit_msg)
    else:
        err("Write blocked (possible syntax error). File unchanged.")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ENTRY POINT
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def main():
    parser = argparse.ArgumentParser(
        prog="pyslick agent",
        description="Agentic AI coding assistant â€” finds and proposes patches autonomously.",
    )
    parser.add_argument(
        "directive", nargs="+",
        help="Natural language description of what you want to change.",
    )
    parser.add_argument(
        "--root", default=".",
        help="Project root to work in (default: cwd).",
    )
    args      = parser.parse_args()
    directive = " ".join(args.directive)
    run_agent(directive, root=args.root)


if __name__ == "__main__":
    main()
