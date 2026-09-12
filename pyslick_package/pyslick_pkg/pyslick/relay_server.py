#!/usr/bin/env python3
"""
relay_server.py — bidirectional bridge between pyslick terminal and Claude
==========================================================================

Endpoints
─────────
GET  /relay            → serve ~/.pyslick/relay.json to extension
POST /claude-response  → receive code blocks harvested from Claude's reply,
                         write to ~/.pyslick/claude_response.json,
                         print them to terminal if response_watcher is active
POST /tab-ready        → mark that a claude.ai tab has checked in,
                         write to ~/.pyslick/claude_tab_ready.json

Port: 27182
"""

from __future__ import annotations
import time as _time_mod
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 27182
STATE_DIR       = Path.home() / ".pyslick"
RELAY_FILE      = STATE_DIR / "relay.json"
RESPONSE_FILE   = STATE_DIR / "claude_response.json"
TAB_READY_FILE  = STATE_DIR / "claude_tab_ready.json"

_CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# Callback invoked when /claude-response receives data.
# response_watcher.py sets this to print to terminal.
on_claude_response = None  # type: callable | None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path.rstrip("/") == "/relay":
            try:
                data = RELAY_FILE.read_bytes() if RELAY_FILE.exists() else b'{"blocks":[],"count":0}'
            except Exception as e:
                data = json.dumps({"error": str(e)}).encode()
            self._send(200, data)
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path.rstrip("/") == "/tab-ready":
            length  = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            TAB_READY_FILE.write_text(
                json.dumps({"ready": True, "ts": _time_mod.time()}),
                encoding="utf-8",
            )
            self._send(200, b'{"ok":true}')
            return

        if self.path.rstrip("/") != "/claude-response":
            self._send(404, b'{"error":"not found"}')
            return
        
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
            # Persist to disk so pyslick can read it any time
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            RESPONSE_FILE.write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            # Fire terminal callback if registered
            if callable(on_claude_response):
                try:
                    on_claude_response(payload)
                except Exception:
                    pass
            self._send(200, b'{"ok":true}')
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}).encode())


def _start_thread(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def is_running(port: int = PORT) -> bool:
    """Return True if the relay server is already listening on the given port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()

def ensure_running(port: int = PORT) -> None:
    """Start server in daemon thread if not already up. Called from __init__.py."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    already_up = sock.connect_ex(("127.0.0.1", port)) == 0
    sock.close()
    if not already_up:
        _start_thread(port)


def is_claude_tab_ready() -> bool:
    """True if a claude.ai tab checked in within the last 30 seconds."""
    try:
        data = json.loads(TAB_READY_FILE.read_text(encoding="utf-8"))
        return (_time_mod.time() - data.get("ts", 0)) < 30
    except Exception:
        return False


def clear_tab_ready() -> None:
    """Reset the tab-ready flag before a new ask session."""
    try:
        TAB_READY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


if __name__ == "__main__":
    print(f"  [relay_server] http://127.0.0.1:{PORT}/relay")
    print(f"  [relay_server] POST → /claude-response")
    print(f"  Ctrl+C to stop.")
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [relay_server] Stopped.")
