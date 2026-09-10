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
      Equivalent to `pyslick graphify-query <path> <question>`.

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
"""

from __future__ import annotations

import os
import sys
import json
import re
import difflib
import argparse
import urllib.request
from pathlib import Path

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

# API Providers
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
MAX_TOOL_ROUNDS = 6

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
        from comment_blocks import scan_file_for_comment_blocks, print_scan_report
        import io, contextlib
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
            # Include first 20 lines of source
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
                "directive": {"type": "string", "description": "The user's original directive."},
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
    
    # If provider specified but key not available, try other providers
    if provider_name in PROVIDERS:
        provider = PROVIDERS[provider_name]
        if os.environ.get(provider["key_env"]):
            return provider_name, provider
    
    # Try to find any provider with an available key
    for name, provider in PROVIDERS.items():
        if os.environ.get(provider["key_env"]):
            return name, provider
    
    return None, None


def _call_api(messages: list[dict], use_tools: bool = True, provider_name: str = None) -> dict:
    provider_name, provider = _get_provider()
    if not provider:
        raise RuntimeError("No API key found. Set ANTHROPIC_API_KEY or NVIDIA_API_KEY, or use local LLM mode by unsetting all API keys.")
    
    api_url = provider["api_url"]
    model = provider["model"]
    key_env = provider["key_env"]
    api_key = os.environ.get(key_env)
    
    # Convert Anthropic format to OpenAI format for NVIDIA
    if provider_name == "nvidia":
        # Convert messages format
        openai_messages = []
        for msg in messages:
            openai_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        # Convert tools format
        openai_tools = []
        if use_tools:
            for tool in TOOL_SCHEMA:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"]
                    }
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
        # Anthropic format
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
            **provider["headers"]
        }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        api_url,
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read())
            
            # Convert NVIDIA response to Anthropic format
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
    choice = response.get("choices", [{}])[0]
    message = choice.get("message", {})
    
    content = []
    text_content = message.get("content", "")
    
    # Handle tool calls
    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        for tool_call in tool_calls:
            content.append({
                "type": "tool_use",
                "id": tool_call.get("id", ""),
                "name": tool_call.get("function", {}).get("name", ""),
                "input": json.loads(tool_call.get("function", {}).get("arguments", "{}"))
            })
    
    # Add text content if present
    if text_content:
        if isinstance(text_content, str):
            content.append({"type": "text", "text": text_content})
        elif isinstance(text_content, list):
            for item in text_content:
                if item.get("type") == "text":
                    content.append({"type": "text", "text": item.get("text", "")})
    
    return {
        "content": content,
        "stop_reason": "end_turn" if not tool_calls else "tool_use"
    }


def _extract_text(content: list[dict]) -> str:
    return "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()


def _extract_tool_uses(content: list[dict]) -> list[dict]:
    return [b for b in content if b.get("type") == "tool_use"]


# ═════════════════════════════════════════════════════════════════════════
# LOCAL LLM AGENT (no API key required)
# ═════════════════════════════════════════════════════════════════════════

def _run_local_agent(directive: str) -> None:
    """Simplified agent using only local LLM and tools."""
    from llm import is_available, maybe_expand_query
    from find_nearest_nodes import load_graph_nodes, find_closest_graph_nodes
    from comment_blocks import scan_project_for_comment_blocks, comment_nodes_as_graph_nodes
    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio
    from query import graphify_query

    if not is_available():
        print("Local LLM not available. Run: pyslick llm-status")
        return

    directive_lower = directive.lower()
    
    # Detect file connection queries (e.g., "how does X connect with Y")
    if "connect" in directive_lower and any(kw in directive_lower for kw in ["how", "what", "with", "to"]):
        # Extract potential file names from query
        words = directive_lower.split()
        file_candidates = []
        for word in words:
            if len(word) > 3 and word.replace("py", "").replace("js", "").replace("ts", "").isalnum():
                file_candidates.append(word)
        
        # Fuzzy match to actual files
        all_files = []
        for root, dirs, files in os.walk("."):
            for f in files:
                all_files.append(os.path.join(root, f))
        
        matched_files = []
        for candidate in file_candidates:
            results = process.extract(candidate, all_files, scorer=WRatio, limit=3)
            for match, score, idx in results:
                if score > 60 and match not in matched_files:
                    matched_files.append(match)
        
        if matched_files:
            print(f"Found {len(matched_files)} files matching query:")
            for i, f in enumerate(matched_files):
                print(f"  [{i}] {f}")
            
            # Show important comments and functions in each file
            print(f"\n{DIM}Scanning for important comments and functions...{RST}")
            for f in matched_files:
                print(f"\n{BOLD}{f}{RST}")
                
                # Show comment blocks related to query
                comment_blocks = scan_project_for_comment_blocks(os.path.dirname(f))
                file_comments = [c for c in comment_blocks if c.file == f]
                if file_comments:
                    # Filter comments by relevance to query
                    relevant_comments = []
                    for c in file_comments:
                        comment_lower = c.comment_text.lower()
                        for word in directive_lower.split():
                            if len(word) > 3 and word in comment_lower:
                                relevant_comments.append(c)
                                break
                    if relevant_comments:
                        print(f"  {DIM}Relevant comments:{RST}")
                        for c in relevant_comments[:3]:  # Top 3 relevant comments
                            print(f"    L{c.start_line}: {c.comment_text[:60]}")
                            # Show nearby function (first 3 lines)
                            try:
                                content = tool_get_file(f)
                                lines = content.splitlines()
                                # Find function near comment
                                for i in range(max(0, c.start_line - 5), min(len(lines), c.start_line + 10)):
                                    line = lines[i]
                                    if "def " in line or "class " in line:
                                        print(f"      → {line.strip()}")
                                        # Show next 2 lines
                                        for j in range(i+1, min(len(lines), i+3)):
                                            if j < len(lines):
                                                print(f"        {lines[j].strip()}")
                                        break
                            except Exception:
                                pass
                
                # Show key functions using find_nearest_nodes
                try:
                    nodes = load_graph_nodes()
                    if nodes:
                        file_nodes = [n for n in nodes if n.get("file") == f]
                        if file_nodes:
                            print(f"  {DIM}Key functions:{RST}")
                            for node in file_nodes[:3]:  # Top 3 functions
                                print(f"    {node['label']} (L{node.get('start_line', '?')}-{node.get('end_line', '?')})")
                                if node.get("docstring"):
                                    print(f"      {node['docstring'][:80]}")
                except Exception:
                    pass
            
            # Show connections between matched files
            print(f"\n{DIM}Analyzing connections...{RST}")
            for i, file1 in enumerate(matched_files):
                for j, file2 in enumerate(matched_files):
                    if i < j:  # Avoid duplicates
                        try:
                            print(f"\n{BOLD}{file1} ↔ {file2}{RST}")
                            results = graphify_query(file1, f"functions that call or are called by {file2}", top_k=3, depth=2)
                            if results:
                                for r in results:
                                    sym = r.symbol
                                    print(f"  {sym['name']} (L{sym['start_line']}-{sym['end_line']})")
                                    if sym.get("docstring"):
                                        print(f"    {sym['docstring'][:100]}")
                        except Exception as e:
                            pass
            return
    
    # Informational queries - show and exit
    if any(kw in directive_lower for kw in ["pyslick", "help", "how", "what", "command", "usage"]):
        result = tool_pyslick_help()
        lines = result.splitlines()[:30]
        print("\n".join(lines))
        return
    
    if any(kw in directive_lower for kw in ["git", "status", "commit", "push", "repo"]):
        result = tool_pyslick_status()
        print(result)
        return

    # Patching queries
    print(f"{DIM}Scanning {len(os.listdir('.'))} files...{RST}")
    nodes = load_graph_nodes() or []
    
    if nodes:
        print(f"{DIM}Found {len(nodes)} functions. Top 5:{RST}")
        for i, node in enumerate(nodes[:5]):
            callees = sorted(node.get("callees", set()))
            callers = sorted(node.get("callers", set()))
            print(f"  [{i}] {node['label'][:40]}")
            if node.get("file"):
                print(f"      → {node['file']}")
            if callees:
                print(f"      calls: {', '.join(callees[:2])}")
            if callers:
                print(f"      called by: {', '.join(callers[:2])}")
    else:
        comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
        comment_nodes = comment_nodes_as_graph_nodes(comment_nodes_raw)
        if comment_nodes:
            print(f"{DIM}Using {len(comment_nodes)} comment blocks{RST}")
            nodes = comment_nodes
        else:
            print("No graph found. Run: graphify extract . --code-only")
            return

    expanded = maybe_expand_query(directive)
    if expanded != directive:
        print(f"{DIM}Expanded: {expanded}{RST}")

    labels = [n["label"] for n in nodes]
    raw_results = process.extract(expanded, labels, scorer=WRatio, limit=5)

    print("\nMatches:")
    for match, score, index in raw_results:
        node = nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            cn = node["_comment_node"]
            print(f"  [{score:5.1f}%] {node['label'][:40]} → {cn.file}")
        else:
            print(f"  [{score:5.1f}%] {node['label'][:40]}")

    file_paths = []
    for match, score, index in raw_results:
        node = nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            path = node["_comment_node"].file
        else:
            path = node.get("file") or node.get("path")
        if path and os.path.isfile(path) and path not in file_paths:
            file_paths.append(path)

    if not file_paths:
        print("No files found")
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
            print("Invalid selection")
            return
        target_file = file_paths[idx]
    else:
        matched = None
        for fp in file_paths:
            if choice.lower() in fp.lower():
                matched = fp
                break
        if not matched:
            print(f"No match for '{choice}'")
            return
        target_file = matched

    print(f"Selected: {target_file}")

    content = tool_get_file(target_file)
    if content.startswith("ERROR"):
        print(content)
        return

    find_str, replace_str = None, None
    try:
        from llm import maybe_extract_find_replace
        find_str, replace_str = maybe_extract_find_replace(directive, content)
    except Exception as e:
        print(f"LLM failed: {e}")

    if find_str and replace_str:
        print(f"\n{YELL}Find:{RST}    {find_str[:150]}{'...' if len(find_str) > 150 else ''}")
        print(f"{GREEN}Replace:{RST} {replace_str[:150]}{'...' if len(replace_str) > 150 else ''}")
        confirm = input(f"\nApply patch? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            return
    else:
        print("LLM couldn't extract. Manual mode:")
        lines = content.splitlines()[:50]
        for i, line in enumerate(lines, 1):
            print(f"{i:3d}: {line}")
        find_str = input(f"\nFind: ").strip()
        replace_str = input(f"Replace: ").strip()

    if not find_str or not replace_str:
        print("Missing find/replace")
        return

    patch = {
        "file": target_file,
        "find": find_str,
        "replace": replace_str,
        "explanation": f"Patch for: {directive}"
    }

    _present_and_apply_patch(patch, directive)


# ═════════════════════════════════════════════════════════════════════════
# AGENTIC LOOP
# ═════════════════════════════════════════════════════════════════════════

def run_agent(directive: str, root: str = ".") -> None:
    os.chdir(root) if root != "." else None

    print(f"{BOLD}Agent: {directive}{RST}")

    # Check which provider to use
    provider_name, provider = _get_provider()
    if not provider:
        print(f"{DIM}No API key - using local LLM{RST}")
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

    # ── Agentic tool loop ─────────────────────────────────────────────────
    for round_num in range(1, MAX_TOOL_ROUNDS + 1):
        hdr(f"Round {round_num}/{MAX_TOOL_ROUNDS}", "Thinking…")

        try:
            response = _call_api(messages, use_tools=True)
        except RuntimeError as e:
            err(str(e))
            if "401" in str(e) or "authentication" in str(e).lower():
                print(f"\n  {YELL}Tip: make sure ANTHROPIC_API_KEY is set in your environment.{RST}")
                print(f"  {YELL}Or use local LLM mode by unsetting ANTHROPIC_API_KEY.{RST}")
            return

        content  = response.get("content", [])
        stop_reason = response.get("stop_reason", "")
        text_out = _extract_text(content)
        tool_uses = _extract_tool_uses(content)

        # Print any narrative text the model produced
        if text_out:
            print(f"\n{DIM}  Claude:{RST} {text_out[:600]}")

        # ── No more tool calls → extract patch proposal ────────────────
        if stop_reason == "end_turn" or not tool_uses:
            # Try to parse a JSON patch from the final text
            patch = _parse_patch(text_out or _extract_text(content))
            if patch:
                _present_and_apply_patch(patch, directive)
            else:
                warn("Agent finished without a parseable patch proposal.")
                print(f"\n  {DIM}Full response:{RST}\n  {text_out}")
            return

        # ── Execute tool calls ─────────────────────────────────────────
        # Append the assistant turn (with tool_use blocks)
        messages.append({"role": "assistant", "content": content})

        tool_results = []
        for tu in tool_uses:
            tool_name = tu.get("name", "")
            tool_args  = tu.get("input", {})
            tool_id    = tu.get("id", "")

            print(f"\n  {CYAN}→ {tool_name}{RST}({_fmt_args(tool_args)})")
            result = dispatch_tool(tool_name, tool_args)

            # Print a short preview of the result
            preview = result.replace("\n", " ")[:200]
            print(f"    {DIM}{preview}{RST}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    # ── Max rounds reached — ask for final answer without tools ────────
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
        patch = _parse_patch(text_out)
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
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
    # Find the outermost { ... }
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
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

    file_path = patch.get("file")
    find_str  = patch.get("find")
    replace_str = patch.get("replace")
    explanation = patch.get("explanation", "")

    hdr("Proposal", "Patch ready for review")
    print(f"  {BOLD}File:{RST}       {file_path}")
    print(f"  {BOLD}Why:{RST}        {explanation}\n")

    if not file_path or not find_str or not replace_str:
        warn("Patch is incomplete (missing file/find/replace).")
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
        # Suggest the closest lines
        lines = content.splitlines()
        first_token = find_str.split()[0] if find_str.split() else ""
        if first_token:
            hits = [f"  L{i+1}: {l}" for i, l in enumerate(lines)
                    if first_token in l][:5]
            if hits:
                print(f"\n  {DIM}Lines containing '{first_token}':{RST}")
                print("\n".join(hits))
        return

    modified = content.replace(find_str, replace_str, 1)

    # Show diff
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

    # One human gate
    confirm = input(
        f"\n{BOLD}  ⏸  Apply this patch? (yes/no): {RST}"
    ).strip().lower()

    if confirm not in ("yes", "y"):
        warn("Patch aborted by user — no files changed.")
        return

    write_ok = write_with_safety(file_path, modified)
    if write_ok:
        ok(f"Patch applied to {file_path}")
        # Git checkpoint with directive as commit message
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
    args = parser.parse_args()
    directive = " ".join(args.directive)
    run_agent(directive, root=args.root)


if __name__ == "__main__":
    main()
