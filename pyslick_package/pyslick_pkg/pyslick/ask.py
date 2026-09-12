"""
ask.py  —  pyslick ask <question> [--auto]

Recon loop via browser relay (Miamico extension):
  1. Check if a claude.ai tab is already open (via /tab-ready ping)
     → if yes, reuse it; if two exist, the relay kills the stale one
     → if no, open claude.ai in Edge background
  2. Write skill.md + question → relay.json
  3. content.js auto-polls relay.json (no manual startRelay needed)
  4. Claude responds with one pyslick command
  5. y/n gate (--auto skips it)
  6. Run command → feed result back as next relay.json turn
  7. Detect "I have enough information." → print summary → stop
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
STATE_DIR      = Path.home() / ".pyslick"
RELAY_FILE     = STATE_DIR / "relay.json"
RESPONSE_FILE  = STATE_DIR / "claude_response.json"
TAB_READY_FILE = STATE_DIR / "claude_tab_ready.json"

EDGE_EXE   = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CLAUDE_URL = "https://claude.ai"
STOP_PHRASE = "i have enough information"

# ── ANSI ──────────────────────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM  = "\033[2m"
CYAN = "\033[96m"
YELL = "\033[93m"
GRN  = "\033[92m"
RST  = "\033[0m"


# ── Relay helpers ──────────────────────────────────────────────────────────────

def _ensure_relay() -> None:
    try:
        from .relay_server import ensure_running, is_running, PORT
        if is_running(PORT):
            print(f"{DIM}  [ask] relay server already running on port {PORT}{RST}")
        else:
            ensure_running(PORT)
            time.sleep(0.5)
            if is_running(PORT):
                print(f"{GRN}  [ask] relay server started on port {PORT}{RST}")
            else:
                print(f"{YELL}  [ask] relay server FAILED to start — responses won't reach terminal{RST}", file=sys.stderr)
    except Exception as exc:
        print(f"{YELL}  [ask] relay_server ERROR: {exc}{RST}", file=sys.stderr)
        import traceback
        traceback.print_exc()


def _tab_is_ready() -> bool:
    """
    Returns True if a claude.ai tab with Miamico loaded checked in
    within the last 30 seconds via POST /tab-ready.
    """
    try:
        data = json.loads(TAB_READY_FILE.read_text(encoding="utf-8"))
        return (time.time() - data.get("ts", 0)) < 30
    except Exception:
        return False


def _clear_tab_ready() -> None:
    try:
        TAB_READY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _open_edge_background(url: str) -> None:
    """
    Open URL in Edge without stealing focus.
    Only called if no live claude.ai tab is detected.
    """
    try:
        subprocess.Popen(
            [EDGE_EXE, "--new-tab", url],
            creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True,
        )
        print(f"{DIM}  [ask] Opened {url} in Edge (background){RST}")
    except FileNotFoundError:
        print(f"{YELL}  [ask] Edge not found at {EDGE_EXE} — open claude.ai manually{RST}")
    except Exception as exc:
        print(f"{YELL}  [ask] Could not open Edge: {exc}{RST}")


def _ensure_claude_tab() -> None:
    """
    Reuse existing claude.ai tab if one checked in recently.
    Open a new one only if none is detected after a short wait.
    """
    # Give a recently-opened tab up to 5s to check in before deciding
    for _ in range(5):
        if _tab_is_ready():
            print(f"{DIM}  [ask] claude.ai tab already active — reusing it{RST}")
            return
        time.sleep(1)

    # No live tab — open one
    _open_edge_background(CLAUDE_URL)

    # Wait up to 15s for it to load and check in
    print(f"{DIM}  [ask] Waiting for claude.ai tab to load…{RST}")
    for _ in range(15):
        time.sleep(1)
        if _tab_is_ready():
            print(f"{DIM}  [ask] Tab ready{RST}")
            return

    print(f"{YELL}  [ask] Tab didn't check in — proceeding anyway{RST}")


def _write_relay(blocks: list[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RELAY_FILE.write_text(
        json.dumps({"blocks": blocks}, indent=2),
        encoding="utf-8",
    )


def _clear_response() -> None:
    RESPONSE_FILE.unlink(missing_ok=True)


def _poll_response(timeout: int = 120) -> dict | None:
    """Block until claude_response.json appears or timeout."""
    print(f"{DIM}  [ask] Waiting for Claude…{RST}")
    for _ in range(timeout):
        time.sleep(1)
        if RESPONSE_FILE.exists():
            try:
                data = json.loads(RESPONSE_FILE.read_text(encoding="utf-8"))
                RESPONSE_FILE.unlink(missing_ok=True)  # consume it
                return data
            except Exception:
                pass
    return None


# ── Response parsing ───────────────────────────────────────────────────────────

def _extract_text(response: dict) -> str:
    """Prefer plainText from content.js; fall back to joining code blocks."""
    if "plainText" in response:
        return response["plainText"].strip()
    blocks = response.get("blocks", [])
    return "\n\n".join(b.get("code", "") for b in blocks).strip()


def _extract_pyslick_command(text: str) -> str | None:
    """
    Find a pyslick command in Claude's response.
    Handles:  bare line, ```-fenced block, backtick-prefixed line.
    """
    for line in text.splitlines():
        stripped = line.strip().lstrip("`").strip()
        if stripped.startswith("pyslick "):
            return stripped
    return None


def _is_stop(text: str) -> bool:
    return STOP_PHRASE in text.lower()


# ── Command execution ──────────────────────────────────────────────────────────

def _run_command(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60,
        )
        return (result.stdout + result.stderr).strip()
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60s"
    except Exception as exc:
        return f"Error running command: {exc}"


def _confirm(cmd: str, auto: bool) -> bool:
    if auto:
        print(f"{GRN}  [auto] Running:{RST} {BOLD}{cmd}{RST}")
        return True
    try:
        ans = input(f"\n{CYAN}  Run?{RST} {BOLD}{cmd}{RST}  {DIM}[y/n]{RST} › ").strip().lower()
        return ans in ("y", "yes", "")
    except (EOFError, KeyboardInterrupt):
        return False


# ── Main entry point ───────────────────────────────────────────────────────────

def run_ask(question: str, auto: bool = False) -> None:
    print(f"\n{BOLD}pyslick ask:{RST} {question}\n")

    # Start relay server daemon
    _ensure_relay()

    # Reuse existing claude.ai tab or open a new one — never duplicates
    _ensure_claude_tab()

    # Write the question to relay.json — content.js picks it up automatically
    # (auto-polling starts on page load, no startRelay message needed)
    _write_relay([f"QUESTION: {question}"])
    _clear_response()

    print(f"{DIM}  [ask] Relay written — extension will inject into Claude…{RST}\n")

    turn = 0
    while True:
        turn += 1
        print(f"{DIM}── Turn {turn} {'─' * 40}{RST}")

        response = _poll_response(timeout=120)
        if response is None:
            print(f"{YELL}  [ask] Timed out — no response after 120s.{RST}")
            print(f"{DIM}  Is the Miamico extension active on the claude.ai tab?{RST}")
            break

        text = _extract_text(response)

        # ── Stop condition ────────────────────────────────────────────────────
        if _is_stop(text):
            print(f"\n{GRN}{BOLD}Claude has enough information:{RST}\n")
            idx     = text.lower().find(STOP_PHRASE)
            summary = text[idx + len(STOP_PHRASE):].strip(" .:–-\n")
            print(summary or text)
            print(f"\n{DIM}  [ask] Recon complete — {turn} turn(s).{RST}\n")
            break

        # ── Extract command ───────────────────────────────────────────────────
        cmd = _extract_pyslick_command(text)
        if not cmd:
            print(f"{YELL}  [ask] No pyslick command detected. Claude said:{RST}")
            print(f"  {DIM}{text[:400]}{RST}")
            _write_relay([
                "[pyslick] No command detected in your last reply. "
                "Output exactly one pyslick command on its own line, "
                "or say \"I have enough information.\" followed by your summary."
            ])
            _clear_response()
            continue

        # ── Confirm + run ─────────────────────────────────────────────────────
        if not _confirm(cmd, auto):
            print(f"{DIM}  Skipped.{RST}")
            _write_relay(["[pyslick] User skipped that command. Try a different one."])
            _clear_response()
            continue

        output = _run_command(cmd)
        print(f"\n{DIM}{output[:2000]}{RST}\n")

        # Feed result back for next turn — no skill.md, just the result
        _write_relay([f"[pyslick result for: {cmd}]\n{output}"])
        _clear_response()


# ── CLI shim ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="pyslick ask — relay recon loop")
    p.add_argument("question", nargs="+")
    p.add_argument("--auto", action="store_true")
    args = p.parse_args()
    run_ask(" ".join(args.question), auto=args.auto)
