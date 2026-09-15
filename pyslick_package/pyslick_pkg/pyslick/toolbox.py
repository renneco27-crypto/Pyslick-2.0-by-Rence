#!/usr/bin/env python3
"""
toolbox.py — small PowerShell-equivalent read-only commands, bundled so you
don't have to remember the PowerShell one-liners.

    pyslick ls  [root]
        == Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name

    pyslick lines <file>
        == Get-Content -Path <file> | ForEach-Object -Begin {$i=1} -Process {"{0:D3}: {1}" -f $i++,$_}

    pyslick grep <file> <pattern> [<pattern> ...] [--context N]
        == Select-String -Path <file> -Pattern <patterns> -Context N,N
"""

import os
import re
import sys
import json
import shutil
import argparse
import subprocess

CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
BOLD  = "\033[1m"
RST   = "\033[0m"

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", ".pyslick_backups",
    "graphify-out",
}
SKIP_DIR_PREFIXES = ("pyslick_backup_",)


# ─────────────────────────────────────────────────────────────────────────
# ls  ==  Get-ChildItem -Recurse -File | Select-Object -ExpandProperty Name
# ─────────────────────────────────────────────────────────────────────────
SKIP_FILE_EXTS = {".bak", ".map", ".lock", ".pyc"}

# Noise files pyslick should never surface
NOISE_FILES = {
    "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
    "last_query_stamp", "GRAPH_REPORT.md",
}
NOISE_PREFIXES = ("gemini-code-",)
NOISE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
NOISE_PATTERNS = (
    # SHA256-named files (64 hex chars + .json)
    re.compile(r'^[0-9a-f]{64}\.json$'),
    # graphify artifacts
    re.compile(r'^graph\.(html|json)$'),
    # stat/cache json
    re.compile(r'^stat-index\.json$'),
)

def _is_noise(fn: str) -> bool:
    if fn in NOISE_FILES:
        return True
    if any(fn.startswith(p) for p in NOISE_PREFIXES):
        return True
    _, ext = os.path.splitext(fn)
    if ext.lower() in NOISE_EXTS:
        return True
    if any(p.match(fn) for p in NOISE_PATTERNS):
        return True
    return False


def mode_ls(root: str = ".", names_only: bool = True):
    """List source files, grouped by directory.

    Files at the root are printed flat at the top (no heading). Files
    inside a subdirectory are printed under a `[dir] <name>/` heading,
    indented two spaces, so it's obvious which files belong to which
    directory instead of one flat mixed list. Files larger than 30 MB
    are skipped — catches model weights (.gguf, .bin, .safetensors),
    archives, and media without hardcoding extension lists that go stale.
    """
    _MAX_FILE_BYTES = 30 * 1024 * 1024

    def _keep_dir(d: str) -> bool:
        return (
            d not in SKIP_DIRS
            and not d.startswith(".")
            and not any(d.startswith(p) for p in SKIP_DIR_PREFIXES)
        )

    def _keep_file(fn: str, dirpath: str) -> bool:
        if fn.startswith("."):
            return False
        if any(fn.endswith(ext) for ext in SKIP_FILE_EXTS):
            return False
        if _is_noise(fn):
            return False
        try:
            if os.path.getsize(os.path.join(dirpath, fn)) > _MAX_FILE_BYTES:
                return False
        except OSError:
            return False
        return True

    root = os.path.normpath(root)
    root_files: list[str] = []
    subdir_files: dict[str, list[tuple[str, str]]] = {}
    subdir_order: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted([d for d in dirnames if _keep_dir(d)])

        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""

        files_here = sorted(fn for fn in filenames if _keep_file(fn, dirpath))
        if not files_here:
            continue

        if rel_dir == "":
            for fn in files_here:
                root_files.append(
                    fn if names_only
                    else os.path.normpath(os.path.join(dirpath, fn))
                )
        else:
            if rel_dir not in subdir_files:
                subdir_files[rel_dir] = []
                subdir_order.append(rel_dir)
            for fn in files_here:
                subdir_files[rel_dir].append(
                    (fn, os.path.normpath(os.path.join(dirpath, fn)))
                )

    count = 0

    for fn in root_files:
        print(fn)
        count += 1

    if root_files and subdir_order:
        print()

    for i, rel_dir in enumerate(subdir_order):
        entries = subdir_files[rel_dir]
        if not entries:
            continue
        print(f"{DIM}[dir]  {rel_dir}/{RST}")
        for fn, full in entries:
            label = fn if names_only else full
            print(f"  {label}")
            count += 1
        if i < len(subdir_order) - 1:
            print()

    if count == 0:
        print(f"{DIM}(no files found under '{root}'){RST}")



# ─────────────────────────────────────────────────────────────────────────
# grep  ==  Select-String -Pattern ... -Context N,N
# ────────────────────────c─────────────────────────────────────────────────
_RG_BIN = None  # cached: resolved ripgrep binary path, or "" if unavailable


def _ripgrep_binary() -> str:
    global _RG_BIN
    if _RG_BIN is None:
        _RG_BIN = shutil.which("rg") or ""
    return _RG_BIN
def mode_lines(filepath: str, start: int | None = None, end: int | None = None):
    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    lo = 1 if start is None else max(1, start)
    hi = total if end is None else min(total, end)

    if start is not None and end is None and start > 0:
        hi = min(total, start)

    for i in range(lo - 1, hi):
        print(f"{i + 1:03d}: {all_lines[i].rstrip()}")

    if lo > 1 or hi < total:
        print(f"... ({total} lines total; showing {lo}-{hi})")



def _grep_via_ripgrep(path: str, patterns: list[str], context: int) -> list[tuple] | None:
    """One `rg --json` call per pattern (rg's JSON stream doesn't say which
    -e matched, and mode_grep needs to tag each printed hit with its
    pattern). Returns [(line_idx0, pattern, {line_idx0: text, ...}), ...],
    or None if `rg` isn't installed or the call fails — caller falls back
    to the pure-Python scan unchanged."""
    binary = _ripgrep_binary()
    if not binary:
        return None
    hits: list[tuple] = []
    try:
        for pat in patterns:
            cmd = [binary, "--json", f"-C{context}", "-e", pat, "--", path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode not in (0, 1):  # 1 == "no matches", not an error
                return None
            window: dict[int, str] = {}
            match_lines: list[int] = []
            for line in res.stdout.splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") not in ("match", "context"):
                    continue
                data = obj["data"]
                lno = data["line_number"] - 1
                text = data["lines"]["text"].rstrip("\n")
                window[lno] = text
                if obj["type"] == "match":
                    match_lines.append(lno)
            for lno in match_lines:
                hits.append((lno, pat, window))
        return hits
    except (subprocess.TimeoutExpired, OSError, Exception):
        return None


def mode_grep(filepath: str, patterns: list[str], context: int = 1):
    # ── normalize: if a pattern arrived containing '|' (e.g. from a shell
    # that couldn't escape it), split it into separate patterns so
    # "Phone\|Facebook" still works instead of silently matching nothing ──
    normalized = []
    for p in patterns:
        normalized.extend(p.split("|"))
    patterns = [p.strip() for p in normalized if p.strip()]

    if not os.path.exists(filepath):
        print(f"{RED}Error: File '{filepath}' does not exist.{RST}")
        sys.exit(1)

    rg_hits = _grep_via_ripgrep(filepath, patterns, context)
    if rg_hits is not None:
        if not rg_hits:
            print(f"{DIM}No matches for {patterns} in {filepath}{RST}")
            return
        # Mirror the pure-Python loop below exactly (same "..." gap marker,
        # same lack of dedup for overlapping windows) rather than reusing
        # tool_grep's seen_ranges skip — that's a different tool's semantics.
        print(f"{BOLD}{filepath}{RST} — {len(rg_hits)} match(es) {DIM}(via ripgrep){RST}")
        last_printed = -1
        for line_idx, matched_pattern, window in rg_hits:
            lo, hi = max(0, line_idx - context), line_idx + context
            start, end = lo, min(hi, max(window)) + 1
            if start > last_printed + 1:
                print(f"{DIM}  ...{RST}")
            for j in range(start, end):
                marker = f"{GREEN}>{RST}" if j == line_idx else " "
                tag = f"{YELL}[{matched_pattern}]{RST} " if j == line_idx else ""
                print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{window.get(j, '')}")
            last_printed = end - 1
            print()
        return

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    compiled = [re.compile(p) for p in patterns]
    hits = []
    for i, line in enumerate(lines):
        for pat, raw in zip(compiled, patterns):
            if pat.search(line):
                hits.append((i, raw))
                break

    if not hits:
        print(f"{DIM}No matches for {patterns} in {filepath}{RST}")
        return

    print(f"{BOLD}{filepath}{RST} — {len(hits)} match(es)")
    last_printed = -1
    for line_idx, matched_pattern in hits:
        start = max(0, line_idx - context)
        end = min(len(lines), line_idx + context + 1)
        if start > last_printed + 1:
            print(f"{DIM}  ...{RST}")
        for j in range(start, end):
            marker = f"{GREEN}>{RST}" if j == line_idx else " "
            tag = f"{YELL}[{matched_pattern}]{RST} " if j == line_idx else ""
            print(f"{marker} {DIM}{j+1:4d}:{RST} {tag}{lines[j].rstrip()}")
        last_printed = end - 1
        print()


def main():
    parser = argparse.ArgumentParser(prog="pyslick", add_help=False)
    sub = parser.add_subparsers(dest="cmd")

    p_ls = sub.add_parser("ls")
    p_ls.add_argument("root", nargs="?", default=".")
    p_ls.add_argument("--full-path", action="store_true", help="print full paths instead of just filenames")

    p_lines = sub.add_parser("lines")
    p_lines.add_argument("file")
    p_lines.add_argument("range", nargs="?", default=None,
                         help="line number, or start-end (e.g. 17 or 10-25 or 10:25)")
    p_lines.add_argument("--head", type=int, default=None,
                         help="show first N lines")
    p_lines.add_argument("--tail", type=int, default=None,
                         help="show last N lines")

    p_grep = sub.add_parser("grep")
    p_grep.add_argument("file")
    p_grep.add_argument("patterns", nargs="+")
    p_grep.add_argument("--context", type=int, default=1)

    args = parser.parse_args()

    if args.cmd == "ls":
        mode_ls(args.root, names_only=not args.full_path)
    elif args.cmd == "lines":
        start = None
        end = None
        if args.head is not None:
            start = 1
            end = args.head
        elif args.tail is not None:
            with open(args.file, "r", encoding="utf-8", errors="replace") as _f:
                _total = len(_f.readlines())
            start = max(1, _total - args.tail + 1)
            end = _total
        elif args.range:
            m = re.match(r"^(\d+)(?:[-:](\d+))?$", args.range)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else start
            else:
                print(f"{RED}Bad range: {args.range}{RST}")
                sys.exit(1)
        mode_lines(args.file, start=start, end=end)
    elif args.cmd == "grep":
        mode_grep(args.file, args.patterns, context=args.context)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
