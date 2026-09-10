#!/usr/bin/env python3
"""
clipboard.py — PySlick output capture + clipboard copy
========================================================

Wraps any pyslick command so that its terminal output is:
  1. Printed normally (you see it as usual)
  2. Saved to a session buffer
  3. Copyable to the clipboard via Ctrl+C *after* the command finishes,
     without interrupting the run

Windows (PowerShell):   uses Set-Clipboard (built-in, no install needed)
macOS:                  uses pbcopy
Linux:                  tries xclip, then xsel, then pyperclip

HOW IT WORKS
─────────────
main() in __init__.py wraps itself with capture_and_offer():

    from .clipboard import capture_and_offer
    with capture_and_offer():
        <run the command normally>

Inside the context manager, sys.stdout is replaced with a Tee that writes
to both the real terminal and an in-memory StringIO. When the command
finishes (or errors), the buffer is saved to a temp file and the footer
is printed:

    ────────────────────────────────────────────
    ✔ Output saved  |  Press Ctrl+C to copy to clipboard
    ────────────────────────────────────────────

If the user presses Ctrl+C (KeyboardInterrupt) inside the 2-second wait
window, the buffer goes straight to the clipboard. If they don't press
anything, pyslick exits normally — no interruption.

The last session output is also written to ~/.pyslick/last_output.txt
so the user can always run:

    pyslick copy          ← re-copies last session output
    pyslick copy --show   ← also prints it

STRIP ANSI
─────────────
ANSI colour codes are stripped before copying so the pasted text is
clean plain text for your LLM chat box.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
import tempfile
import subprocess
from contextlib import contextmanager
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────
PYSLICK_STATE_DIR = Path.home() / ".pyslick"
LAST_OUTPUT_FILE  = PYSLICK_STATE_DIR / "last_output.txt"

# ── ANSI stripper ─────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\033\[[0-9;]*[mABCDEFGHJKSTfnsu]")

def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ── Tee: write to real stdout AND a buffer ────────────────────────────────
class _Tee(io.TextIOBase):
    """Proxy that forwards every write() to both `real` and `buf`."""

    def __init__(self, real: io.TextIOWrapper, buf: io.StringIO):
        self._real = real
        self._buf  = buf

    def write(self, s: str) -> int:
        self._real.write(s)
        self._real.flush()
        self._buf.write(s)
        return len(s)

    def flush(self):
        self._real.flush()

    @property
    def encoding(self):
        return getattr(self._real, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._real, "errors", "replace")

    def fileno(self):          # needed by some subprocesses
        return self._real.fileno()

    def isatty(self):
        return self._real.isatty()


# ── clipboard backends ────────────────────────────────────────────────────

def _copy_windows(text: str) -> bool:
    """PowerShell Set-Clipboard — always available on Win 10+."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Set-Clipboard -Value @'\n{text}\n'@"],
            capture_output=True, timeout=8,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _copy_macos(text: str) -> bool:
    try:
        proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"),
                               capture_output=True, timeout=8)
        return proc.returncode == 0
    except Exception:
        return False


def _copy_linux(text: str) -> bool:
    encoded = text.encode("utf-8")
    for cmd in (
        ["xclip",  "-selection", "clipboard"],
        ["xsel",   "--clipboard", "--input"],
        ["wl-copy"],                           # Wayland
    ):
        try:
            proc = subprocess.run(cmd, input=encoded,
                                  capture_output=True, timeout=8)
            if proc.returncode == 0:
                return True
        except FileNotFoundError:
            continue
        except Exception:
            continue
    # Last resort: pyperclip
    try:
        import pyperclip                       # type: ignore
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def copy_to_clipboard(text: str) -> bool:
    """Copy `text` to the system clipboard. Returns True on success."""
    clean = strip_ansi(text)
    if sys.platform == "win32":
        return _copy_windows(clean)
    elif sys.platform == "darwin":
        return _copy_macos(clean)
    else:
        return _copy_linux(clean)


# ── persist last output ───────────────────────────────────────────────────

def save_last_output(text: str) -> None:
    try:
        PYSLICK_STATE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_OUTPUT_FILE.write_text(strip_ansi(text), encoding="utf-8")
    except Exception:
        pass


def load_last_output() -> str | None:
    try:
        if LAST_OUTPUT_FILE.exists():
            return LAST_OUTPUT_FILE.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


# ── colours (minimal — this module is imported before colour setup) ────────
_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_CYAN  = "\033[96m"
_YELL  = "\033[93m"
_DIM   = "\033[2m"
_RST   = "\033[0m"


def _print_footer(buf_len: int) -> None:
    """Print the copy-offer footer after a command finishes."""
    lines = f"{buf_len:,} chars"
    bar   = "─" * 52
    print(f"\n{_DIM}{bar}{_RST}")
    print(
        f"  {_GREEN}✔{_RST} Output captured  {_DIM}({lines}){_RST}"
        f"  │  {_BOLD}Press Ctrl+C to copy to clipboard{_RST}"
    )
    print(
        f"  {_DIM}Or run:{_RST}  {_CYAN}pyslick copy{_RST}"
        f"  {_DIM}to copy the last session any time{_RST}"
    )
    print(f"{_DIM}{bar}{_RST}")


def _wait_for_ctrl_c(timeout: float = 2.5) -> bool:
    """
    Block for `timeout` seconds waiting for Ctrl+C.
    Returns True if the user pressed Ctrl+C, False if time elapsed.
    We do this by sleeping in tiny increments inside a try/except so
    KeyboardInterrupt is caught cleanly.
    """
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.05)
        return False
    except KeyboardInterrupt:
        return True


# ── main context manager ──────────────────────────────────────────────────

@contextmanager
def capture_and_offer(skip_commands: set[str] | None = None):
    """
    Context manager. Wraps a block, tees stdout into a buffer, then
    offers clipboard copy via Ctrl+C when the block finishes.

    Usage:
        with capture_and_offer():
            run_the_command()
    """
    buf      = io.StringIO()
    real_out = sys.stdout
    tee      = _Tee(real_out, buf)

    sys.stdout = tee  # type: ignore[assignment]
    try:
        yield
    except SystemExit as exc:
        # Command called sys.exit() — restore stdout before we continue
        sys.stdout = real_out
        captured = buf.getvalue()
        if captured.strip():
            save_last_output(captured)
            _print_footer(len(captured))
            if _wait_for_ctrl_c():
                _do_copy(captured, real_out)
        raise  # re-raise so the exit code propagates normally
    except Exception:
        sys.stdout = real_out
        raise
    else:
        sys.stdout = real_out
        captured = buf.getvalue()
        if captured.strip():
            save_last_output(captured)
            _print_footer(len(captured))
            if _wait_for_ctrl_c():
                _do_copy(captured, real_out)


def _do_copy(text: str, real_out) -> None:
    ok = copy_to_clipboard(text)
    if ok:
        real_out.write(f"\n  {_GREEN}✔ Copied to clipboard!{_RST}  "
                       f"{_DIM}Paste into your LLM chat.{_RST}\n")
    else:
        real_out.write(f"\n  {_YELL}⚠ Clipboard copy failed.{_RST}  "
                       f"Output is in: {LAST_OUTPUT_FILE}\n")
    real_out.flush()


# ── pyslick copy command ──────────────────────────────────────────────────

def cmd_copy(show: bool = False) -> None:
    """
    `pyslick copy` — re-copy the last session output to clipboard.
    `pyslick copy --show` — also print the saved text.
    """
    text = load_last_output()
    if not text:
        print(f"{_YELL}  No previous output found in {LAST_OUTPUT_FILE}{_RST}")
        print(f"  Run any pyslick command first.")
        return

    if show:
        print(text)

    ok = copy_to_clipboard(text)
    if ok:
        lines = len(text.splitlines())
        print(f"{_GREEN}  ✔ Copied {len(text):,} chars ({lines} lines) to clipboard.{_RST}")
        print(f"  {_DIM}Paste into your LLM chat box.{_RST}")
    else:
        print(f"{_YELL}  ⚠ Could not copy automatically.{_RST}")
        print(f"  {_DIM}File is at: {LAST_OUTPUT_FILE}{_RST}")
        print(f"  {_DIM}Open it and copy manually, or install xclip (Linux) / pbcopy (macOS).{_RST}")
