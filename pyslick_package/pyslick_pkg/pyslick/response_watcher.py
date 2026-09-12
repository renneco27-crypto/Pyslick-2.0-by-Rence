#!/usr/bin/env python3
"""
response_watcher.py — print Claude's response code blocks to the terminal
=========================================================================

When the extension POSTs code blocks from Claude's reply back to the relay
server, this module receives them via the on_claude_response callback and
prints them to the terminal in a readable format — so the loop looks like:

    $ pyslick "help me fix this"
    ... pyslick output with PowerShell blocks ...
    [relay] Injected 2 block(s) into Claude → waiting for response...
    ──────────────────────────────────────────────
    Claude responded with 1 code block(s):

    [powershell]
    Get-Content 'clipboard.py' | Select-Object -Skip 99 -First 42

    ──────────────────────────────────────────────
    $

The watcher is activated by calling register() from __init__.py BEFORE
capture_and_offer runs, so the callback is live for the whole session.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── colours ──────────────────────────────────────────────────────────────────
_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_CYAN  = "\033[96m"
_YELL  = "\033[93m"
_DIM   = "\033[2m"
_RST   = "\033[0m"
_BAR   = f"{_DIM}{'─' * 54}{_RST}"

STATE_DIR     = Path.home() / ".pyslick"
RESPONSE_FILE = STATE_DIR / "claude_response.json"


def _on_response(payload: dict) -> None:
    """
    Called by relay_server when /claude-response receives data from extension.
    Prints the blocks to the real terminal (not through the Tee buffer).
    """
    blocks = payload.get("blocks", [])
    if not blocks:
        return

    # Write directly to the real terminal fd so it appears even if stdout
    # is currently being tee'd into the capture buffer.
    out = sys.__stdout__

    out.write(f"\n{_BAR}\n")
    out.write(
        f"  {_GREEN}✔{_RST} {_BOLD}Claude responded{_RST} "
        f"with {_CYAN}{len(blocks)}{_RST} code block(s):\n\n"
    )
    for block in blocks:
        lang = block.get("lang", "code")
        code = block.get("code", "").strip()
        out.write(f"  {_DIM}[{lang}]{_RST}\n")
        # Indent each line for readability
        for line in code.splitlines():
            out.write(f"  {_CYAN}{line}{_RST}\n")
        out.write("\n")
    out.write(f"{_BAR}\n\n")
    out.flush()


def register() -> None:
    """
    Register the terminal printer with relay_server.
    Call this from __init__.py before capture_and_offer.

    Safe to call multiple times — just overwrites the callback.
    """
    try:
        from .relay_server import ensure_running
        import importlib
        relay_server = importlib.import_module(".relay_server", package=__package__)
        relay_server.on_claude_response = _on_response
        ensure_running()
    except Exception as exc:
        # Non-fatal — if relay server isn't available, just skip
        print(f"  {_YELL}[response_watcher] Warning: {exc}{_RST}", file=sys.stderr)


if __name__ == "__main__":
    # CLI: print the last saved claude_response.json to terminal
    import json
    if RESPONSE_FILE.exists():
        payload = json.loads(RESPONSE_FILE.read_text(encoding="utf-8"))
        _on_response(payload)
    else:
        print(f"  {_YELL}No Claude response saved yet.{_RST}")
        print(f"  {_DIM}(Run with relay active and wait for a response){_RST}")
