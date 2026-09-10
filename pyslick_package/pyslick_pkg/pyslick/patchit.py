#!/usr/bin/env python3
"""
patchit - Smart interactive file modifier for PySlick CLI.
Modes:
  1. Default (Paste): Replace entire file or append if empty.
  2. -f / --find: Find & Replace block.
  3. -r / --regex: Search pattern and replace with template.
  4. -i / --insert: Insert snippet at specific line or after anchor string.
  5. -l / --lines: Show file with line numbers (read-only).

Safety net (all automatic, no flags needed):
  - Every write makes a per-file timestamped backup in .pyslick_backups/
    before touching the real file — independent of git.
  - Any write to a .py file is syntax-checked (ast.parse) BEFORE writing.
    A write that would produce invalid Python is refused by default.
  - Find & Replace auto-matches the indentation of the block it's
    replacing, instead of using whatever indentation you happened to
    paste with.
  - A drastic size drop (paste mode) requires typing OVERWRITE, not
    just y/N — a stray blank line + "END" can no longer nuke a file.
  - After every successful write, a diff of what's now on disk is
    printed (git diff if in a repo, otherwise a diff against the
    backup) so you can see exactly what landed.
"""

import os
import sys
import re
import ast
import shutil
import difflib
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

# ANSI Colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

BACKUP_DIR = ".pyslick_backups"


def print_banner():
    print(f"{CYAN}{BOLD}pyslick patchit{RESET} - File Modifier\n")


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
# Safety helpers
# ─────────────────────────────────────────────────────────────────────────
def backup_file(filepath: str) -> str | None:
    """Snapshot the CURRENT on-disk contents of filepath before we touch it.
    Cheap (single file, not the whole project) and independent of git."""
    if not os.path.exists(filepath):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = filepath.replace(os.sep, "__").replace("/", "__")
    backup_path = os.path.join(BACKUP_DIR, f"{safe_name}.{ts}.bak")
    shutil.copy2(filepath, backup_path)
    return backup_path


def validate_python_syntax(filepath: str, content: str) -> str | None:
    """Return an error string if `content` isn't valid Python, else None.
    Only checked for .py files — other languages are skipped."""
    if not filepath.endswith(".py"):
        return None
    try:
        ast.parse(content, filename=filepath)
        return None
    except SyntaxError as e:
        return f"{e.msg} (line {e.lineno}, col {e.offset})"


def show_post_write_diff(filepath: str, backup_path: str | None):
    """After a write, show what's actually on disk now — prefer `git diff`
    when in a repo (most trustworthy), fall back to a diff against the
    backup we just took."""
    in_git = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True
    ).returncode == 0

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
        print(f"{DIM}(not a git repo and no prior backup to diff against — first write of this file){RESET}")


def reindent_to_match(target_block: str, replace_block: str) -> str:
    """Re-indent `replace_block` to match the base indentation of
    `target_block`, preserving relative indentation within replace_block.
    This is what stops Find&Replace from wrecking Python indentation."""
    # base indent = leading whitespace of the first non-blank line of target
    base_indent = ""
    for line in target_block.splitlines():
        if line.strip():
            base_indent = line[: len(line) - len(line.lstrip())]
            break

    # dedent the replacement to remove whatever indentation the user
    # happened to paste it with, then reapply the target's base indent
    dedented = textwrap.dedent(replace_block)
    out_lines = []
    for line in dedented.splitlines():
        out_lines.append(base_indent + line if line.strip() else line)
    result = "\n".join(out_lines)
    if replace_block.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def write_with_safety(filepath: str, modified: str) -> bool:
    """Single choke point for every write in this module: validates Python
    syntax, backs up the current file, writes, then shows a post-write diff.
    Returns True if the write happened."""
    syntax_err = validate_python_syntax(filepath, modified)
    if syntax_err:
        print(f"\n{RED}{BOLD}[BLOCKED] This change would break Python syntax:{RESET}")
        print(f"{RED}  {syntax_err}{RESET}")
        print(f"{DIM}The file was NOT written.{RESET}")
        if not confirm(f"{RED}Write it anyway despite the syntax error? [y/N]: {RESET}"):
            print(f"{YELLOW}Aborted — no changes made.{RESET}")
            return False
        print(f"{YELLOW}Proceeding with a known-broken write, as requested.{RESET}")

    backup_path = backup_file(filepath)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(modified)
    print(f"{GREEN}File updated successfully.{RESET}")
    if backup_path:
        print(f"{DIM}Backup saved: {backup_path}{RESET}")
    show_post_write_diff(filepath, backup_path)
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


def mode_show_lines(filepath: str):
    """Print the file with 1-indexed line numbers (used by recon's inspect phase)."""
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            print(f"{DIM}{i:5d}{RESET}  {line.rstrip()}")


def mode_paste(filepath: str):
    print(f"Target: {BOLD}{filepath}{RESET}")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
    else:
        original = ""

    print(
        f"{CYAN}Paste NEW file contents below{RESET} {DIM}(end with 'END' on a new line):{RESET}"
    )
    lines = []
    while True:
        try:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
        except EOFError:
            break  # was `pass` — that spun forever once stdin closed

    orig_line_cnt = len(original.splitlines())
    new_line_cnt = len(lines)

    # Always compare sizes, regardless of file size — the old ">15 lines"
    # gate is exactly what let a small/truncated paste through unchecked.
    shrank_a_lot = orig_line_cnt > 0 and new_line_cnt < max(1, orig_line_cnt * 0.5)
    near_empty = orig_line_cnt >= 3 and new_line_cnt <= 1

    if shrank_a_lot or near_empty:
        print(f"\n{YELLOW}[SAFETY WARNING] File has {orig_line_cnt} lines, "
              f"but the paste you gave has only {new_line_cnt} lines.{RESET}")
        print(f"{DIM}If you hit a stray blank line before typing END, this is why.{RESET}")
        print(f"{DIM}To edit specific lines instead, use: pyslick patchit <file> -f (or -r / -i){RESET}")
        if not confirm_phrase("OVERWRITE", "Type OVERWRITE (all caps) to replace the ENTIRE file anyway: "):
            print(f"{YELLOW}Aborted — nothing was written.{RESET}")
            return

    modified = "\n".join(lines)
    if not modified.endswith("\n"):
        modified += "\n"
    changed = show_diff(original, modified, filepath)

    if changed and confirm():
        write_with_safety(filepath, modified)


def mode_find(filepath: str):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RESET}")
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        original = f.read()

    target_block = read_multiline_input("Find (paste the OLD block)")
    if not target_block.strip():
        print(f"{RED}Error: Search block cannot be empty.{RESET}")
        return

    if target_block not in original:
        clean_target = target_block.strip()
        if clean_target in original:
            target_block = clean_target
        else:
            print(f"{RED}Error: Specified block was not found in {filepath}.{RESET}")
            return

    replace_block = read_multiline_input("Replace with (paste NEW block)")

    # Auto-match indentation for Python files so pasted replacements
    # don't wreck the file's indentation.
    if filepath.endswith(".py"):
        fixed = reindent_to_match(target_block, replace_block)
        if fixed != replace_block:
            print(f"{DIM}[auto-reindent] Matched replacement indentation to the block it's replacing.{RESET}")
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

    # Match indentation of the line right before the insertion point for .py files
    if filepath.endswith(".py") and insert_idx > 0:
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


def main():
    print_banner()
    if len(sys.argv) < 2:
        print("Usage: pyslick patchit <filepath> [-f|-r|-i|-l]")
        print("Modes:")
        print("  (none)      Paste mode (full overwrite, requires typing OVERWRITE on big changes)")
        print("  -f, --find  Find & Replace exact block (auto re-indents for .py)")
        print("  -r, --regex Regex Find & Replace")
        print("  -i, --insert Insert lines after line/anchor (auto re-indents for .py)")
        print("  -l, --lines Show file with line numbers (read-only)")
        sys.exit(1)

    filepath = sys.argv[1]
    flag = sys.argv[2] if len(sys.argv) > 2 else ""

    if flag in ("-f", "--find"):
        mode_find(filepath)
    elif flag in ("-r", "--regex"):
        mode_regex(filepath)
    elif flag in ("-i", "--insert"):
        mode_insert(filepath)
    elif flag in ("-l", "--lines"):
        mode_show_lines(filepath)
    else:
        mode_paste(filepath)


if __name__ == "__main__":
    main()
