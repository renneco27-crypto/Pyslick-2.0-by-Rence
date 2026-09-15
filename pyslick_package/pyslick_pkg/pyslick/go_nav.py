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
_STOP_WORDS = frozenset({
    "go", "to", "the", "a", "an", "in", "at", "cd", "goto",
    "navigate", "change", "dir", "directory", "folder", "please",
})
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
    name = name.lower().strip()

    scope: Path | None = None
    m_in = re.search(r"^(.*?)\s+in\s+(.+)$", name)
    if m_in:
        name = m_in.group(1).strip()
        scope_name = m_in.group(2).strip()
        for folder in _walk_folders(HOME):
            if folder.name.lower() == scope_name:
                scope = folder
                break
        if scope is None:
            candidate = HOME / scope_name
            if candidate.exists() and candidate.is_dir():
                scope = candidate

    search_root = scope if scope else HOME
    # Priority pass: search common user folders first (Desktop, Documents,
    # Downloads, etc.). If exact matches exist there, use them and skip
    # the full-tree walk. This makes "android" resolve to Desktop\android
    # instead of CrossDevice\...\Android when both exist.
    _PRIORITY_DIRS = (
        "Desktop", "Documents", "Downloads", "Videos",
        "Pictures", "Music", "OneDrive",
    )
    if scope is None:  # only when no explicit scope was given
        priority_hits: list[Path] = []
        for pname in _PRIORITY_DIRS:
            pdir = HOME / pname
            if not (pdir.exists() and pdir.is_dir()):
                continue
            for folder in _walk_folders(pdir):
                if folder.name.lower() in {name.strip()}:
                    priority_hits.append(folder)
        if priority_hits:
            return priority_hits, []
    phrase = name.strip()
    phrase_hits = []
    for folder in _walk_folders(search_root):
        if folder.name.lower() == phrase:
            phrase_hits.append(folder)
    if len(phrase_hits) == 1:
        return phrase_hits, []

    # Path interpretation: "documents pyslick" → HOME\documents\pyslick.
    # Only when the query has 2+ tokens, no 'in' keyword, no slashes.
    if " " in name and "/" not in name and "\\" not in name:
        parts = name.split()
        # Try in-order then reversed, both case-sensitively and -insensitively.
        for order in (parts, list(reversed(parts))):
            probe = HOME
            ok = True
            for part in order:
                try:
                    match = next(
                        (d for d in probe.iterdir()
                         if d.is_dir() and d.name.lower() == part.lower()),
                        None,
                    )
                except Exception:
                    match = None
                if match is None:
                    ok = False
                    break
                probe = match
            if ok and probe.is_dir():
                return [probe], []
    tokens = [w for w in re.split(r"[\s/\\]+", name) if w and w not in _STOP_WORDS]
    if not tokens:
        return [], []

    # Exact pass: the folder basename must equal the whole (normalized)
    # query, not just any token. "document pyslick" should not match
    # a folder named "pySlick".
    exact = []
    if len(tokens) == 1:
        target = tokens[0]
        for folder in _walk_folders(search_root):
            if folder.name.lower() == target:
                exact.append(folder)
    if exact:
        return exact, []

    # Partial match: if ALL tokens appear as folder names, list them
    # (multi-word query listing multiple folders). Otherwise fall to fuzzy.
    if len(tokens) > 1:
        partial = []
        for folder in _walk_folders(search_root):
            if folder.name.lower() in tokens:
                partial.append(folder)
        if len(partial) >= 2:
            return partial, []

    try:
        from synonyms import expand as _syn_expand
        from rapidfuzz.fuzz import WRatio
    except Exception:
        return [], []

    terms = set(tokens)
    try:
        for t in _syn_expand(tokens[-1]).get("terms", []):
            if isinstance(t, str) and len(t) >= 3 and t.isalpha():
                terms.add(t.lower())
    except Exception:
        pass

    fuzzy = []
    for folder in _walk_folders(search_root):
        b = folder.name.lower()
        if b in terms:
            fuzzy.append(folder)
            continue
        if any(WRatio(b, t) >= 85 for t in terms):
            fuzzy.append(folder)
    return [], fuzzy

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

def _find_real_shell_pid() -> int:
    """Walk up the process tree to find the actual PowerShell/cmd host."""
    import subprocess as _sp
    pid = os.getppid()
    for _ in range(8):
        try:
            r = _sp.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
            )
            line = (r.stdout or "").strip().splitlines()
            if not line:
                return pid
            # CSV row: "name","pid","session",...
            parts = [p.strip('"') for p in line[0].split('","')]
            name = parts[0].lower().strip('"')
            if any(k in name for k in ("powershell", "pwsh", "cmd", "windowsterminal", "wt")):
                return pid
            # Get parent via wmic (fallback that still works on most Win10/11).
            r2 = _sp.run(
                ["wmic", "process", "where", f"ProcessId={pid}",
                 "get", "ParentProcessId"],
                capture_output=True, text=True, timeout=3,
            )
            lines2 = [l.strip() for l in (r2.stdout or "").splitlines() if l.strip()]
            if len(lines2) < 2:
                return pid
            pid = int(lines2[1])
        except Exception:
            return pid
    return pid


def _spawn_shell_and_exit(path: Path):
    target = str(path)
    print(f"[pyslick] opening new shell in: {target}")
    shell_pid = _find_real_shell_pid()

    # The new shell kills the old one as its first action, then cd's.
    # Wrapped in try/catch so a failed kill doesn't prevent the cd.
    ps_cmd = (
        f"Start-Sleep -Milliseconds 400; "
        f"try {{ Stop-Process -Id {shell_pid} -Force -ErrorAction SilentlyContinue }} catch {{}}; "
        f"Set-Location -LiteralPath '{target}'"
    )
    subprocess.Popen(
        ["powershell", "-NoExit", "-NoProfile", "-Command", ps_cmd],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    sys.exit(0)

    target = str(path)
    print(f"[pyslick] opening new shell in: {target}")
    parent_pid = os.getppid()
    # Build the PowerShell command as a single string with proper escaping.
    # The new shell kills the old one, then cd's and stays open.
    ps_cmd = (
        f"Stop-Process -Id {parent_pid} -Force -ErrorAction SilentlyContinue; "
        f"Set-Location -LiteralPath '{target}'"
    )
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", ps_cmd],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    sys.exit(0)

def _parse_query(directive: str) -> str:
    d = directive.strip().lower()
    d = re.sub(
        r"^(?:please\s+)?(?:go\s+to|goto|cd|change\s+dir(?:ectory)?(?:\s+to)?|navigate\s+to)"
        r"(?:\s+to)?\s+",
        "", d,
    )
    d = re.sub(r"^(?:the\s+)?(?:folder|directory|dir)\s+", "", d)
    d = re.sub(r"^(?:the|a|an)\s+", "", d)
    return d.strip(" ,.")

def _is_cd_query(directive: str) -> bool:
    d = directive.strip().lower()
    if re.match(r"^(?:please\s+)?(?:go\s+to|goto|cd|navigate\s+to|change\s+dir)", d):
        return True
    # Bare digit: "6" after a list was printed.
    if re.match(r"^\d+$", d):
        _, candidates = _load_last_candidates()
        return bool(candidates)

    # Follow-up pick: "<word> <number>" matching the last saved CD query.
    m = re.match(r"^(.+?)\s+(\d+)$", d)
    if not m:
        return False
    name = m.group(1).strip()
    saved_q, candidates = _load_last_candidates()
    if not candidates or not saved_q:
        return False
    if saved_q == name:
        return True
    try:
        from rapidfuzz.fuzz import WRatio
        return WRatio(saved_q, name) >= 80
    except Exception:
        return False


def run_cd(directive: str) -> bool:
    """Top-level handler. Returns True if it handled the directive."""
    if not _is_cd_query(directive):
        return False

    q = _parse_query(directive)
    # Absolute or drive-relative path: cd directly if it exists.
    if re.match(r"^[a-zA-Z]:[\\/]", q) or q.startswith("\\\\"):
        p = Path(q)
        if p.exists() and p.is_dir():
            _bump_keyword(str(p), str(p))
            _spawn_shell_and_exit(p)
        print(f"[pyslick] path does not exist: {q}")
        return True
    if not q:
        print("[pyslick] go to what? example: pyslick \"go to android\"")
        return True

    # Bare digit pick: "1" after a list was printed.
    if re.match(r"^\d+$", q):
        idx = int(q)
        _, candidates = _load_last_candidates()
        if candidates and 1 <= idx <= len(candidates):
            pick = candidates[idx - 1]
            _spawn_shell_and_exit(pick)
        print(f"[pyslick] no saved list, or index {idx} out of range")
        return True

    # "android 2" → pick from last candidate list.
    m = re.match(r"^(.+?)\s+(\d+)$", q)
    if m:
        name, idx = m.group(1).strip(), int(m.group(2))
        saved_q, candidates = _load_last_candidates()
        _fuzzy_ok = False
        if candidates and saved_q:
            if saved_q == name:
                _fuzzy_ok = True
            else:
                try:
                    from rapidfuzz.fuzz import WRatio
                    _fuzzy_ok = WRatio(saved_q, name) >= 80
                except Exception:
                    _fuzzy_ok = False
        if _fuzzy_ok:
            if 1 <= idx <= len(candidates):
                pick = candidates[idx - 1]
                _bump_keyword(name, str(pick))
                _spawn_shell_and_exit(pick)
            print(f"[pyslick] only {len(candidates)} candidate(s) for '{name}'")
            return True
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

    if len(matches) == 1 and exact:
        # Exact single match → open directly.
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