"""Natural-language folder navigation: pyslick "go to <name>"."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
PYSLICK_DIR = HOME / ".pyslick"
KEYWORDS_FILE = PYSLICK_DIR / "keywords.json"
LAST_CD_FILE = PYSLICK_DIR / "last_cd.json"

_SKIP_DIRS = {
    "AppData", "Application Data", "Windows", "Program Files",
    "Program Files (x86)", "ProgramData", "$Recycle.Bin",
    "System Volume Information", "Local Settings", "My Documents",
    "NetHood", "PrintHood", "Recent", "SendTo", "Templates",
}
_MAX_DEPTH = 4
_MAX_KEYWORDS = 5
_SAVE_THRESHOLD = 3


def _ensure_dir():
    PYSLICK_DIR.mkdir(exist_ok=True)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data):
    _ensure_dir()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _walk_folders(root: Path, max_depth: int = _MAX_DEPTH):
    """Yield all folder paths under root, skipping system/dot dirs."""
    root_depth = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        p = Path(dirpath)
        depth = len(p.parts) - root_depth
        # Filter in-place so os.walk doesn't descend into skipped dirs.
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS
            and not d.startswith(".")
            and not d.startswith("$")
        ]
        if depth >= max_depth:
            dirnames[:] = []
            continue
        for d in dirnames:
            yield p / d


def _find_matches(name: str) -> tuple[list[Path], list[Path]]:
    """Return (exact, substring) matches for name under HOME."""
    target = name.lower()
    exact, substring = [], []
    for folder in _walk_folders(HOME):
        b = folder.name.lower()
        if b == target:
            exact.append(folder)
        elif target in b:
            substring.append(folder)
    return exact, substring


def _load_keywords() -> dict:
    return _load_json(KEYWORDS_FILE, {})


def _save_keywords(kw: dict):
    _save_json(KEYWORDS_FILE, kw)


def _bump_keyword(name: str, path: str):
    kw = _load_keywords()
    entry = kw.get(name, {"count": 0})
    entry["count"] = entry.get("count", 0) + 1
    entry["path"] = path
    entry["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    kw[name] = entry
    # Evict LRU if over cap, but only entries that are over threshold.
    saved = {k: v for k, v in kw.items() if v.get("count", 0) >= _SAVE_THRESHOLD}
    overflow = {k: v for k, v in kw.items() if v.get("count", 0) < _SAVE_THRESHOLD}
    if len(saved) > _MAX_KEYWORDS:
        ordered = sorted(saved.items(), key=lambda kv: kv[1].get("last_used", ""))
        for k, _ in ordered[: len(saved) - _MAX_KEYWORDS]:
            del kw[k]
    _save_keywords(kw)


def _resolve_shortcut(name: str) -> Path | None:
    """If name matches a saved keyword, return its path directly."""
    kw = _load_keywords()
    entry = kw.get(name.lower())
    if entry and entry.get("count", 0) >= _SAVE_THRESHOLD:
        p = Path(entry["path"])
        if p.exists() and p.is_dir():
            return p
    return None


def _save_last_candidates(candidates: list[Path], query: str):
    data = {"query": query, "candidates": [str(p) for p in candidates]}
    _save_json(LAST_CD_FILE, data)


def _load_last_candidates(query: str | None = None) -> tuple[str, list[Path]]:
    data = _load_json(LAST_CD_FILE, {"query": "", "candidates": []})
    q = data.get("query", "")
    paths = [Path(p) for p in data.get("candidates", []) if Path(p).exists()]
    return q, paths


def _spawn_shell_and_exit(path: Path):
    """Open a new PowerShell in `path`, then close this process."""
    target = str(path)
    print(f"[pyslick] opening new shell in: {target}")
    # New PowerShell window, cd'd to target, stays open.
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", f"Set-Location -LiteralPath '{target}'"],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    time.sleep(0.3)
    sys.exit(0)


def _parse_query(directive: str) -> str:
    """Strip leading 'go to' / 'goto' / 'cd' and trailing junk."""
    d = directive.strip().lower()
    d = re.sub(r"^(?:please\s+)?(?:go\s+to|goto|cd|change\s+dir(?:ectory)?(?:\s+to)?|navigate\s+to)\s+", "", d)
    d = re.sub(r"^(?:the\s+)?(?:folder|directory|dir)\s+", "", d)
    d = d.strip(" ,.")
    return d


def _is_cd_query(directive: str) -> bool:
    d = directive.strip().lower()
    return bool(re.match(r"^(?:please\s+)?(?:go\s+to|goto|cd|navigate\s+to|change\s+dir)", d))


def run_cd(directive: str) -> bool:
    """Top-level handler. Returns True if it handled the directive."""
    if not _is_cd_query(directive):
        return False

    q = _parse_query(directive)
    if not q:
        print("[pyslick] go to what? example: pyslick \"go to android\"")
        return True

    # Case 1: "android 2" — pick from last candidate list if query matches.
    m = re.match(r"^(.+?)\s+(\d+)$", q)
    if m:
        name, idx = m.group(1).strip(), int(m.group(2))
        saved_q, candidates = _load_last_candidates()
        if saved_q == name and candidates:
            if 1 <= idx <= len(candidates):
                pick = candidates[idx - 1]
                _bump_keyword(name, str(pick))
                _spawn_shell_and_exit(pick)
            print(f"[pyslick] only {len(candidates)} candidate(s) for '{name}'")
            return True
        # No saved list — fall through and re-search for `name`.

    # Case 2: saved shortcut with enough uses → go directly.
    shortcut = _resolve_shortcut(q)
    if shortcut is not None:
        _bump_keyword(q, str(shortcut))
        _spawn_shell_and_exit(shortcut)

    # Case 3: fresh search.
    exact, substring = _find_matches(q)
    matches = exact or substring

    if not matches:
        print(f"[pyslick] no folder named '{q}' under {HOME}")
        return True

    if len(matches) == 1:
        pick = matches[0]
        _bump_keyword(q, str(pick))
        _spawn_shell_and_exit(pick)

    # Multiple — show list, save, don't pick.
    print(f"[pyslick] {len(matches)} folders named '{q}':")
    for i, p in enumerate(matches, 1):
        try:
            rel = p.relative_to(HOME)
        except ValueError:
            rel = p
        print(f"  {i}. {rel}")
    _save_last_candidates(matches, q)
    print(f"[pyslick] pick one: pyslick \"{q} <number>\"")
    return True