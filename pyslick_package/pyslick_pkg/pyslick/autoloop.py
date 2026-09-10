#!/usr/bin/env python3
"""
autoloop.py — PySlick write-test-fix loop
==========================================

Closes the gap between "smoke.py reports a failure" and "someone has to
manually go fix it." This module wraps a single find/replace patch attempt
in a retry loop:

    patch -> run test command -> if it fails, ask Claude for a corrected
    find/replace using the failure output as context -> re-patch -> re-test

It never writes silently. Every attempt still goes through
patchit.write_with_safety (backup + syntax check + diff), and the loop
always stops to ask for confirmation before the FINAL attempt is kept,
even if tests pass. Nothing here auto-commits.

This is intentionally a separate module rather than a rewrite of recon.py
or patchit.py, so existing flows (`pyslick patchit`, `pyslick recon`) are
completely unaffected unless a caller opts in with --auto-fix.
"""

from __future__ import annotations

import os
import sys
import json
import time
import difflib
import subprocess
import urllib.request
from dataclasses import dataclass, field

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from patchit import (
    read_file,
    write_with_safety,
    reindent_to_match,
    backup_file,
)

BOLD = "\033[1m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELL = "\033[93m"
RED = "\033[91m"
DIM = "\033[2m"
RST = "\033[0m"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TEST_TIMEOUT = 120


@dataclass
class Attempt:
    number: int
    find_str: str
    replace_str: str
    test_passed: bool
    test_output: str = ""
    write_ok: bool = False


@dataclass
class LoopResult:
    success: bool
    attempts: list = field(default_factory=list)
    final_file_state: str | None = None
    aborted_reason: str | None = None


def _run_test_command(test_cmd: list[str], cwd: str, timeout: int) -> tuple[bool, str]:
    """Run the user-supplied test/lint command. Returns (passed, combined_output)."""
    if not test_cmd:
        return True, "(no test command configured — treated as pass)"
    try:
        result = subprocess.run(
            test_cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s running: {' '.join(test_cmd)}"
    except FileNotFoundError as e:
        return False, f"Could not run test command {test_cmd!r}: {e}"
    except Exception as e:
        return False, f"Error running test command: {e}"


def _ask_claude_for_fix(
    filepath: str,
    file_before: str,
    find_str: str,
    replace_str: str,
    test_output: str,
    attempt_number: int,
) -> tuple[str | None, str | None]:
    """Ask the Claude API for a corrected find/replace pair given a test
    failure. Returns (new_find_str, new_replace_str) or (None, None) if the
    API is unavailable or the response can't be parsed."""

    # Give the model a window of context around the failed find_str so it
    # can see real surrounding code, not just the two strings.
    idx = file_before.find(find_str)
    if idx != -1:
        start = max(0, idx - 400)
        end = min(len(file_before), idx + len(find_str) + 400)
        context = file_before[start:end]
    else:
        context = file_before[:1500]

    prompt = f"""You are fixing a failed code patch attempt #{attempt_number}.

File: {filepath}

Surrounding code context:
```
{context}
```

The previous patch attempt was:
FIND:
{find_str}

REPLACE (this is what got written):
{replace_str}

After applying this, the test/build command failed with:
{test_output[:2000]}

Give a corrected FIND and REPLACE pair that fixes the failure, using the
exact original code style. FIND must be an exact substring that exists in
the file above. Respond with ONLY valid JSON, no markdown fences, no
commentary:
{{"find": "...", "replace": "..."}}"""

    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = "".join(
                block["text"] for block in data.get("content", [])
                if block.get("type") == "text"
            ).strip()

        # Be tolerant of stray markdown fences even though we asked for none
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        parsed = json.loads(text)
        return parsed.get("find"), parsed.get("replace")
    except Exception as e:
        print(f"{YELL}  Auto-fixer unavailable: {e}{RST}")
        return None, None


def run_auto_fix_loop(
    filepath: str,
    find_str: str,
    replace_str: str,
    test_cmd: list[str] | None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    test_timeout: int = DEFAULT_TEST_TIMEOUT,
    require_final_confirm: bool = True,
) -> LoopResult:
    """
    Attempt a patch, run tests, and retry with Claude-suggested corrections
    on failure. Stops after max_attempts. Always shows a diff and requires
    confirmation before the final write is kept.
    """
    result = LoopResult(success=False)
    cwd = os.getcwd()

    if not os.path.isfile(filepath):
        result.aborted_reason = f"File not found: {filepath}"
        print(f"{RED}  ✖ {result.aborted_reason}{RST}")
        return result

    original_content = read_file(filepath)
    current_find, current_replace = find_str, replace_str

    for attempt_num in range(1, max_attempts + 1):
        print(f"\n{BOLD}{CYAN}━━ Attempt {attempt_num}/{max_attempts} ━━{RST}")

        content = read_file(filepath)
        if current_find not in content:
            print(f"{RED}  ✖ FIND string not present in file — cannot patch.{RST}")
            print(f"{DIM}  Looking for: {current_find[:120]}{RST}")
            result.aborted_reason = "find_str not found in file"
            break

        # Re-indent to match, same as manual patchit find/replace does
        matched_block = current_find
        reindented_replace = reindent_to_match(matched_block, current_replace)
        modified = content.replace(current_find, reindented_replace, 1)

        print(f"{DIM}  Writing candidate patch...{RST}")
        write_ok = write_with_safety(filepath, modified)

        if not write_ok:
            print(f"{RED}  ✖ Write blocked (likely syntax error) — stopping loop.{RST}")
            result.attempts.append(Attempt(attempt_num, current_find, current_replace,
                                            test_passed=False, write_ok=False))
            result.aborted_reason = "write blocked by safety check"
            break

        print(f"{DIM}  Running test command: {' '.join(test_cmd) if test_cmd else '(none configured)'}{RST}")
        passed, output = _run_test_command(test_cmd, cwd, test_timeout)

        attempt_record = Attempt(
            number=attempt_num,
            find_str=current_find,
            replace_str=current_replace,
            test_passed=passed,
            test_output=output,
            write_ok=True,
        )
        result.attempts.append(attempt_record)

        if passed:
            print(f"{GREEN}  ✔ Test command passed on attempt {attempt_num}.{RST}")
            result.success = True
            result.final_file_state = modified
            break

        print(f"{RED}  ✖ Test command failed on attempt {attempt_num}.{RST}")
        if output:
            preview = "\n".join(output.strip().splitlines()[-15:])
            print(f"{DIM}{preview}{RST}")

        if attempt_num == max_attempts:
            print(f"{YELL}  ⚠ Reached max attempts ({max_attempts}) without a passing test.{RST}")
            break

        print(f"{DIM}  Asking Claude for a corrected patch...{RST}")
        new_find, new_replace = _ask_claude_for_fix(
            filepath, content, current_find, current_replace, output, attempt_num
        )

        if not new_find or not new_replace:
            print(f"{YELL}  ⚠ No corrected patch available — restoring last-good state and stopping.{RST}")
            result.aborted_reason = "auto-fixer returned no correction"
            break

        # Restore the file to its pre-attempt state before trying the
        # next candidate, so each attempt patches from a clean baseline
        # rather than stacking edits on top of a failing one.
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        current_find, current_replace = new_find, new_replace

    # If the loop didn't succeed, make sure the file is back to its
    # original on-disk state rather than left mid-failed-attempt.
    if not result.success:
        current_on_disk = read_file(filepath) if os.path.isfile(filepath) else ""
        if current_on_disk != original_content:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(original_content)
            print(f"{DIM}  File restored to its original state (no passing patch found).{RST}")
        return result

    # Success path: show the final diff and require explicit confirmation
    # before leaving the change in place, even though tests passed.
    print(f"\n{BOLD}{CYAN}━━ Final diff (tests passing) ━━{RST}")
    diff = list(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        result.final_file_state.splitlines(keepends=True),
        fromfile=f"a/{filepath}", tofile=f"b/{filepath}",
    ))
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{GREEN}{line.rstrip()}{RST}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{RED}{line.rstrip()}{RST}")
        else:
            print(f"{DIM}{line.rstrip()}{RST}")

    if require_final_confirm:
        keep = input(f"\n{BOLD}  Keep this change? (yes/no): {RST}").strip().lower()
        if keep not in ("yes", "y"):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(original_content)
            print(f"{YELL}  Change discarded — file restored to original.{RST}")
            result.success = False
            result.aborted_reason = "user declined to keep passing change"
            return result

    print(f"{GREEN}  ✔ Change kept.{RST}")
    return result


def print_summary(result: LoopResult):
    print(f"\n{BOLD}{'─'*58}{RST}")
    if result.success:
        print(f"{GREEN}{BOLD}  Auto-fix loop succeeded{RST} "
              f"after {len(result.attempts)} attempt(s).")
    else:
        reason = result.aborted_reason or "unknown"
        print(f"{RED}{BOLD}  Auto-fix loop did not produce a passing patch.{RST} "
              f"({reason})")
    for a in result.attempts:
        mark = f"{GREEN}pass{RST}" if a.test_passed else f"{RED}fail{RST}"
        print(f"  attempt {a.number}: {mark}")
