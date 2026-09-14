#!/usr/bin/env python3
"""
debugger.py — PySlick Runtime Debugger & Traceback Analyzer
============================================================

Wraps any pyslick command, catches crashes, and gives you:
  - The exact traceback with file/line highlighted
  - Which pyslick module failed
  - A minimal repro snippet
  - A suggested fix (from Claude API if available, else heuristic)
  - The exact patchit command to apply the fix

Usage:
    python debugger.py query "make the mic button bigger"
    python debugger.py patchit src/app/page.tsx -l
    python debugger.py recon "flashcard spacing"
    python debugger.py --wrap-cmd "pnpm build"    # wrap any shell command

Also exposes a decorator for wrapping pyslick internals:

    from debugger import debug_wrap
    result = debug_wrap(graphify_query, file, question)
"""

import os
import sys
import ast
import re
import json
import time
import textwrap
import traceback
import subprocess
import importlib.util
from pathlib import Path
from datetime import datetime
from typing import Callable, Any, Optional

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

# ─────────────────────────────────────────────────────────────────────────
# Heuristic fix database — common pyslick crashes and their cures
# (tried FIRST so we don't need the API for well-known errors)
# ─────────────────────────────────────────────────────────────────────────
HEURISTICS = [
    {
        "pattern": r"ModuleNotFoundError.*rapidfuzz",
        "label": "rapidfuzz not installed",
        "fix": "pip install rapidfuzz>=3.0",
        "kind": "install",
    },
    {
        "pattern": r"ModuleNotFoundError.*llama_cpp",
        "label": "llama-cpp-python not installed",
        "fix": "pip install llama-cpp-python",
        "kind": "install",
    },
    {
        "pattern": r"ModuleNotFoundError.*tree_sitter",
        "label": "tree-sitter not installed",
        "fix": "pip install tree-sitter tree-sitter-typescript",
        "kind": "install",
    },
    {
        "pattern": r"ImportError.*cannot import name '(\w+)' from '(\w+)'",
        "label": "missing symbol from module",
        "fix": "The symbol {1} is missing from {2}.py — check REQUIRED_API in smoke.py",
        "kind": "code",
    },
    {
        "pattern": r"FileNotFoundError.*graph\.json",
        "label": "graphify graph not built",
        "fix": "Run: graphify extract . --code-only   (or just run pyslick query — it falls back to file scan)",
        "kind": "usage",
    },
    {
        "pattern": r"SyntaxError",
        "label": "Python SyntaxError in a pyslick file",
        "fix": "Run: python smoke.py --only static   to find which file is broken",
        "kind": "code",
    },
    {
        "pattern": r"UnicodeDecodeError",
        "label": "file encoding issue",
        "fix": "The file has non-UTF-8 bytes. Open with: errors='replace'",
        "kind": "code",
    },
    {
        "pattern": r"PermissionError",
        "label": "file permission denied",
        "fix": "Check file permissions: icacls <file>  (Windows) or ls -la <file>  (Unix)",
        "kind": "os",
    },
    {
        "pattern": r"RecursionError",
        "label": "recursion too deep (likely in graphify AST walk)",
        "fix": "Add a visited-set guard to the call-graph traversal. See graphify.py's query() depth limiter.",
        "kind": "code",
    },
    {
        "pattern": r"KeyboardInterrupt",
        "label": "interrupted by user",
        "fix": None,
        "kind": "user",
    },
    {
        "pattern": r"pnpm.*command not found|pnpm.*not recognized",
        "label": "pnpm not installed",
        "fix": "npm install -g pnpm   (then close/reopen your terminal)",
        "kind": "install",
    },
    {
        "pattern": r"Cannot find module|Module not found",
        "label": "missing JS/TS module (build error)",
        "fix": "Run: pnpm install   to restore node_modules",
        "kind": "install",
    },
    {
        "pattern": r"Type error|TypeScript error|TS\d{4}",
        "label": "TypeScript type error",
        "fix": "Run: tsc --noEmit   to see all type errors with file/line references",
        "kind": "code",
    },
    {
        "pattern": r"EADDRINUSE",
        "label": "port already in use (dev server)",
        "fix": "Kill the existing process: npx kill-port 3000   (or whatever port your dev server uses)",
        "kind": "os",
    },
]


def _match_heuristic(tb_text: str) -> Optional[dict]:
    for h in HEURISTICS:
        m = re.search(h["pattern"], tb_text, re.IGNORECASE)
        if m:
            fix = h["fix"]
            if fix and m.lastindex:
                try:
                    fix = fix.format(*m.groups())
                except Exception:
                    pass
            return {**h, "fix": fix, "match": m.group(0)}
    return None


# ─────────────────────────────────────────────────────────────────────────
# Traceback parser — extracts file, line, function from a Python traceback
# ─────────────────────────────────────────────────────────────────────────
def parse_traceback(tb_text: str) -> list[dict]:
    """Returns a list of frame dicts: {file, line, func, code_line}."""
    frames = []
    # matches: File "path/to/file.py", line 42, in function_name
    pattern = re.compile(
        r'File "([^"]+)", line (\d+), in (\S+)\s*\n\s*(.+)?'
    )
    for m in pattern.finditer(tb_text):
        frames.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "func": m.group(3),
            "code": (m.group(4) or "").strip(),
        })
    return frames


def _is_pyslick_frame(frame: dict) -> bool:
    return "pyslick" in frame["file"] or any(
        frame["file"].endswith(f"{m}.py")
        for m in ["patchit", "recon", "query", "graphify",
                  "toolbox", "find_nearest_nodes", "webdesign", "llm"]
    )


def _read_lines_around(filepath: str, line: int, context: int = 4) -> str:
    """Read `context` lines before and after `line` in the file."""
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        start = max(0, line - context - 1)
        end = min(len(lines), line + context)
        out = []
        for i, l in enumerate(lines[start:end], start + 1):
            marker = f"{RED}→{RST}" if i == line else " "
            out.append(f"  {marker} {DIM}{i:4d}{RST}  {l.rstrip()}")
        return "\n".join(out)
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────
# Report printer
# ─────────────────────────────────────────────────────────────────────────
def _print_debug_report(
    tb_text: str,
    frames: list[dict],
    heuristic: Optional[dict],
    cmd: list[str],
    elapsed: float,
):
    print(f"\n{RED}{BOLD}{'━'*60}")
    print("  PySlick Debugger — Crash Report")
    print(f"{'━'*60}{RST}")
    print(f"  {DIM}Command: {' '.join(cmd)}{RST}")
    print(f"  {DIM}Crashed after {elapsed:.2f}s{RST}\n")

    # ── error type ──
    last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "unknown error"
    print(f"{BOLD}Error:{RST}  {RED}{last_line}{RST}\n")

    # ── relevant frames ──
    pyslick_frames = [f for f in frames if _is_pyslick_frame(f)]
    print_frames = pyslick_frames or frames[-3:]

    if print_frames:
        print(f"{BOLD}Where it happened:{RST}")
        for frame in print_frames:
            rel = os.path.relpath(frame["file"]) if os.path.exists(frame["file"]) else frame["file"]
            print(f"  {CYAN}{rel}{RST}  line {YELL}{frame['line']}{RST}  in {BOLD}{frame['func']}{RST}")
            if frame["code"]:
                print(f"    {DIM}{frame['code']}{RST}")
            # show surrounding source
            ctx = _read_lines_around(frame["file"], frame["line"])
            if ctx:
                print(ctx)
        print()

    # ── heuristic fix ──
    if heuristic:
        kind_colour = {
            "install": GREEN, "code": YELL, "usage": CYAN, "os": YELL, "user": DIM
        }.get(heuristic["kind"], RST)
        print(f"{BOLD}Diagnosis:{RST}  {kind_colour}{heuristic['label']}{RST}")
        if heuristic["fix"]:
            print(f"{BOLD}Fix:{RST}")
            for line in heuristic["fix"].splitlines():
                print(f"  {GREEN}{line}{RST}")
        print()

    # ── patchit command (for code fixes) ──
    if heuristic and heuristic["kind"] == "code" and pyslick_frames:
        frame = pyslick_frames[-1]
        rel = os.path.relpath(frame["file"]) if os.path.exists(frame["file"]) else frame["file"]
        print(f"{BOLD}To inspect & fix:{RST}")
        print(f"  {CYAN}pyslick patchit {rel} -l{RST}   # see line numbers")
        print(f"  {CYAN}pyslick patchit {rel} -f{RST}   # find & replace the bad block")
        print()

    print(f"{DIM}Full traceback:{RST}")
    print(textwrap.indent(tb_text.strip(), "  "))
    print(f"\n{DIM}{'━'*60}{RST}")


# ─────────────────────────────────────────────────────────────────────────
# Claude API fix suggester
# ─────────────────────────────────────────────────────────────────────────
def _api_fix_suggest(tb_text: str, frames: list[dict]) -> Optional[str]:
    """Call Claude API to suggest a fix. Returns text or None."""
    # Build a context snippet from the failing frame's source
    context_snippets = []
    for frame in frames:
        if _is_pyslick_frame(frame) and os.path.exists(frame["file"]):
            ctx = _read_lines_around(frame["file"], frame["line"], context=10)
            if ctx:
                rel = os.path.relpath(frame["file"])
                context_snippets.append(f"=== {rel} (around line {frame['line']}) ===\n{ctx}")

    context = "\n\n".join(context_snippets[:2])

    prompt = f"""A pyslick CLI tool crashed with this traceback:

{tb_text.strip()}

Relevant source code:
{context}

Give a concise diagnosis and the minimal code fix. Format:
DIAGNOSIS: <one sentence>
FIX:
  File: <filename>
  Old: <old code>
  New: <new code>
COMMAND: pyslick patchit <file> -f"""

    try:
        import urllib.request
        payload = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return "".join(
                b["text"] for b in data.get("content", [])
                if b.get("type") == "text"
            )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Subprocess wrapper — runs pyslick as a subprocess and intercepts crashes
# ─────────────────────────────────────────────────────────────────────────
def wrap_subprocess(cmd: list[str], use_api: bool = False, cwd: str = None):
    """
    Run `cmd` as a subprocess. If it exits non-zero, parse the stderr for
    a traceback and print a debug report.
    """
    print(f"{DIM}Running: {' '.join(cmd)}{RST}")
    start = time.time()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd or os.getcwd()
        )
    except FileNotFoundError as e:
        print(f"{RED}Command not found: {e}{RST}")
        _print_debug_report(str(e), [], _match_heuristic(str(e)), cmd, 0.0)
        return

    elapsed = time.time() - start

    # If it succeeded, just show output and done
    if result.returncode == 0:
        print(result.stdout)
        if result.stderr:
            print(f"{DIM}{result.stderr}{RST}")
        print(f"\n{GREEN}✔ Exited 0 in {elapsed:.2f}s{RST}")
        return

    # It failed — analyse
    combined_err = result.stderr + result.stdout
    frames = parse_traceback(combined_err)
    heuristic = _match_heuristic(combined_err)

    # Print stdout/stderr first (the command's own output)
    if result.stdout.strip():
        print(result.stdout)

    _print_debug_report(combined_err, frames, heuristic, cmd, elapsed)

    # Try Claude API fix if requested and heuristic didn't resolve it
    if use_api and (not heuristic or heuristic["kind"] == "code"):
        print(f"\n{CYAN}Asking Claude API for a fix suggestion…{RST}")
        suggestion = _api_fix_suggest(combined_err, frames)
        if suggestion:
            print(f"\n{BOLD}Claude's suggestion:{RST}")
            print(textwrap.indent(suggestion, "  "))
        else:
            print(f"{DIM}(API unavailable — see heuristic fix above){RST}")


# ─────────────────────────────────────────────────────────────────────────
# Python function wrapper (for use as a decorator or direct call)
# ─────────────────────────────────────────────────────────────────────────
def debug_wrap(fn: Callable, *args, use_api: bool = False, **kwargs) -> Any:
    """
    Call fn(*args, **kwargs). On any exception, print a debug report and
    re-raise. Useful for wrapping graphify_query, patchit modes, etc.

    Example:
        from debugger import debug_wrap
        results = debug_wrap(graphify_query, "src/app.py", "mic button size")
    """
    start = time.time()
    try:
        return fn(*args, **kwargs)
    except Exception:
        elapsed = time.time() - start
        tb_text = traceback.format_exc()
        frames = parse_traceback(tb_text)
        heuristic = _match_heuristic(tb_text)
        cmd_label = [f"{fn.__module__}.{fn.__name__}"] + [repr(a) for a in args]
        _print_debug_report(tb_text, frames, heuristic, cmd_label, elapsed)
        if use_api and (not heuristic or heuristic["kind"] == "code"):
            suggestion = _api_fix_suggest(tb_text, frames)
            if suggestion:
                print(f"\n{BOLD}Claude's suggestion:{RST}")
                print(textwrap.indent(suggestion, "  "))
        raise


# ─────────────────────────────────────────────────────────────────────────
# Build / terminal log scanner
# ─────────────────────────────────────────────────────────────────────────
def scan_build_output(output: str, source: str = "build") -> list[dict]:
    """
    Parse raw pnpm/next build output for errors and return structured issues.
    Each issue: {severity, file, line, col, message, raw}

    Covers:
      - Next.js   ./src/app/page.tsx:42:10  Type error: ...
      - ESLint    /path/to/file.ts  10:3  error  ...
      - Vite      [vite:esbuild] ...
      - TypeScript  error TS2345: ...
    """
    issues = []

    # Next.js / tsc style:  path/to/file.ts(42,10): error TS2345: ...
    for m in re.finditer(
        r"([^\s:]+\.[tj]sx?)\((\d+),(\d+)\):\s*(error|warning)\s+(TS\d+):\s*(.+)",
        output
    ):
        issues.append({
            "severity": m.group(4),
            "file": m.group(1), "line": int(m.group(2)), "col": int(m.group(3)),
            "code": m.group(5), "message": m.group(6).strip(),
            "raw": m.group(0),
        })

    # Next.js page format:  ./src/app/page.tsx:42:10  ...
    for m in re.finditer(
        r"\./([^\s]+\.[tj]sx?):(\d+):(\d+)\s+(.+)",
        output
    ):
        msg = m.group(4).strip()
        sev = "error" if re.search(r"\berror\b", msg, re.I) else "warning"
        issues.append({
            "severity": sev, "file": m.group(1),
            "line": int(m.group(2)), "col": int(m.group(3)),
            "code": None, "message": msg, "raw": m.group(0),
        })

    # ESLint:  /abs/path/file.ts  10:3  error  no-unused-vars  ...
    for m in re.finditer(
        r"(/[^\s]+\.[tj]sx?)\s+(\d+):(\d+)\s+(error|warning)\s+(.+)",
        output
    ):
        issues.append({
            "severity": m.group(4), "file": m.group(1),
            "line": int(m.group(2)), "col": int(m.group(3)),
            "code": None, "message": m.group(5).strip(), "raw": m.group(0),
        })

    return issues


def print_build_issues(issues: list[dict], project_root: str = "."):
    if not issues:
        print(f"{GREEN}✔ No build issues detected.{RST}")
        return

    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    print(f"\n{BOLD}Build Issues:{RST}  "
          f"{RED}{len(errors)} errors{RST}  {YELL}{len(warnings)} warnings{RST}\n")

    for issue in issues[:20]:  # cap output
        colour = RED if issue["severity"] == "error" else YELL
        code_tag = f"  {DIM}[{issue['code']}]{RST}" if issue.get("code") else ""
        rel = issue["file"]
        if os.path.isabs(rel):
            rel = os.path.relpath(rel, project_root)
        print(f"  {colour}{issue['severity'].upper()}{RST}{code_tag}  "
              f"{CYAN}{rel}{RST}:{YELL}{issue['line']}{RST}:{issue['col']}")
        print(f"    {issue['message']}")
        # show source context
        full_path = issue["file"] if os.path.isabs(issue["file"]) else os.path.join(project_root, rel)
        ctx = _read_lines_around(full_path, issue["line"], context=2)
        if ctx:
            print(ctx)
        print()

    if len(issues) > 20:
        print(f"{DIM}  … and {len(issues) - 20} more issues (run pnpm build for full output){RST}")

    # patchit commands for the first 3 errors
    if errors:
        print(f"\n{BOLD}Quick-fix commands (inspect the erroring files):{RST}")
        seen = set()
        for issue in errors[:3]:
            rel = issue["file"]
            if os.path.isabs(rel):
                rel = os.path.relpath(rel, project_root)
            if rel not in seen:
                seen.add(rel)
                print(f"  {CYAN}pyslick patchit {rel} -l{RST}")


# ─────────────────────────────────────────────────────────────────────────
# Entry — wraps pyslick commands or arbitrary shell commands
# ─────────────────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(
        prog="python debugger.py",
        description="PySlick runtime debugger — wraps commands and analyses crashes"
    )
    parser.add_argument("--wrap-cmd", metavar="CMD",
                        help="Wrap an arbitrary shell command (e.g. 'pnpm build')")
    parser.add_argument("--fix", action="store_true",
                        help="Ask Claude API for a fix on crash (requires API access)")
    parser.add_argument("--scan-log", metavar="FILE",
                        help="Scan a build log file for errors (no command run)")
    parser.add_argument("--root", default=".",
                        help="Project root for relative path display")
    parser.add_argument("pyslick_args", nargs="*",
                        help="pyslick subcommand and args, e.g.: query 'make mic bigger'")
    args = parser.parse_args()

    if args.scan_log:
        # Scan a saved build log file
        with open(args.scan_log, "r", encoding="utf-8", errors="replace") as f:
            log = f.read()
        issues = scan_build_output(log)
        print_build_issues(issues, project_root=args.root)
        return

    if args.wrap_cmd:
        # Arbitrary shell command
        import shlex
        cmd = shlex.split(args.wrap_cmd)
        # Run it and capture output for build issue scanning too
        start = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=args.root)
        elapsed = time.time() - start
        combined = result.stdout + result.stderr
        print(combined)
        if result.returncode != 0:
            issues = scan_build_output(combined)
            if issues:
                print_build_issues(issues, project_root=args.root)
            else:
                frames = parse_traceback(combined)
                heuristic = _match_heuristic(combined)
                _print_debug_report(combined, frames, heuristic, cmd, elapsed)
                if args.fix:
                    suggestion = _api_fix_suggest(combined, frames)
                    if suggestion:
                        print(f"\n{BOLD}Claude's suggestion:{RST}")
                        print(textwrap.indent(suggestion, "  "))
        return

    if args.pyslick_args:
        # Wrap a pyslick subcommand
        cmd = [sys.executable, "-m", "pyslick"] + args.pyslick_args
        wrap_subprocess(cmd, use_api=args.fix, cwd=args.root)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
