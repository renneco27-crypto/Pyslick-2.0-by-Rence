#!/usr/bin/env python3
"""
watch.py — Aider-style "save the file, get a fix" mode for pyslick.

    pyslick watch [--root .] [--auto-apply]

Watches the filesystem for saved files containing a marker comment:

    // pyslick? make this handle empty input
    # pyslick? add a docstring

...and calls into the existing agent pipeline (`agent.run_agent`, which
picks API vs. local-LLM mode on its own — see agent.py) with the extracted
directive.

── Safety note (read before changing the default) ─────────────────────────
Every other write path in pyslick shows a diff and asks a real terminal
"yes/no" question before touching a file — see `_present_and_apply_patch`
in agent.py (API path) and the "patch → ... → confirm" step described in
`_run_local_agent`'s docstring (local path). That confirmation gate lives
*inside* agent.py and is never bypassed here, regardless of flags.

What `--auto-apply` actually controls is much narrower than the name might
suggest: without it, detecting a marker comment only prints a notice —
`watch()` does not invoke the agent at all, so nothing is explored, no API
calls are made, and there is nothing to review. With `--auto-apply`,
`watch()` invokes `run_agent()` for you automatically on save — which then
goes through its own existing preview-and-confirm flow exactly as if you'd
typed `pyslick agent "<directive>"` yourself. In neither mode does a save
event, by itself, ever write a file with no human in the loop. If a future
change wants true unattended writes, that requires deliberately threading
a bypass through agent.py's confirm() call sites — a bigger, separate
decision — not something this module should do quietly.
"""

from __future__ import annotations

import os
import re
import time

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = object  # so the class body below can still import

MARKER_PATTERN = re.compile(r"(?://|#)\s*pyslick\?\s*(.*)$", re.MULTILINE)

DEBOUNCE_SECONDS = 1.0


def is_available() -> bool:
    """Whether watchdog is installed. Mirrors the is_available()/_HAS_X
    convention used by llm.py, repomap.py, etc."""
    return _HAS_WATCHDOG


class _PyslickWatchHandler(FileSystemEventHandler):
    """Watches for file saves containing a `// pyslick? <directive>` or
    `# pyslick? <directive>` marker, debounces duplicate save events from
    editors that fire multiple filesystem events per save, and calls
    on_trigger(path, directive) once per genuine save."""

    def __init__(self, on_trigger):
        self.on_trigger = on_trigger
        self._debounce: dict[str, float] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        path = event.src_path

        now = time.time()
        if now - self._debounce.get(path, 0) < DEBOUNCE_SECONDS:
            return
        self._debounce[path] = now

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            return

        m = MARKER_PATTERN.search(content)
        if m:
            directive = m.group(1).strip() or "fix or improve this code"
            self.on_trigger(path, directive)


def _default_on_trigger(path: str, directive: str, auto_apply: bool) -> None:
    print(f"[pyslick watch] marker found: {path} — {directive!r}")
    if not auto_apply:
        print("[pyslick watch] not invoking the agent (pass --auto-apply to do that "
              "automatically on save; it still previews the diff and asks before writing).")
        print(f"[pyslick watch] to act on this now, run:  pyslick agent {directive!r} --root <project>")
        return

    from agent import run_agent
    root = os.path.dirname(os.path.abspath(path)) or "."
    try:
        run_agent(directive, root=root)
    except Exception as e:
        print(f"[pyslick watch] agent run failed: {e}")


def watch(root: str = ".", auto_apply: bool = False, on_trigger=None) -> bool:
    """Blocking call — watches `root` recursively for saved files containing
    a pyslick marker comment. Ctrl+C to stop.

    Returns False immediately (does not block) if watchdog isn't installed,
    same graceful-degradation shape as every other optional dependency in
    this project — the caller should print an install hint and return.
    """
    if not _HAS_WATCHDOG:
        print("watchdog not installed — pip install watchdog")
        return False

    if on_trigger is None:
        def on_trigger(path, directive):
            _default_on_trigger(path, directive, auto_apply)

    handler = _PyslickWatchHandler(on_trigger)
    observer = Observer()
    observer.schedule(handler, root, recursive=True)
    observer.start()
    print(f"[pyslick watch] watching {os.path.abspath(root)} — Ctrl+C to stop")
    if auto_apply:
        print("[pyslick watch] --auto-apply: the agent will run automatically on a "
              "marker save (it still previews the diff and asks before writing).")
    else:
        print("[pyslick watch] pass --auto-apply to run the agent automatically on save.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[pyslick watch] stopping…")
        observer.stop()
    observer.join()
    return True
