#!/usr/bin/env python3
"""
patchit - Smart interactive file modifier and AI paste auto-patcher for PySlick CLI.

Modes:
  1. Smart AI Auto-Patch (-a / --auto / default when AI text pasted):
     Paste entire ChatGPT/Claude/Gemini responses containing:
       - Target file mentions (e.g. "Patch background.js", "pyslick patchit background.js -f")
       - Line number references (e.g. "Line 21 and line 34")
       - Multiple FIND & REPLACE blocks (e.g. "1. FIND: ... 2. REPLACE WITH: ...")
       - Function name anchors (e.g. "async function processPromptItem")
       - Diffs or conflict markers (<<<<<<< SEARCH ... ======= ... >>>>>>>)
     patchit automatically parses, matches, auto-reindents, validates syntax,
     runs project build/typecheck verifications, and applies all patches with one confirmation!

  2. -f / --find: Find & Replace block (auto re-indents for .py / .js / .ts, supports multi-block paste).
  3. -r / --regex: Search pattern and replace with template.
  4. -i / --insert: Insert snippet at specific line or after anchor string.
  5. -l / --lines: Show file with line numbers (read-only).
  6. --check / --verify: Run syntax validation and build/test checks against patchit_vocab dictionary.
  7. Default (Paste): Replace entire file or append if empty (with safety gate on drastic size drop).

Safety net (all automatic, no flags needed):
  - Every write makes a per-file timestamped backup in .pyslick_backups/
    before touching the real file — independent of git.
  - Multi-language syntax checks (Python AST, Node.js check, JSON parse, JSX tag checker) BEFORE writing.
    A write that would produce invalid syntax is refused by default.
  - Find & Replace auto-matches indentation of the block it's replacing.
  - After every successful write, a diff of what's now on disk is printed.
"""

import os
import sys
import re
import ast
import json
import shutil
import difflib
import subprocess
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

try:
    import libcst
    _HAS_LIBCST = True
except ImportError:
    _HAS_LIBCST = False

# ANSI Colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

BACKUP_DIR = os.path.join(".pyslick", "backups")
MAX_BACKUPS_PER_FILE = 5
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
VOCAB_PATH = Path(THIS_DIR) / "patchit_vocab.json"

LANGUAGE_NAMES = {
    "javascript", "typescript", "python", "powershell", "bash", "sh", "shell",
    "json", "html", "css", "jsx", "tsx", "js", "ts", "py", "text", "txt", "diff"
}


def _load_patchit_vocab() -> dict:
    """Load patchit_vocab.json for parser regexes, syntax rules, and test verification."""
    if VOCAB_PATH.exists():
        try:
            return json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_VOCAB = _load_patchit_vocab()


def print_banner():
    print(f"{CYAN}{BOLD}pyslick patchit{RESET} - Smart File Modifier & AI Auto-Patcher\n")


def confirm(prompt: str = "Apply these changes? [y/N]: ") -> bool:
    try:
        ans = input(f"{YELLOW}{prompt}{RESET}").strip().lower()
        return ans in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return False


def confirm_phrase(phrase: str, prompt: str) -> bool:
    """Require the user to type an exact word (not just y/N) to proceed."""
    try:
        ans = input(f"{YELLOW}{prompt}{RESET}").strip()
        return ans == phrase
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Safety & Syntax Helpers
# ─────────────────────────────────────────────────────────────────────────
def _prune_old_backups(safe_name: str, keep: int = MAX_BACKUPS_PER_FILE) -> None:
    """Keep only the newest `keep` backups for a given source file."""
    pattern_prefix = f"{safe_name}."
    if not os.path.exists(BACKUP_DIR):
        return
    existing = sorted(
        f for f in os.listdir(BACKUP_DIR)
        if f.startswith(pattern_prefix) and f.endswith(".bak")
    )
    excess = len(existing) - keep
    for old in existing[:max(0, excess)]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass


def backup_file(filepath: str) -> str | None:
    """Snapshot current on-disk contents of filepath before modifying."""
    if not os.path.exists(filepath):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = filepath.replace(os.sep, "__").replace("/", "__")
    backup_path = os.path.join(BACKUP_DIR, f"{safe_name}.{ts}.bak")
    shutil.copy2(filepath, backup_path)
    _prune_old_backups(safe_name)
    return backup_path


def validate_python_syntax(filepath: str, content: str) -> str | None:
    """Return an error string if content isn't valid Python, else None.

    Two independent parsers are consulted: stdlib `ast` (always available)
    and, if installed, `libcst` as a second, defense-in-depth pass — a
    different grammar implementation checking the same patched content.
    Framed honestly: in testing during this project (malformed syntax,
    mixed tabs/spaces, Python-2-style statements, walrus misuse, and newer
    grammar like match statements / PEP 695 generics), `libcst` never
    caught anything `ast` missed, and never rejected anything `ast`
    accepted. This tier is here in case some future patch hits an edge
    case where the two disagree, not because it's known to catch more —
    don't oversell it in docs or errors elsewhere in the codebase.
    """
    if not filepath.endswith(".py"):
        return None
    try:
        ast.parse(content, filename=filepath)
    except SyntaxError as e:
        return f"Python SyntaxError: {e.msg} (line {e.lineno}, col {e.offset})"

    if _HAS_LIBCST:
        try:
            libcst.parse_module(content)
        except Exception as e:
            return f"Python SyntaxError (libcst): {e}"

    return None


def validate_json_syntax(filepath: str, content: str) -> str | None:
    """Return an error string if content isn't valid JSON, else None."""
    if not filepath.endswith(".json"):
        return None
    try:
        json.loads(content)
        return None
    except json.JSONDecodeError as e:
        return f"JSON Error: {e.msg} (line {e.lineno}, col {e.colno})"


def validate_js_syntax(filepath: str, content: str) -> str | None:
    """Validate JavaScript / ESM syntax using Node.js --check if available, or bracket balancing."""
    ext = Path(filepath).suffix.lower()
    if ext not in (".js", ".mjs", ".cjs"):
        return None

    # Try node --check if node is installed
    try:
        with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_path = tf.name
        try:
            res = subprocess.run(["node", "--check", temp_path], capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                err_msg = (res.stderr or res.stdout).replace(temp_path, filepath).strip()
                lines = err_msg.splitlines()
                first_few = [l for l in lines if not l.startswith("Node.js v")][:4]
                return f"JavaScript SyntaxError:\n" + "\n".join(first_few)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # Basic bracket & backtick balance fallback
    open_braces = content.count("{") - content.count("}")
    open_parens = content.count("(") - content.count(")")
    open_brackets = content.count("[") - content.count("]")
    if open_braces != 0 or open_parens != 0 or open_brackets != 0:
        return f"Warning: Unbalanced brackets/braces ({open_braces} braces, {open_parens} parens, {open_brackets} brackets)"

    return None


def validate_jsx_syntax(filepath: str, content: str) -> str | None:
    """Validate JSX/TSX tags."""
    ext = Path(filepath).suffix.lower()
    if ext not in (".jsx", ".tsx"):
        return None
    try:
        from jsx_tag_checker import check_jsx_tags
        import io, contextlib
        with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, encoding="utf-8") as tf:
            tf.write(content)
            temp_path = tf.name
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                check_jsx_tags(temp_path)
            out = buf.getvalue().strip()
            if "unmatched" in out.lower() or "error" in out.lower():
                return f"JSX Tag Mismatch: {out.replace(temp_path, filepath)}"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception:
        pass
    return None


def validate_syntax(filepath: str, content: str) -> str | None:
    """Run all relevant syntax validators for filepath."""
    if filepath.endswith(".py"):
        return validate_python_syntax(filepath, content)
    if filepath.endswith(".json"):
        return validate_json_syntax(filepath, content)
    if filepath.endswith((".js", ".mjs", ".cjs")):
        return validate_js_syntax(filepath, content)
    if filepath.endswith((".jsx", ".tsx")):
        return validate_jsx_syntax(filepath, content)
    return None


def run_verification(filepath: str = "", custom_cmd: str | list[str] | None = None) -> tuple[bool, str]:
    """Run build / test verification commands from patchit_vocab or user flag."""
    cmd_to_run = None
    if isinstance(custom_cmd, str) and custom_cmd.strip():
        cmd_to_run = custom_cmd.split()
    elif isinstance(custom_cmd, list) and custom_cmd:
        cmd_to_run = custom_cmd

    if not cmd_to_run:
        # Detect from project files
        verif_cfg = _VOCAB.get("verification_commands", {})
        for _, rule in verif_cfg.items():
            detect_files = rule.get("detect_files", [])
            if any(os.path.exists(f) for f in detect_files):
                for candidate in rule.get("commands", []):
                    if shutil.which(candidate[0]):
                        cmd_to_run = candidate
                        break
            if cmd_to_run:
                break

    if not cmd_to_run:
        return True, "(no verification command detected)"

    print(f"\n{BOLD}{CYAN}--- Running Verification Check: {' '.join(cmd_to_run)} ---{RESET}")
    try:
        res = subprocess.run(cmd_to_run, capture_output=True, text=True, timeout=120)
        output = (res.stdout or "") + (res.stderr or "")
        passed = res.returncode == 0
        if passed:
            print(f"{GREEN}✔ Verification passed: {' '.join(cmd_to_run)}{RESET}")
        else:
            print(f"{RED}✖ Verification failed ({res.returncode}): {' '.join(cmd_to_run)}{RESET}")
            if output:
                preview = "\n".join(output.strip().splitlines()[-20:])
                print(f"{DIM}{preview}{RESET}")
        return passed, output
    except Exception as e:
        print(f"{YELLOW}Verification error: {e}{RESET}")
        return False, str(e)


def show_post_write_diff(filepath: str, backup_path: str | None):
    """Show what's actually on disk now after write."""
    in_git = False
    try:
        in_git = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True
        ).returncode == 0
    except Exception:
        pass

    print(f"\n{BOLD}--- Verifying what's on disk ---{RESET}")
    if in_git:
        result = subprocess.run(["git", "diff", "--", filepath], capture_output=True, text=True)
        out = result.stdout.strip()
        if out:
            for line in out.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    print(f"{GREEN}{line}{RESET}")
                elif line.startswith("-") and not line.startswith("---"):
                    print(f"{RED}{line}{RESET}")
                else:
                    print(f"{DIM}{line}{RESET}")
        else:
            print(f"{DIM}(git diff is empty — file matches last commit, or is untracked/new){RESET}")
    elif backup_path and os.path.exists(backup_path):
        with open(backup_path, "r", encoding="utf-8", errors="replace") as f:
            before = f.read()
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            after = f.read()
        diff = list(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile="backup", tofile="current"
        ))
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                print(f"{GREEN}{line.rstrip()}{RESET}")
            elif line.startswith("-") and not line.startswith("---"):
                print(f"{RED}{line.rstrip()}{RESET}")
            else:
                print(f"{DIM}{line.rstrip()}{RESET}")
    else:
        print(f"{DIM}(first write of this file){RESET}")


def reindent_to_match(target_block: str, replace_block: str) -> str:
    """Re-indent replace_block to match the base indentation of target_block.

    BUG FIX (Bug 2): Only reindents when the first non-empty line of replace_block
    has DIFFERENT leading whitespace than the first non-empty line of target_block.
    If they already match, the block is returned verbatim — this prevents the
    auto-reindent from silently shifting sibling blocks (catch/finally etc.) that
    were already correctly indented in the user's paste.
    """
    def _first_indent(text: str) -> str:
        for line in text.splitlines():
            if line.strip():
                return line[: len(line) - len(line.lstrip())]
        return ""

    target_indent = _first_indent(target_block)
    replace_indent = _first_indent(replace_block)

    # If indentation already matches, write verbatim — no silent drift.
    if target_indent == replace_indent:
        return replace_block

    # Indents differ — apply the existing reindent logic.
    dedented = textwrap.dedent(replace_block)
    out_lines = []
    for line in dedented.splitlines():
        out_lines.append(target_indent + line if line.strip() else line)
    result = "\n".join(out_lines)
    if replace_block.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def write_with_safety(filepath: str, modified: str, verify_cmd: str | list[str] | None = None) -> bool:
    """Single choke point for writes: validates syntax, backups, writes, verifies diff, and runs test check."""
    syntax_err = validate_syntax(filepath, modified)
    if syntax_err:
        print(f"\n{RED}{BOLD}[BLOCKED] This change produces a syntax error:{RESET}")
        print(f"{RED}  {syntax_err}{RESET}")
        print(f"{DIM}The file was NOT written.{RESET}")
        if not confirm(f"{RED}Write it anyway despite the syntax error? [y/N]: {RESET}"):
            print(f"{YELLOW}Aborted — no changes made.{RESET}")
            return False
        print(f"{YELLOW}Proceeding with write as requested.{RESET}")

    backup_path = backup_file(filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(modified)
    print(f"{GREEN}File updated successfully.{RESET}")
    if backup_path:
        print(f"{DIM}Backup saved: {backup_path}{RESET}")
    show_post_write_diff(filepath, backup_path)

    if verify_cmd:
        run_verification(filepath, verify_cmd)

    return True


def show_diff(original: str, modified: str, filepath: str) -> bool:
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            orig_lines, mod_lines, fromfile=f"a/{filepath}", tofile=f"b/{filepath}"
        )
    )

    if not diff:
        print(f"{DIM}No changes detected.{RESET}")
        return False

    print(f"\n{BOLD}--- Proposed Changes ({filepath}) ---{RESET}")
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line.rstrip()}{RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line.rstrip()}{RESET}")
        elif line.startswith("@"):
            print(f"{CYAN}{line.rstrip()}{RESET}")
        else:
            print(f"{DIM}{line.rstrip()}{RESET}")
    print()
    return True


def read_multiline_input(prompt_msg: str) -> str:
    print(
        f"{CYAN}{prompt_msg}{RESET} {DIM}(type or paste text, then enter 'END' on a new line to finish):{RESET}"
    )
    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        except EOFError:
            break
    return "\n".join(lines) + "\n" if lines else ""


def read_file(filepath: str) -> str:
    """Read a file's raw text content. Returns '' if it doesn't exist."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _normalize_whitespace(text: str) -> str:
    """Normalize non-breaking spaces, CRLF line endings, and trailing whitespace."""
    text = text.replace("\xa0", " ").replace("\u2007", " ").replace("\u202f", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def _locate_block(target_block: str, original: str, filepath: str | None = None,
                   line_hint: int | None = None) -> tuple[str, str] | None:
    """Progressively matches target_block inside original using
    exact -> stripped -> whitespace-normalized -> structural (ast-grep) ->
    line-bounded -> indentation-invariant.

    The structural tier sits between whitespace-normalized and the two
    manual heuristics because it's strictly more reliable than either:
    ast-grep compares real AST nodes, so it survives reformatting (extra
    spaces, different line breaks, reordered-but-equivalent whitespace)
    that would defeat exact/stripped matching, without the false-positive
    risk of indentation-invariant's loose line-by-line stripping.
    """
    if target_block in original:
        return target_block, "exact"

    stripped = target_block.strip()
    if stripped and stripped in original:
        return stripped, "stripped"

    norm_target = _normalize_whitespace(target_block).strip()
    if not norm_target:
        return None

    orig_lines = original.split("\n")
    norm_lines = [_normalize_whitespace(l) for l in orig_lines]
    target_lines = norm_target.split("\n")
    n = len(target_lines)

    # Search window ordering based on line hint
    search_indices = list(range(len(norm_lines) - n + 1))
    if line_hint is not None and 0 <= line_hint <= len(norm_lines):
        search_indices.sort(key=lambda idx: abs(idx - (line_hint - 1)))

    for i in search_indices:
        if norm_lines[i:i + n] == target_lines:
            real_span = "\n".join(orig_lines[i:i + n])
            return real_span, "whitespace-normalized"

    structural = _locate_block_structural(target_block, original, filepath)
    if structural is not None:
        return structural, "structural (ast-grep)"

    # Indentation-invariant matching
    dedent_target = [l.strip() for l in target_lines if l.strip()]
    if len(dedent_target) >= 2:
        for i in search_indices:
            slice_lines = [norm_lines[j].strip() for j in range(i, min(i + n + 4, len(norm_lines))) if norm_lines[j].strip()]
            if slice_lines[:len(dedent_target)] == dedent_target:
                real_span = "\n".join(orig_lines[i:i + n])
                return real_span, "indentation-invariant"

    return None


# ── ast-grep structural matching ────────────────────────────────────────
# Fills the gap patchit.py's own module docstring calls out: text/regex
# find-replace with "hope the indentation matches" re-indent guessing.
# ast-grep compares AST nodes instead of characters, so a find-block that's
# been reformatted (different spacing, wrapped differently) by whoever
# pasted it can still be located by real structural equivalence.

_AST_GREP_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin", ".kts": "kotlin",
    ".css": "css", ".scss": "css",
    ".html": "html", ".htm": "html",
    ".json": "json",
    ".lua": "lua",
    ".yaml": "yaml", ".yml": "yaml",
}

_AST_GREP_BIN = None  # cached: resolved binary path, or "" if unavailable


def _ast_grep_binary() -> str:
    global _AST_GREP_BIN
    if _AST_GREP_BIN is None:
        _AST_GREP_BIN = shutil.which("ast-grep") or shutil.which("sg") or ""
    return _AST_GREP_BIN


def _locate_block_structural(target_block: str, original: str, filepath: str | None) -> str | None:
    """Use ast-grep to find target_block's real on-disk span by AST
    equivalence rather than text equivalence. Returns the exact substring
    of `original` that matched, or None if ast-grep isn't installed, the
    file's language isn't supported, or nothing matches structurally."""
    if not filepath:
        return None
    binary = _ast_grep_binary()
    if not binary:
        return None

    lang = _AST_GREP_EXT_TO_LANG.get(Path(filepath).suffix.lower())
    if lang is None:
        return None

    pattern = target_block.strip()
    if not pattern:
        return None

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=Path(filepath).suffix, delete=False, encoding="utf-8"
        ) as tf:
            tf.write(original)
            temp_path = tf.name
        try:
            res = subprocess.run(
                [binary, "run", "--pattern", pattern, "--lang", lang,
                 "--json=compact", temp_path],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return None
            matches = json.loads(res.stdout)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    except Exception:
        return None

    if not matches:
        return None

    matched_text = matches[0].get("text")
    if matched_text and matched_text in original:
        return matched_text
    return None


# ─────────────────────────────────────────────────────────────────────────
# AI Paste & Multi-Block Parser
# ─────────────────────────────────────────────────────────────────────────

def clean_fenced_code(text: str) -> str:
    """Extract code from inside markdown fences (```lang ... ```) or strip language headers."""
    text = text.strip()
    if not text:
        return ""

    fence_match = re.search(r"```(?:[a-zA-Z0-9_\-]+)?\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1).rstrip("\r\n")

    lines = text.splitlines()
    while lines and (lines[0].strip().lower() in LANGUAGE_NAMES or not lines[0].strip()):
        lines = lines[1:]

    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    return "\n".join(lines).rstrip()


def extract_file_from_text(text: str) -> str | None:
    """Extract candidate target filename from pasted AI text."""
    m = re.search(r"pyslick\s+patchit\s+([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)", text, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r"(?:Step\s+\d+:\s*)?Patch\s+[`\"']?([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[`\"']?", text, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r"(?:File|Target|In)\s*:\s*[`\"']?([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)[`\"']?", text, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r"---\s+[ab]/([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)", text)
    if m:
        return m.group(1)

    first_lines = "\n".join(text.splitlines()[:15])
    matches = re.findall(r"[`\"']([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]{1,6})[`\"']", first_lines)
    for candidate in matches:
        if os.path.isfile(candidate) or Path(candidate).suffix in (".js", ".ts", ".tsx", ".jsx", ".py", ".json", ".html", ".css"):
            return candidate

    return None


def extract_find_replace_pairs(text: str) -> list[dict]:
    """
    Extract all (FIND, REPLACE) pairs from text.
    Handles:
      - 1. FIND:\n```js\n...\n```\n2. REPLACE WITH:\n```js\n...\n```
      - FIND:\n...\nREPLACE:\n...
      - <<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE
      - Line numbers and function anchor hints
    """
    lines = text.splitlines()
    pairs: list[dict] = []

    find_re = re.compile(
        r"^\s*(?:\d+[.:]\s*)?(?:FIND|SEARCH|OLD|BEFORE)(?:\s+WITH|\s+BLOCK|\s*\(.*?\))?:?\s*$",
        re.IGNORECASE
    )
    replace_re = re.compile(
        r"^\s*(?:\d+[.:]\s*)?(?:REPLACE(?: WITH)?|NEW|AFTER)(?:\s+WITH|\s+BLOCK|\s*\(.*?\))?:?\s*$",
        re.IGNORECASE
    )
    section_break_re = re.compile(
        r"^\s*(?:Step\s+\d+:|###|##|Verify\s+|Run\s+this|Once\s+applied|pyslick\s+)",
        re.IGNORECASE
    )
    conflict_start_re = re.compile(r"^\s*<<<<<<<\s*(?:SEARCH|ORIGINAL|HEAD)?\s*$", re.IGNORECASE)
    conflict_mid_re   = re.compile(r"^\s*=======\s*$", re.IGNORECASE)
    conflict_end_re   = re.compile(r"^\s*>>>>>>>\s*(?:REPLACE|PATCH|BRANCH)?\s*$", re.IGNORECASE)

    line_hints = [int(n) for n in re.findall(r"(?:line|lines|L)\s*(\d+)", text, re.IGNORECASE)]

    state = None
    cur_find_lines: list[str] = []
    cur_replace_lines: list[str] = []
    fence_count = 0

    for l in lines:
        if find_re.match(l) or conflict_start_re.match(l):
            if state == "replace" and cur_find_lines and cur_replace_lines:
                f_clean = clean_fenced_code("\n".join(cur_find_lines))
                r_clean = clean_fenced_code("\n".join(cur_replace_lines))
                if f_clean and r_clean is not None:
                    pairs.append({"find": f_clean, "replace": r_clean})
                cur_find_lines = []
                cur_replace_lines = []
            state = "find"
            cur_find_lines = []
            fence_count = 0
        elif replace_re.match(l) or conflict_mid_re.match(l):
            if state == "find":
                state = "replace"
                cur_replace_lines = []
                fence_count = 0
        elif conflict_end_re.match(l):
            if state == "replace" and cur_find_lines and cur_replace_lines:
                f_clean = clean_fenced_code("\n".join(cur_find_lines))
                r_clean = clean_fenced_code("\n".join(cur_replace_lines))
                if f_clean and r_clean is not None:
                    pairs.append({"find": f_clean, "replace": r_clean})
                cur_find_lines = []
                cur_replace_lines = []
            state = None
        elif state == "replace" and fence_count >= 2 and section_break_re.match(l):
            f_clean = clean_fenced_code("\n".join(cur_find_lines))
            r_clean = clean_fenced_code("\n".join(cur_replace_lines))
            if f_clean and r_clean is not None:
                pairs.append({"find": f_clean, "replace": r_clean})
            cur_find_lines = []
            cur_replace_lines = []
            state = None
        else:
            if l.strip().startswith("```"):
                fence_count += 1
            if state == "find":
                cur_find_lines.append(l)
            elif state == "replace":
                cur_replace_lines.append(l)

    if state == "replace" and cur_find_lines and cur_replace_lines:
        f_clean = clean_fenced_code("\n".join(cur_find_lines))
        r_clean = clean_fenced_code("\n".join(cur_replace_lines))
        if f_clean and r_clean is not None:
            pairs.append({"find": f_clean, "replace": r_clean})

    for i, p in enumerate(pairs):
        if i < len(line_hints):
            p["line_hint"] = line_hints[i]

    return pairs


def parse_smart_paste(raw_text: str, default_filepath: str | None = None) -> tuple[str | None, list[dict]]:
    """Parse pasted text to extract target filepath and list of patch dicts."""
    target_file = default_filepath or extract_file_from_text(raw_text)
    patches = extract_find_replace_pairs(raw_text)
    return target_file, patches


def is_ai_multi_patch(text: str) -> bool:
    """Determine if pasted text is an AI response with FIND/REPLACE blocks rather than raw file code."""
    has_find = bool(re.search(r"(?:^|\n)\s*(?:\d+[.:]\s*)?FIND(?:\s+WITH)?:?", text, re.IGNORECASE))
    has_replace = bool(re.search(r"(?:^|\n)\s*(?:\d+[.:]\s*)?REPLACE(?:\s+WITH)?:?", text, re.IGNORECASE))
    has_conflict = "<<<<<<<" in text and "=======" in text
    has_cmd = "pyslick patchit" in text or "Step 1: Patch" in text
    return (has_find and has_replace) or has_conflict or (has_cmd and has_find)


# ─────────────────────────────────────────────────────────────────────────
# Modes
# ─────────────────────────────────────────────────────────────────────────

def mode_show_lines(filepath: str, start: int | None = None, end: int | None = None):
    """Print the file with 1-indexed line numbers.

    Optional start/end limit the output:
        pyslick patchit file.tsx -l              # whole file
        pyslick patchit file.tsx -l 17           # only line 17
        pyslick patchit file.tsx -l 10-25        # lines 10 through 25
        pyslick patchit file.tsx -l 10:25        # same
        pyslick patchit file.tsx -l --head 20    # first 20 lines
        pyslick patchit file.tsx -l --tail 20    # last 20 lines
    """
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    lo = 1 if start is None else max(1, start)
    hi = total if end is None else min(total, end)

    if start is not None and end is None and start > 0:
        # single-line mode
        hi = min(total, start)

    for i in range(lo - 1, hi):
        print(f"{DIM}{i + 1:5d}{RESET}  {all_lines[i].rstrip()}")

    if lo > 1 or hi < total:
        print(f"{DIM}... ({total} lines total; showing {lo}-{hi}){RESET}")


def mode_smart_patch(filepath: str | None = None, initial_paste: str | None = None, verify_cmd: str | None = None):
    """Smart AI auto-patcher: parses multi-block FIND/REPLACE snippets, line numbers, and function anchors."""
    if initial_paste is None:
        print_banner()
        initial_paste = read_multiline_input("Paste AI Assistant Response / Patch Blocks")

    if not initial_paste.strip():
        print(f"{YELLOW}No input provided.{RESET}")
        return

    detected_file, patches = parse_smart_paste(initial_paste, filepath)
    target_file = filepath or detected_file

    if not target_file:
        target_file = input(f"{CYAN}Enter target file path:{RESET} ").strip()

    if not target_file:
        print(f"{RED}Error: No target file specified.{RESET}")
        return

    if not os.path.exists(target_file):
        print(f"{RED}Error: File '{target_file}' does not exist.{RESET}")
        return

    if not patches:
        print(f"{YELLOW}No FIND / REPLACE blocks detected in paste.{RESET}")
        print(f"{DIM}Falling back to single Find & Replace mode.{RESET}")
        mode_find(target_file)
        return

    print(f"\n{BOLD}{CYAN}━━ Smart Patch: {target_file} ({len(patches)} change block{'s' if len(patches) > 1 else ''}) ━━{RESET}")

    original = read_file(target_file)
    modified = original
    applied_count = 0

    for i, p in enumerate(patches, start=1):
        find_str = p["find"]
        replace_str = p["replace"]
        line_hint = p.get("line_hint")

        located = _locate_block(find_str, modified, filepath=target_file, line_hint=line_hint)
        if located is None:
            print(f"\n{RED}✖ Block {i} not found in {target_file}:{RESET}")
            print(f"  {DIM}{find_str[:160]}{RESET}")
            continue

        target_span, match_kind = located
        if match_kind != "exact":
            print(f"  {YELLOW}• Block {i}: matched via {match_kind} matching{RESET}")
        else:
            print(f"  {GREEN}• Block {i}: matched exactly{RESET}")

        reindented_replace = reindent_to_match(target_span, replace_str)
        modified = modified.replace(target_span, reindented_replace, 1)
        applied_count += 1

    if applied_count == 0:
        print(f"\n{RED}Error: None of the {len(patches)} patch blocks could be located in {target_file}.{RESET}")
        return

    changed = show_diff(original, modified, target_file)
    if not changed:
        print(f"{DIM}No changes to apply.{RESET}")
        return

    if confirm(f"Apply {applied_count}/{len(patches)} patch block(s) to {target_file}? [y/N]: "):
        write_with_safety(target_file, modified, verify_cmd=verify_cmd)


_FIND_SEPARATOR = re.compile(r'^\s*---+\s*$', re.MULTILINE)


def _split_find_replace_input(raw: str) -> list[tuple[str, str]]:
    """Parse one or more Find/Replace pairs from a single pasted block.

    Supported formats
    -----------------
    Single patch (original behaviour):
        <find block>
        ---            ← separator line (3+ dashes)
        <replace block>

    Multiple patches (new):
        <find 1>
        ---
        <replace 1>
        ===            ← patch boundary (3+ equals) separates pairs
        <find 2>
        ---
        <replace 2>

    If no separator is found the raw text is returned as the find block alone
    (caller will prompt for replace separately — legacy fallback).
    """
    PATCH_BOUNDARY = re.compile(r'^\s*={3,}\s*$', re.MULTILINE)
    pairs = []
    for chunk in PATCH_BOUNDARY.split(raw):
        parts = _FIND_SEPARATOR.split(chunk, maxsplit=1)
        if len(parts) == 2:
            find_part, replace_part = parts
            pairs.append((find_part.strip('\n'), replace_part.strip('\n')))
    return pairs


def mode_find(filepath: str):
    """Interactive Find & Replace supporting multiple patches in one session.

    NEW: Paste multiple Find/Replace pairs separated by '===' between pairs
    and '---' between the find and replace halves of each pair:

        <old code 1>
        ---
        <new code 1>
        ===
        <old code 2>
        ---
        <new code 2>

    All pairs are shown as a single combined diff before you confirm once.
    Falls back to the original single-prompt flow if no separator is found.
    Also detects full AI multi-patch text (existing behaviour).
    """
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return

    target_block = read_multiline_input(
        "Find (paste OLD block, or OLD---NEW pairs separated by ===, or full AI response)"
    )
    if not target_block.strip():
        print(f"{RED}Error: Search block cannot be empty.{RESET}")
        return

    if is_ai_multi_patch(target_block):
        print(f"\n{CYAN}⚡ Detected multi-block AI patch format in pasted text.{RESET}")
        mode_smart_patch(filepath=filepath, initial_paste=target_block)
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    # --- Multi-patch shorthand path ---
    pairs = _split_find_replace_input(target_block)
    if pairs:
        print(f"\n{CYAN}⚡ Detected {len(pairs)} Find/Replace pair(s) in pasted block.{RESET}")
        modified = original
        applied = 0
        for i, (find_raw, replace_raw) in enumerate(pairs, 1):
            located = _locate_block(find_raw, modified, filepath=filepath)
            if located is None:
                print(f"{RED}  Pair {i}: block not found — skipping.{RESET}")
                continue
            found_block, match_kind = located
            if match_kind != "exact":
                print(f"{YELLOW}  Pair {i}: matched via {match_kind}.{RESET}")
            else:
                print(f"{GREEN}  Pair {i}: matched exactly.{RESET}")
            fixed = reindent_to_match(found_block, replace_raw)
            if fixed != replace_raw:
                print(f"{DIM}  Pair {i}: [auto-reindent] adjusted indentation.{RESET}")
            modified = modified.replace(found_block, fixed, 1)
            applied += 1

        if applied == 0:
            print(f"{RED}Error: None of the {len(pairs)} blocks could be found in {filepath}.{RESET}")
            return

        changed = show_diff(original, modified, filepath)
        if changed and confirm(f"Apply {applied}/{len(pairs)} patch(es) to {filepath}? [y/N]: "):
            write_with_safety(filepath, modified)
        return

    # --- Legacy single-patch path (no separator detected) ---
    located = _locate_block(target_block, original, filepath=filepath)
    if located is None:
        print(f"{RED}Error: Specified block was not found in {filepath}.{RESET}")
        print(f"{DIM}Tried exact, stripped, and whitespace-normalized matching — none matched.{RESET}")
        return

    target_block, match_kind = located
    if match_kind != "exact":
        print(f"{YELLOW}[fuzzy-match] Found via {match_kind} matching.{RESET}")

    replace_block = read_multiline_input("Replace with (paste NEW block)")

    fixed = reindent_to_match(target_block, replace_block)
    if fixed != replace_block:
        print(f"{DIM}[auto-reindent] Matched replacement indentation to target block.{RESET}")
    replace_block = fixed

    modified = original.replace(target_block, replace_block, 1)

    changed = show_diff(original, modified, filepath)
    if changed and confirm():
        write_with_safety(filepath, modified)


# Alias used by recon.py
mode_find_replace = mode_find


def mode_regex(filepath: str):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    pattern = input(f"{CYAN}Enter Regex Pattern:{RESET} ").strip()
    if not pattern:
        print(f"{RED}Error: Pattern cannot be empty.{RESET}")
        return

    replacement = input(f"{CYAN}Enter Replacement Pattern:{RESET} ")

    try:
        modified = re.sub(pattern, replacement, original, flags=re.MULTILINE)
    except re.error as e:
        print(f"{RED}Regex Error: {e}{RESET}")
        return

    changed = show_diff(original, modified, filepath)
    if changed and confirm():
        write_with_safety(filepath, modified)


def mode_insert(filepath: str):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    lines = original.splitlines(keepends=True)

    print(f"{CYAN}Insertion Point:{RESET}")
    print(" 1. After Line Number")
    print(" 2. After Matching String/Anchor")
    choice = input("Choice [1/2]: ").strip()

    insert_idx = -1
    if choice == "1":
        line_num = int(input(f"Insert after line number (1-{len(lines)}): ").strip())
        insert_idx = max(0, min(line_num, len(lines)))
    elif choice == "2":
        anchor = input(f"Insert after line containing: ").strip()
        for i, l in enumerate(lines):
            if anchor in l:
                insert_idx = i + 1
                break
        if insert_idx == -1:
            print(f"{RED}Error: Anchor string not found.{RESET}")
            return

    new_block = read_multiline_input("Content to Insert")

    if insert_idx > 0:
        anchor_line = lines[insert_idx - 1]
        base_indent = anchor_line[: len(anchor_line) - len(anchor_line.lstrip())] if anchor_line.strip() else ""
        dedented = textwrap.dedent(new_block)
        new_block = "\n".join(
            (base_indent + l if l.strip() else l) for l in dedented.splitlines()
        )
        if not new_block.endswith("\n"):
            new_block += "\n"

    new_lines = new_block.splitlines(keepends=True)
    lines[insert_idx:insert_idx] = new_lines
    modified = "".join(lines)

    changed = show_diff(original, modified, filepath)
    if changed and confirm():
        write_with_safety(filepath, modified)


def mode_paste(filepath: str):
    print(f"Target: {BOLD}{filepath}{RESET}")
    original = read_file(filepath)

    raw_input_text = read_multiline_input(f"Paste contents for {filepath}")
    if is_ai_multi_patch(raw_input_text):
        print(f"\n{CYAN}⚡ Detected AI multi-patch response instead of raw file.{RESET}")
        if confirm("Switch to Smart Auto-Patch mode for these blocks? [y/N]: "):
            mode_smart_patch(filepath=filepath, initial_paste=raw_input_text)
            return

    lines = raw_input_text.splitlines()
    orig_line_cnt = len(original.splitlines())
    new_line_cnt = len(lines)

    shrank_a_lot = orig_line_cnt > 0 and new_line_cnt < max(1, orig_line_cnt * 0.5)
    near_empty = orig_line_cnt >= 3 and new_line_cnt <= 1

    if shrank_a_lot or near_empty:
        print(f"\n{YELLOW}[SAFETY WARNING] File has {orig_line_cnt} lines, "
              f"but the paste you gave has only {new_line_cnt} lines.{RESET}")
        print(f"{CYAN}Looks like a small edit. Trying Find & Replace first.{RESET}")
        find_str = raw_input_text.strip("\n")
        found = False
        matched_span = find_str
        if find_str and find_str in original:
            print(f"{GREEN}  Matched your paste exactly.{RESET}")
            found = True
        elif find_str:
            located = _locate_block(find_str, original, filepath=filepath)
            if located:
                matched_span, kind = located
                print(f"{YELLOW}  Matched via {kind}.{RESET}")
                found = True
        if found:
            replace_block = read_multiline_input("Replace with (paste NEW block)")
            fixed = reindent_to_match(matched_span, replace_block)
            modified = original.replace(matched_span, fixed, 1)
            changed = show_diff(original, modified, filepath)
            if changed and confirm():
                write_with_safety(filepath, modified)
            return
        print(f"{YELLOW}Could not locate your paste in the file.{RESET}")
        print(f"{DIM}Falling back to whole-file overwrite. This will replace all {orig_line_cnt} lines.{RESET}")
        if not confirm_phrase("OVERWRITE", "Type OVERWRITE (all caps) to replace the ENTIRE file anyway: "):
            print(f"{YELLOW}Aborted — nothing was written.{RESET}")
            return
    modified = "\n".join(lines)
    if not modified.endswith("\n") and lines:
        modified += "\n"
    changed = show_diff(original, modified, filepath)

    if changed and confirm():
        write_with_safety(filepath, modified)


def mode_dict_info():
    """Display information about patchit dictionary and registered verification commands."""
    print_banner()
    print(f"{BOLD}Patchit Dictionary ({VOCAB_PATH}):{RESET}\n")
    verif = _VOCAB.get("verification_commands", {})
    print(f"{CYAN}Registered Verification Commands:{RESET}")
    for name, cfg in verif.items():
        print(f"  • {BOLD}{name}{RESET}: triggers if {cfg.get('detect_files')}")
        for cmd in cfg.get("commands", []):
            print(f"      → {' '.join(cmd)}")
    print(f"\n{CYAN}Syntax Validators:{RESET}")
    for ext, cfg in _VOCAB.get("syntax_validators", {}).items():
        print(f"  • {BOLD}{ext}{RESET}: {cfg.get('description')}")


# ─────────────────────────────────────────────────────────────────────────
# Main CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────

def main():
    print_banner()
    if len(sys.argv) < 2:
        print("Usage: pyslick patchit <filepath> [-a|-f|-r|-i|-l|--check|--verify <cmd>]")
        print()
        print("Modes:")
        print("  -a, --auto   Smart AI Auto-Patch (paste full ChatGPT/Claude responses)")
        print("  -f, --find   Find & Replace — single or MULTIPLE patches in one shot")
        print("  -r, --regex  Regex Find & Replace")
        print("  -i, --insert Insert lines after line number or anchor string")
        print("  -l, --lines  Show file with line numbers (read-only)")
        print("  --check      Run syntax check and project build/typecheck verification")
        print("  --verify CMD Run custom verification command (e.g. --verify 'pnpm build')")
        print("  --dict       Show verification commands and syntax dictionary")
        print("  (none)       Full overwrite mode with safety gate on drastic size drops")
        print()
        print("-f / --find  multi-patch format (one confirm for all):")
        print("  Paste the entire block when prompted:")
        print()
        print("    <old block 1>")
        print("    ---")
        print("    <new block 1>")
        print("    ===")
        print("    <old block 2>")
        print("    ---")
        print("    <new block 2>")
        print()
        print("  Separators:  --- between find and replace halves")
        print("               === between patch pairs")
        print()
        print("Safety:")
        print("  • Every write is backed up to .pyslick_backups/ before touching the file.")
        print("  • Syntax is validated (Python AST / Node --check / JSX tag check) before write.")
        print("  • Auto-reindent only fires when old and new blocks have different leading")
        print("    whitespace — it will NOT silently shift sibling blocks (catch/finally etc.).")
        sys.exit(1)

    first_arg = sys.argv[1]
    if first_arg in ("--dict", "-d"):
        mode_dict_info()
        return

    if first_arg in ("-a", "--auto", "--smart"):
        mode_smart_patch()
        return

    filepath = first_arg
    flag = sys.argv[2] if len(sys.argv) > 2 else ""
    extra_arg = sys.argv[3] if len(sys.argv) > 3 else None

    if flag in ("-a", "--auto", "--smart"):
        mode_smart_patch(filepath=filepath)
    elif flag in ("-f", "--find"):
        mode_find(filepath)
    elif flag in ("-r", "--regex"):
        mode_regex(filepath)
    elif flag in ("-i", "--insert"):
        mode_insert(filepath)
    elif flag in ("-l", "--lines"):
        extra_args = sys.argv[3:]
        start = None
        end = None
        for a in extra_args:
            if a in ("--head",):
                continue
            if a in ("--tail",):
                continue
            m = re.match(r"^(\d+)(?:[-:](\d+))?$", a)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else (start if "-" not in a and ":" not in a else None)
                break
        # --head N / --tail N
        for i, a in enumerate(extra_args):
            if a == "--head" and i + 1 < len(extra_args) and extra_args[i+1].isdigit():
                start = 1
                end = int(extra_args[i+1])
            if a == "--tail" and i + 1 < len(extra_args) and extra_args[i+1].isdigit():
                with open(filepath, "r", encoding="utf-8", errors="replace") as _f:
                    _total = len(_f.readlines())
                n = int(extra_args[i+1])
                start = max(1, _total - n + 1)
                end = _total
        mode_show_lines(filepath, start=start, end=end)
    elif flag == "--check":
        if os.path.exists(filepath):
            syntax_err = validate_syntax(filepath, read_file(filepath))
            if syntax_err:
                print(f"{RED}✖ Syntax check failed:{RESET}\n  {syntax_err}")
            else:
                print(f"{GREEN}✔ Syntax check passed for {filepath}{RESET}")
            run_verification(filepath)
        else:
            print(f"{RED}File '{filepath}' not found.{RESET}")
    elif flag == "--verify":
        run_verification(filepath, custom_cmd=extra_arg)
    else:
        if os.path.exists(filepath):
            mode_paste(filepath)
        else:
            mode_smart_patch(filepath=filepath)


if __name__ == "__main__":
    main()
