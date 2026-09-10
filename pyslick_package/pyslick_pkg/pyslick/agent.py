#!/usr/bin/env python3
"""
agent.py — PySlick Agentic AI Session
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

─────────────────────────────────────────────────────────────────────────
TOOL VOCABULARY  (PowerShell-equivalent, all local, all read-only)
─────────────────────────────────────────────────────────────────────────

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
      block's label, start/end lines, and a code preview — the AI uses
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

─────────────────────────────────────────────────────────────────────────
FLOW
─────────────────────────────────────────────────────────────────────────

  1. Send the directive + tool schema to Claude (Anthropic API)
  2. Claude returns a PLAN (which tools to call, why)
  3. Execute each tool call locally (no data leaves your machine)
  4. Feed results back to Claude for synthesis
  5. Claude proposes: file, find_str, replace_str, explanation
  6. Show diff → ONE confirmation → patchit writes it
  7. git checkpoint

The tool calls in steps 2-4 loop up to MAX_TOOL_ROUNDS times so the
agent can react to what it finds (e.g. "that file had no className,
let me check the CSS file instead").

─────────────────────────────────────────────────────────────────────────
LOCAL LLM MODE (no API key)
─────────────────────────────────────────────────────────────────────────

When no ANTHROPIC_API_KEY / NVIDIA_API_KEY is set, the agent falls back
to _run_local_agent(). That function uses intent_vocab.json to route the
query to the right handler — no reasoning required from the 124M model.

Intent routing order (first match wins):
  help         → print pyslick command docs
  git          → show git status / push
  list_files   → walk directory, show files + their main symbols
  comments     → show every comment in the matched file
  nearest      → find_nearest_nodes around a line or symbol
  scan_function→ print a named function end-to-end with comments
  graph        → ast_query / graphify call graph
  connect      → cross-file call-graph walk
  file_info    → purpose + key functions + first 3 lines per function
  patch        → fuzzy match → LLM find/replace → diff → confirm

The vocab file lives next to this file: intent_vocab.json
"""

from __future__ import annotations

import os
import sys
import json
import re
import io
import difflib
import argparse
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

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

def hdr(phase: str, title: str):
    print(f"\n{BOLD}{CYAN}━━  {phase}  {RST}{BOLD}{title}{RST}")
    print(f"{DIM}{'─' * 60}{RST}")

def ok(msg):   print(f"{GREEN}  ✔ {msg}{RST}")
def warn(msg): print(f"{YELL}  ⚠ {msg}{RST}")
def err(msg):  print(f"{RED}  ✖ {msg}{RST}")

# ── API providers ───────────────────────────────────────────────────────────
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
    },
}

DEFAULT_PROVIDER = "anthropic"
MAX_TOOL_ROUNDS  = 6

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out",
}
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json"}


# ═════════════════════════════════════════════════════════════════════════
# LOCAL TOOL IMPLEMENTATIONS
# ═════════════════════════════════════════════════════════════════════════

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


def tool_grep(path: str, patterns: list[str], context: int = 2) -> str:
    """Search file for patterns with N lines of context."""
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
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
                preview = n.code_preview[:120].replace("\n", " ↵ ")
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
                out.append(f"    calls → {', '.join(callees)}")
            if callers:
                out.append(f"    called by ← {', '.join(callers)}")
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
        return output if output else "OK — no JSX tag mismatches found."
    except ImportError:
        return "(jsx_tag_checker not available)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_llm_expand(directive: str) -> str:
    """Expand directive using local LLM (if available)."""
    try:
        from llm import maybe_expand_query, is_available
        if not is_available():
            return f"(LLM not available — using directive as-is: {directive})"
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
            return f"(LLM not available — cannot auto-extract patch)"
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


# ── dispatcher ─────────────────────────────────────────────────────────────

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
}


def dispatch_tool(name: str, args: dict) -> str:
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        result = impl(args)
        # Truncate very large outputs so they don't blow the context window
        if isinstance(result, str) and len(result) > 12_000:
            result = result[:12_000] + f"\n… [truncated — {len(result)} chars total]"
        return result
    except Exception as e:
        return f"ERROR executing {name}: {e}"


# ═════════════════════════════════════════════════════════════════════════
# ANTHROPIC API  (tool-use / function-calling)
# ═════════════════════════════════════════════════════════════════════════

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
calls to explore the project. You NEVER write files directly — you always \
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
  "find": "<exact substring currently in the file — must be unique enough to locate it>",
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


def _call_api(messages: list[dict], use_tools: bool = True, provider_name: str = None) -> dict:
    provider_name, provider = _get_provider()
    if not provider:
        raise RuntimeError(
            "No API key found. Set ANTHROPIC_API_KEY or NVIDIA_API_KEY, "
            "or use local LLM mode by unsetting all API keys."
        )

    api_url = provider["api_url"]
    model   = provider["model"]
    key_env = provider["key_env"]
    api_key = os.environ.get(key_env)

    if provider_name == "nvidia":
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
        }
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
            if provider_name == "nvidia":
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


# ═════════════════════════════════════════════════════════════════════════
# INTENT VOCAB — loads intent_vocab.json once at import time
# ═════════════════════════════════════════════════════════════════════════

def _load_vocab() -> dict:
    """Load intent_vocab.json from the same directory as this file."""
    vocab_path = Path(THIS_DIR) / "intent_vocab.json"
    if not vocab_path.exists():
        # Graceful degradation — return minimal inline vocab
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
            warn(f"Feedback noted ('{matched_signal}') → Reduced '{last_intent}' weight to {new_w:.2f}.")

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
    return " • ".join(parts)


def _load_learned() -> dict:
    """Load learned_intents.json — maps exact query strings to intent names."""
    if not _LEARNED_PATH.exists():
        return {}
    try:
        return json.loads(_LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_learned_intent(query: str, intent: str) -> None:
    """
    Persist a user-confirmed query → intent mapping to learned_intents.json.
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
      - Exact multi-word substring match  → 100% confidence (fastest path)
      - Single-word patterns scored word-by-word so a short word like 'patch'
        cannot outscore a typo of a longer phrase like 'how it connects'.
    """
    intent, _, _ = _classify_intent_with_confidence(directive)
    return intent


def _classify_intent_with_confidence(directive: str) -> tuple:
    """
    Hybrid three-tier intent classification. Returns (intent, confidence, top3).

    Tier 0 — learned_intents.json  (user-confirmed corrections, 100% confidence)
    Tier 1 — Exact vocab substring  (intent_vocab.json, 100% confidence)
    Tier 2 — Python fuzzy           (rapidfuzz word-level, 0-100%)
    Tier 3 — LLM arbitration        (called only when Tier 2 gives 55-84%)

    Returns:
        intent     : str    — winning intent name
        confidence : float  — 0-100, 100 = exact match
        top3       : list   — [(intent_name, score), ...] top candidates for did-you-mean
    """
    dl = directive.lower().strip()
    intents = _VOCAB.get("intents", {})

    priority = [
        "help", "git", "run_info", "list_files", "comments", "nearest",
        "scan_function", "graph", "connect", "file_info", "patch",
    ]

    # ── Tier 0: learned corrections — fastest possible win ───────────────
    learned = _load_learned()
    if dl in learned and learned[dl] in priority:
        return learned[dl], 100.0, [(learned[dl], 100.0)]

    # ── Tier 1: exact vocab match — word-boundary check for single-word patterns ─
    import re as _re_t1
    for intent_name in priority:
        cfg = intents.get(intent_name, {})
        patterns = cfg.get("require_any", [])
        excludes = cfg.get("exclude_if", [])
        # Check excludes first (still plain substring — exclusions are always multi-word)
        if any(ex in dl for ex in excludes):
            continue
        for p in patterns:
            if " " in p:
                # Multi-word phrase — plain substring is fine (specific enough)
                matched = p in dl
            else:
                # Single word — require whole-word match so "patch" doesn't fire on "patchit"
                matched = bool(_re_t1.search(r'\b' + _re_t1.escape(p) + r'\b', dl))
            if matched:
                return intent_name, 100.0, [(intent_name, 100.0)]

    # ── Tier 2: Python fuzzy — word overlap & token scoring × intent weight ─
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

        # High confidence — trust Python, no LLM needed
        if best_score >= 85.0:
            return best_intent, best_score, top3

        # ── Tier 3: LLM arbitration — only in the uncertain 55-84% zone ──────
        if 55.0 <= best_score < 85.0:
            try:
                from llm import maybe_classify_intent
                # Send top-3 intent candidates + their vocab examples to the LLM
                # (not all 11 — keep the prompt small for the 124M model)
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
            except ImportError:
                pass

        return best_intent, best_score, top3

    except ImportError:
        pass

    return "patch", 0.0, [("patch", 0.0)]


def _collect_all_files(root: str = ".") -> list[str]:
    """Walk project tree and return all source file paths (skips binary, build, and backup files)."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith(".")
            and not d.startswith("pyslick_backup")
        ]
        for fn in sorted(filenames):
            ext = Path(fn).suffix.lower()
            if ext in CODE_EXTS or (ext == "" and not fn.startswith(".")):
                files.append(os.path.normpath(os.path.join(dirpath, fn)))
    return files


def _fuzzy_match_files(directive: str, all_files: list[str]) -> list[str]:
    """
    Extract candidate file names from the directive and fuzzy-match them
    against the real file list. Uses the vocab's strip_words list so
    query noise doesn't pollute candidates.
    Prioritizes exact/root file matches first.
    """
    try:
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio
    except ImportError:
        words = directive.lower().split()
        matched = []
        for w in words:
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
    max_results = cfg.get("max_results", 3)

    tokens = [
        w for w in re.split(r"[\s\-_./\\]+", directive.lower())
        if len(w) >= min_len and w not in strip_words
    ]

    matched: list[str] = []
    for token in tokens:
        basenames = [os.path.basename(f) for f in all_files]
        hits = process.extract(token, basenames, scorer=WRatio, limit=max_results)
        for base_match, score, idx in hits:
            if score >= min_score:
                full_path = all_files[idx]
                if full_path not in matched:
                    matched.append(full_path)

    # Sort so exact matches and shorter root paths come first
    dl = directive.lower()
    matched.sort(key=lambda p: (
        0 if os.path.basename(p).lower() in dl else 1,
        len(Path(p).parts),
        len(p)
    ))
    return matched


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

        print(f"  {DIM}Lines: {total_lines}  │  Showing: L{s}-L{e}{RST}")
        print(f"  {CYAN}PowerShell:{RST} {BOLD}{ps_cmd}{RST}\n")

        for idx in range(s - 1, e):
            ln = idx + 1
            line = raw_lines[idx]
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                print(f"  {DIM}{ln:4d}│{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}│{RST} {line}")
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
                print(f"  {DIM}{ln:4d}│{RST} {CYAN}{line}{RST}")
            else:
                print(f"  {DIM}{ln:4d}│{RST} {line}")

        if total_lines > max_show:
            print(f"\n  {DIM}... ({total_lines - max_show} more lines. Run: Get-Content '{filepath}' or pyslick lines '{filepath}'){RST}")
        return

    # 3. Structured summary for large code files (functions / classes)
    _print_file_summary(filepath, directive_lower)


def _print_file_summary(filepath: str, directive_lower: str = "") -> None:
    """
    Print a structured summary of one file:
      • total lines
      • key functions/classes with first 3 lines of body + inline comments
      • relevant comment blocks (filtered by directive words)
    """
    content = tool_get_file(filepath)
    if content.startswith("ERROR"):
        print(f"  {RED}{content}{RST}")
        return

    raw_lines = content.splitlines()
    print(f"  {DIM}Lines: {len(raw_lines)}{RST}")
    print(f"  {CYAN}PowerShell:{RST} {BOLD}Get-Content '{filepath}'{RST}")

    # ── functions and classes ──────────────────────────────────────────
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
                print(f"      {DIM}» {doc_preview}{RST}")

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
            print(f"  {DIM}{idx+1:4d}│{RST} {raw_lines[idx]}")


def _print_all_comments(filepath: str) -> None:
    """
    Print every comment line in a file — inline (#//) and block (''' / /* */).
    Also prints the first 3 lines of any function immediately following
    a comment block.
    """
    content = tool_get_file(filepath)
    if content.startswith("ERROR"):
        print(f"  {RED}{content}{RST}")
        return

    raw_lines = content.splitlines()
    print(f"\n{BOLD}Comments in {filepath}{RST}  {DIM}({len(raw_lines)} lines total){RST}\n")

    in_block = False
    block_delim = None
    block_buf: list[str] = []
    block_start = 0

    def flush_block(end_line: int):
        if block_buf:
            print(f"  {DIM}L{block_start}-{end_line}  block comment:{RST}")
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
                in_block  = True
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
                    print(f"    {DIM}→ {next_line[:90]}{RST}")
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
    print(f"\n{YELL}  ⚡ Did you mean: {BOLD}{label}{RST}{YELL}? ({intent}, {confidence:.0f}% confident){RST}")
    answer = input(f"  {DIM}(y/n): {RST}").strip().lower()

    if answer in ("y", "yes", ""):
        _save_learned_intent(directive, intent)
        return intent

    # User said no — show numbered top-3
    print(f"\n{CYAN}  Pick what you meant:{RST}")
    choices = top3[:3]
    for i, (name, score) in enumerate(choices, 1):
        lbl = INTENT_LABELS.get(name, name)
        print(f"  {BOLD}[{i}]{RST} {lbl}  {DIM}({score:.0f}%){RST}")
    print(f"  {BOLD}[0]{RST} None of these — skip")

    while True:
        pick = input(f"  {DIM}Enter number: {RST}").strip()
        if pick == "0":
            print(f"  {DIM}Skipping — try rephrasing your query.{RST}")
            return intent
        if pick.isdigit() and 1 <= int(pick) <= len(choices):
            chosen = choices[int(pick) - 1][0]
            _save_learned_intent(directive, chosen)
            ok(f"Got it — saved '{directive}' → {chosen} for next time.")
            return chosen
        print(f"  {YELL}Enter a number between 0 and {len(choices)}.{RST}")


# ═════════════════════════════════════════════════════════════════════════
# LOCAL LLM AGENT (no API key required)
# ═════════════════════════════════════════════════════════════════════════

def _run_local_agent(directive: str) -> None:
    """
    Vocab-driven local agent for the 124M-param model.

    Intent routing is a three-tier hybrid pipeline in _classify_intent_with_confidence():
      Tier 0 — learned_intents.json  (user-corrected queries, 100% confidence)
      Tier 1 — Exact vocab substring  (intent_vocab.json, 100% confidence)
      Tier 2 — Python rapidfuzz fuzzy (word-level scoring, 0-100%)
      Tier 3 — LLM arbitration        (only in 55-84% uncertain zone)

    When confidence is 55-84%, the agent asks 'Did you mean X?' before running.
    If the user corrects it, the choice is saved to learned_intents.json.

      help          → pyslick command docs
      git           → git status + optional push prompt
      run_info      → how to run / start / launch project
      list_files    → directory walk + main symbols per file
      comments      → all comments in matched file
      nearest       → find_nearest_nodes around a symbol/line
      scan_function → full function body printed with comments
      graph         → ast_query call graph
      connect       → cross-file call-graph walk
      file_info     → purpose + key functions + first 3 lines
      patch         → fuzzy match → LLM find/replace → diff → confirm
    """
    # Detect frustration / correction from user
    cleaned_directive, was_frustrated, prev_intent = _detect_frustration_and_correction(directive)
    active_directive = cleaned_directive

    # LLM optional — classification still works without it (just skips Tier 3)
    try:
        from llm import is_available, maybe_expand_query
        _llm_ready = is_available()
    except ImportError:
        _llm_ready = False
        def maybe_expand_query(d): return d  # no-op fallback

    intent, confidence, top3 = _classify_intent_with_confidence(active_directive)
    dl = active_directive.lower()
    print(f"{DIM}  intent → {intent}  ({confidence:.0f}%){RST}")

    if was_frustrated:
        _adjust_intent_weight(intent, +0.08)
        _save_learned_intent(directive, intent)
        _save_learned_intent(active_directive, intent)
        ok(f"Learned correction: '{active_directive}' → {intent}")

    # Show 'did you mean?' when confidence is in the uncertain zone (55-84%)
    if 55.0 <= confidence < 85.0 and not was_frustrated:
        intent = _ask_did_you_mean(active_directive, intent, confidence, top3)
        dl = active_directive.lower()

    # Save session for context tracking
    _save_session(directive, intent)

    # ── HELP ──────────────────────────────────────────────────────────
    if intent == "help":
        result = tool_pyslick_help()
        print(result)
        return

    # ── GIT ───────────────────────────────────────────────────────────
    if intent == "git":
        hdr("Git", "Status")
        status = tool_pyslick_status()
        print(status)

        if any(kw in dl for kw in ["push", "commit", "checkpoint"]):
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
                f"\n{BOLD}  ⏸  Create checkpoint '{commit_msg[:70]}'? (yes/no): {RST}"
            ).strip().lower()
            if confirm in ("y", "yes"):
                result = tool_pyslick_checkpoint(commit_msg)
                ok(result)
    # ── RUN INFO (how to run project, repo, directory, or file) ─────────
    if intent == "run_info":
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
                print(f"    • Direct: {BOLD}python {target_f}{RST}")
                print(f"    • Module: {BOLD}python -m {Path(target_f).stem}{RST}")
            elif ext in (".js", ".mjs", ".cjs"):
                if "electron" in target_f.lower() or os.path.exists("electron-main.js"):
                    print(f"    • Electron: {BOLD}npx electron {target_f}{RST} (or {BOLD}{pm} start{RST})")
                print(f"    • Node:     {BOLD}node {target_f}{RST}")
            elif ext in (".ts", ".tsx"):
                print(f"    • TypeScript: {BOLD}npx ts-node {target_f}{RST}")
            elif ext == ".html":
                print(f"    • Open in browser or local server: {BOLD}npx serve .{RST}")
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
                        print(f"    • {BOLD}{run_prefix}{RST} → {DIM}{s_cmd}{RST}")
                    found_info = True
            except Exception:
                pass

        # Check for Python projects
        py_files = [f for f in os.listdir(".") if f.endswith(".py")]
        if os.path.exists("pyproject.toml") or os.path.exists("setup.py") or os.path.exists("requirements.txt") or py_files:
            print(f"\n  {BOLD}Python Environment:{RST}")
            if os.path.exists("requirements.txt"):
                print(f"    • Install deps: {BOLD}pip install -r requirements.txt{RST}")
            if os.path.exists("setup_and_install.py"):
                print(f"    • One-shot setup: {BOLD}python setup_and_install.py{RST}")
            for main_cand in ["main.py", "app.py", "cli.py", "server.py", "index.py", "electron-main.js"]:
                if os.path.exists(main_cand):
                    cmd = f"python {main_cand}" if main_cand.endswith(".py") else f"node {main_cand}"
                    print(f"    • Run entry point: {BOLD}{cmd}{RST}")
            if os.path.exists("pytest.ini") or os.path.exists("tests"):
                print(f"    • Run tests: {BOLD}pytest{RST}")
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

    # ── LIST FILES ────────────────────────────────────────────────────
    if intent == "list_files":
        # Determine root — check if user mentioned a specific directory
        root_match = re.search(
            r'(?:in|into|inside|under|directory|dir|folder)[:\s]+["\']?([./\w\-]+)["\']?',
            dl,
        )
        root = root_match.group(1).strip() if root_match else "."
        if not os.path.isdir(root):
            root = "."

        hdr("List Files", root)
        all_files = _collect_all_files(root)
        code_files = [f for f in all_files if Path(f).suffix in CODE_EXTS]
        print(f"{DIM}  {len(code_files)} source files found{RST}\n")

        wants_symbols = any(kw in dl for kw in ["method", "object", "function", "symbol", "main"])

        for fp in sorted(code_files):
            print(f"  {BOLD}{fp}{RST}")
            if wants_symbols:
                content = tool_get_file(fp)
                if not content.startswith("ERROR"):
                    raw = content.splitlines()
                    syms: list[str] = []
                    for line in raw:
                        m = re.match(
                            r"^\s*(export\s+)?(async\s+)?(?:function|def|class)\s+(\w+)",
                            line,
                        )
                        if m:
                            syms.append(m.group(3))
                    if syms:
                        print(f"    {DIM}{', '.join(syms[:8])}{RST}")
        return

    # ── COMMENTS ──────────────────────────────────────────────────────
    if intent == "comments":
        all_files  = _collect_all_files()
        matched    = _fuzzy_match_files(directive, all_files)

        if not matched:
            warn("No file matched. Try naming a file explicitly.")
            return

        # If multiple matches, let user pick
        target = matched[0]
        if len(matched) > 1:
            print("Multiple files matched:")
            for i, f in enumerate(matched):
                print(f"  [{i}] {f}")
            choice = input("Select (or Enter for first): ").strip()
            if choice.isdigit() and int(choice) < len(matched):
                target = matched[int(choice)]

        _print_all_comments(target)
        return

    # ── NEAREST NODE ──────────────────────────────────────────────────
    if intent == "nearest":
        try:
            from find_nearest_nodes import load_graph_nodes, find_closest_graph_nodes
        except ImportError:
            warn("find_nearest_nodes module not available.")
            return

        # Extract a line number if present
        line_match = re.search(r"(?:line|l)\s*(\d+)", dl)
        line_num   = int(line_match.group(1)) if line_match else None

        # Extract a symbol name if present  e.g. "closest to run_agent"
        sym_match  = re.search(r"closest to\s+[(\"]?(\w+)[)\"]?", dl)
        sym_name   = sym_match.group(1) if sym_match else None

        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(directive, all_files)
        target    = matched[0] if matched else None

        nodes = load_graph_nodes()
        if not nodes:
            warn("No graph loaded. Run: graphify extract . --code-only")
            return

        hdr("Nearest Nodes", sym_name or str(line_num) or directive)

        if sym_name:
            query = sym_name
        elif line_num and target:
            query = f"line {line_num} in {target}"
        else:
            query = directive

        results = find_closest_graph_nodes(nodes, query, top_k=5)
        for r in results:
            node = r if isinstance(r, dict) else r.symbol
            print(
                f"  {CYAN}{node.get('label', node.get('name','?'))}{RST}  "
                f"{DIM}L{node.get('start_line','?')}-{node.get('end_line','?')}{RST}"
            )
            if node.get("docstring"):
                print(f"    {DIM}{node['docstring'][:80]}{RST}")
            callees = sorted(node.get("callees", set()))
            callers = sorted(node.get("callers", set()))
            if callees:
                print(f"    calls → {', '.join(list(callees)[:4])}")
            if callers:
                print(f"    called by ← {', '.join(list(callers)[:4])}")
        return

    # ── SCAN FUNCTION (print end-to-end with comments) ────────────────
    if intent == "scan_function":
        # Pull out the function/class name from the directive
        fn_match = re.search(
            r"(?:function|def|class|method|the)\s+['\"]?(\w+)['\"]?",
            dl,
        )
        fn_name = fn_match.group(1) if fn_match else None

        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(directive, all_files)
        target    = matched[0] if matched else None

        if not fn_name and not target:
            warn("Could not identify a function name or file. Be more specific.")
            return

        hdr("Scan Function", fn_name or directive)

        # If we have a Python file, use AST to find the exact function
        if target and target.endswith(".py") and fn_name:
            content    = tool_get_file(target)
            raw_lines  = content.splitlines()
            try:
                import ast
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    if is_fn and node.name.lower() == fn_name.lower():
                        start = node.lineno - 1          # 0-indexed
                        end   = getattr(node, "end_lineno", start + 60)
                        print(f"{BOLD}{target}  L{node.lineno}-{end}{RST}\n")
                        for ln_idx, ln in enumerate(raw_lines[start:end], start=node.lineno):
                            # Highlight comment lines
                            stripped = ln.strip()
                            if stripped.startswith("#") or stripped.startswith("//"):
                                print(f"  {ln_idx:4d}  {CYAN}{ln}{RST}")
                            else:
                                print(f"  {ln_idx:4d}  {ln}")
                        return
                warn(f"Function '{fn_name}' not found in {target}. Showing file summary instead.")
            except SyntaxError as se:
                warn(f"Syntax error in {target}: {se}")

        # Generic fallback: grep for the function definition
        if target and fn_name:
            content   = tool_get_file(target)
            raw_lines = content.splitlines()
            for i, line in enumerate(raw_lines):
                if re.search(rf"\bdef\s+{fn_name}\b|\bfunction\s+{fn_name}\b|\bclass\s+{fn_name}\b", line, re.IGNORECASE):
                    end = min(i + 80, len(raw_lines))
                    print(f"{BOLD}{target}  L{i+1}-~{end}{RST}\n")
                    for ln_idx, ln in enumerate(raw_lines[i:end], start=i+1):
                        stripped = ln.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            print(f"  {ln_idx:4d}  {CYAN}{ln}{RST}")
                        else:
                            print(f"  {ln_idx:4d}  {ln}")
                        # Stop at the next top-level def/class (dedent back to col 0)
                        if ln_idx > i + 2 and re.match(r"^(def |class |async def )", ln):
                            break
                    return

        warn(f"Could not locate '{fn_name}'. Try: pyslick agent \"scan the entire <function> function in <file>\"")
        return

    # ── CALL GRAPH ────────────────────────────────────────────────────
    if intent == "graph":
        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(directive, all_files)
        py_files  = [f for f in (matched or all_files) if f.endswith(".py")]

        if not py_files:
            warn("No Python files found for AST graph.")
            return

        target = py_files[0]
        hdr("Call Graph", target)
        result = tool_ast_query(target, directive)
        print(result)
        return

    # ── CONNECT (cross-file call-graph) ───────────────────────────────
    if intent == "connect":
        try:
            from query import graphify_query
        except ImportError:
            warn("query module not available.")
            return

        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(directive, all_files)

        if not matched:
            warn("No files matched. Be more specific.")
            return

        hdr("Connections", " ↔ ".join(os.path.basename(f) for f in matched[:3]))

        matched = matched[:4]
        for fp in matched:
            print(f"\n{BOLD}{fp}{RST}")
            _print_file_summary(fp, dl)

        # Cross-file relationships for every pair
        if len(matched) >= 2:
            print(f"\n{DIM}Cross-file call relationships:{RST}")
            for i, f1 in enumerate(matched):
                for f2 in matched[i+1:]:
                    if not f1.endswith(".py"):
                        continue
                    try:
                        print(f"\n  {BOLD}{os.path.basename(f1)} ↔ {os.path.basename(f2)}{RST}")
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
                                    print(f"      calls → {', '.join(callees[:4])}")
                                if callers:
                                    print(f"      called by ← {', '.join(callers[:4])}")
                        else:
                            print(f"    {DIM}(no direct connections found){RST}")
                    except Exception:
                        pass
        return

    # ── FILE INFO ─────────────────────────────────────────────────────
    # ── FILE INFO (Cat / Scan / Snippet / Purpose / Line Ranges) ──────
    if intent == "file_info":
        all_files = _collect_all_files()
        matched   = _fuzzy_match_files(active_directive, all_files)

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

        # If user asks 'show all', show all matched files, otherwise show ONLY primary
        files_to_show = matched if ("show all" in dl or "all files" in dl) else [target_file]
        for fp in files_to_show:
            hdr("File Cat / Scan", fp)
            _print_file_cat_and_snippet(fp, dl, start_line=start_line, end_line=end_line)

        # If there are other matching files and not showing all, list them concisely
        if len(matched) > 1 and "show all" not in dl and "all files" not in dl:
            other_files = [os.path.basename(f) for f in matched[1:5]]
            print(f"\n  {DIM}Other matches: {', '.join(other_files)} (use 'show all' to scan all){RST}")
        return

    # ── PATCH (default) ───────────────────────────────────────────────
    # Uses rapidfuzz graph match → LLM extract → diff → confirm
    try:
        from find_nearest_nodes import load_graph_nodes
        from comment_blocks import scan_project_for_comment_blocks, comment_nodes_as_graph_nodes
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio
    except ImportError as ie:
        warn(f"Missing dependency: {ie}. Run: pip install pyslick[local]")
        return

    print(f"{DIM}Scanning project…{RST}")
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
            print(f"  [{score:5.1f}%] {node['label'][:40]} → {cn.file}")
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
        warn("Missing find/replace — no changes made.")
        return

    patch = {
        "file":        target_file,
        "find":        find_str,
        "replace":     replace_str,
        "explanation": f"Local patch for: {directive}",
    }
    _present_and_apply_patch(patch, directive)


# ═════════════════════════════════════════════════════════════════════════
# AGENTIC LOOP  (Anthropic / NVIDIA API path)
# ═════════════════════════════════════════════════════════════════════════

def run_agent(directive: str, root: str = ".") -> None:
    os.chdir(root) if root != "." else None

    print(f"{BOLD}Agent: {directive}{RST}")

    provider_name, provider = _get_provider()
    if not provider:
        print(f"{DIM}No API key — using local LLM{RST}")
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

    # ── Agentic tool loop ──────────────────────────────────────────────
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        hdr(f"Round {round_num}/{MAX_TOOL_ROUNDS}", "Thinking…")

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

        # ── No more tool calls → extract patch proposal ────────────────
        if stop_reason == "end_turn" or not tool_uses:
            patch = _parse_patch(text_out or _extract_text(content))
            if patch:
                _present_and_apply_patch(patch, directive)
            else:
                warn("Agent finished without a parseable patch proposal.")
                print(f"\n  {DIM}Full response:{RST}\n  {text_out}")
            return

        # ── Execute tool calls ─────────────────────────────────────────
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        for tu in tool_uses:
            tool_name = tu.get("name", "")
            tool_args = tu.get("input", {})
            tool_id   = tu.get("id", "")

            print(f"\n  {CYAN}→ {tool_name}{RST}({_fmt_args(tool_args)})")
            result  = dispatch_tool(tool_name, tool_args)
            preview = result.replace("\n", " ")[:200]
            print(f"    {DIM}{preview}{RST}")

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tool_id,
                "content":     result,
            })

        messages.append({"role": "user", "content": tool_results})

    # ── Max rounds reached → ask for final answer without tools ────────
    warn(f"Reached {MAX_TOOL_ROUNDS} tool rounds. Asking for final proposal…")
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
            val = val[:47] + "…'"
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

    print(f"{DIM}{'─'*60}{RST}")
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line.rstrip()}{RST}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line.rstrip()}{RST}")
        else:
            print(f"{DIM}{line.rstrip()}{RST}")
    print(f"{DIM}{'─'*60}{RST}")

    confirm = input(
        f"\n{BOLD}  ⏸  Apply this patch? (yes/no): {RST}"
    ).strip().lower()

    if confirm not in ("yes", "y"):
        warn("Patch aborted by user — no files changed.")
        return

    write_ok = write_with_safety(file_path, modified)
    if write_ok:
        ok(f"Patch applied to {file_path}")
        commit_msg = f"resolving: {directive}" if directive else None
        git_checkpoint(commit_msg)
    else:
        err("Write blocked (possible syntax error). File unchanged.")


# ═════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="pyslick agent",
        description="Agentic AI coding assistant — finds and proposes patches autonomously.",
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