$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$targetDir = "pyslick_package\pyslick_pkg\pyslick"
if (-not (Test-Path -LiteralPath $targetDir)) {
    Write-Host "Missing target dir: $targetDir" -ForegroundColor Red
    exit 1
}

function New-Utf8File([string]$path, [string]$content) {
    $parent = Split-Path -Parent $path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, $content, $enc)
    # Verify: re-read and confirm the file has no BOM and matches content
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Write-Host "FAIL: BOM written to $path" -ForegroundColor Red
        exit 1
    }
    Write-Host "  created: $path  ($($bytes.Length) bytes)" -ForegroundColor Green
}

# ── 1. playbook.py ───────────────────────────────────────────────────────
$playbookPath = Join-Path $targetDir "playbook.py"
if (Test-Path -LiteralPath $playbookPath) {
    Write-Host "  exists: $playbookPath  (skipping)" -ForegroundColor Cyan
} else {
    $playbookContent = @'
"""
Verbatim debugging directives. Source of truth. Do not edit, summarize, or
paraphrase — if the source changes, re-copy verbatim.

Source files (as of 2026-09-25):
  # Debugging Skill Directive — Portable Rules from a Real Session.txt
  # Debugging Handoff — Tools & Techniques.txt
"""
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent

PLAYBOOK_TEXT = r"""PLACEHOLDER — replace with the verbatim playbook text."""

HANDOFF_TEXT = r"""PLACEHOLDER — replace with the verbatim handoff text."""


def print_playbook(which: str = "main") -> None:
    if which == "handoff":
        print(HANDOFF_TEXT)
    elif which == "all":
        print(PLAYBOOK_TEXT)
        print("\n" + "=" * 78 + "\n")
        print(HANDOFF_TEXT)
    else:
        print(PLAYBOOK_TEXT)
'@
    New-Utf8File $playbookPath $playbookContent
}

# ── 2. oneshot.py ────────────────────────────────────────────────────────
$oneshotPath = Join-Path $targetDir "oneshot.py"
if (Test-Path -LiteralPath $oneshotPath) {
    Write-Host "  exists: $oneshotPath  (skipping)" -ForegroundColor Cyan
} else {
    $oneshotContent = @'
"""
Fast streaming search with verified output. One pass, no temp files, no BOM.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import os, re, shutil, subprocess

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".next",
    ".venv", "venv", "graphify-out", ".pyslick", ".pyslick_context",
    "coverage", ".cache", ".turbo",
}
CODE_EXTS = {
    ".py", ".pyi", ".pyw",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".cs", ".rb", ".php",
    ".html", ".css", ".scss", ".json", ".yaml", ".yml", ".toml", ".md",
}
BOM = "\ufeff"
REPLACEMENT = "\ufffd"


def collect_files(root="."):
    root_p = Path(root)
    out = []
    for p in root_p.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in CODE_EXTS:
            continue
        try:
            out.append((p, p.stat().st_size))
        except OSError:
            continue
    out.sort(key=lambda t: t[1])
    return [p for p, _ in out]


def safe_read(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="strict")
        return text, None
    except UnicodeDecodeError as e:
        return "", f"non-UTF-8 bytes at offset {e.start}: {e.reason}"
    except Exception as e:
        return "", str(e)


def verify_printable(line):
    return REPLACEMENT not in line and BOM not in line


def enclosing_comment(lines, hit_idx, max_lookback=5):
    buf = []
    for i in range(hit_idx - 1, max(0, hit_idx - max_lookback) - 1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if s.startswith(("#", "//", "/*", "*", '"""', "'''")):
            buf.insert(0, lines[i])
        else:
            break
    return buf


def _grep_one(args):
    path, patterns = args
    text, err = safe_read(path)
    if err:
        return [(str(path), 0, f"<unreadable: {err}>", [])]
    lines = text.split("\n")
    try:
        rx = re.compile("|".join(patterns), re.IGNORECASE)
    except re.error:
        rx = re.compile("|".join(re.escape(p) for p in patterns), re.IGNORECASE)
    hits = []
    for i, line in enumerate(lines):
        if rx.search(line):
            anchor = enclosing_comment(lines, i)
            hits.append((str(path), i + 1, line.rstrip(), anchor))
    return hits


def _try_rg(patterns, root="."):
    rg = shutil.which("rg")
    if not rg:
        return None
    cmd = [rg, "--line-number", "--no-heading", "--color=never", "-e",
           "|".join(patterns)]
    for d in SKIP_DIRS:
        cmd += ["--glob", f"!**/{d}/**"]
    cmd.append(root)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace")
    except Exception:
        return None
    hits = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not verify_printable(line):
            continue
        parts = line.split(":", 2)
        if len(parts) == 3:
            hits.append((parts[0],
                         int(parts[1]) if parts[1].isdigit() else 0,
                         parts[2], []))
    proc.wait()
    return hits


def run_oneshot(query, root=".", jobs=None, use_json=False,
                do_verify=False, file_scope=None):
    import json as _json
    patterns = [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
                if len(t) > 2]
    if not patterns:
        patterns = [re.escape(query)]

    rg_hits = None if file_scope else _try_rg(patterns, root)
    if rg_hits is not None:
        for path, lineno, line, anchor in rg_hits:
            if not verify_printable(line):
                continue
            if use_json:
                print(_json.dumps({"file": path, "line": lineno,
                                   "text": line, "anchor": anchor}),
                      flush=True)
            else:
                print(f"{path}:{lineno}", flush=True)
                print(f"  {line}\n", flush=True)
        return

    if file_scope:
        files = [Path(file_scope)]
    else:
        files = collect_files(root)
    jobs = jobs or os.cpu_count() or 4

    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futures = [ex.submit(_grep_one, (f, patterns)) for f in files]
        for fut in as_completed(futures):
            try:
                for path, lineno, line, anchor in fut.result():
                    if not verify_printable(line):
                        continue
                    if use_json:
                        print(_json.dumps({"file": path, "line": lineno,
                                           "text": line, "anchor": anchor}),
                              flush=True)
                    else:
                        print(f"{path}:{lineno}", flush=True)
                        for a in anchor:
                            print(f"  {a}", flush=True)
                        print(f"  {line}\n", flush=True)
            except Exception:
                continue
'@
    New-Utf8File $oneshotPath $oneshotContent
}

# ── 3. orphans.py ────────────────────────────────────────────────────────
$orphansPath = Join-Path $targetDir "orphans.py"
if (Test-Path -LiteralPath $orphansPath) {
    Write-Host "  exists: $orphansPath  (skipping)" -ForegroundColor Cyan
} else {
    $orphansContent = @'
"""
Dead-code detection: find functions/classes whose name appears only at its
own definition — no calls, imports, or references anywhere in the project.
"""
from collections import Counter, defaultdict
from pathlib import Path
import ast
import re
import sys

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".next",
    ".venv", "venv", "graphify-out", ".pyslick", ".pyslick_context",
    "coverage", ".cache", ".turbo", "models",
}
PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def collect_files(root="."):
    out = []
    for p in Path(root).rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in PY_EXTS | JS_EXTS:
            out.append(p)
    return out


def _read(p):
    try:
        return p.read_text(encoding="utf-8-sig", errors="strict")
    except Exception:
        return ""


def _strip_for_counting(text):
    t = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    t = re.sub(r"'''.*?'''", "", t, flags=re.DOTALL)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.DOTALL)
    t = re.sub(r"#.*", "", t)
    t = re.sub(r"//.*", "", t)
    t = re.sub(r'"(?:[^"\\]|\\.)*"', '""', t)
    t = re.sub(r"'(?:[^'\\]|\\.)*'", "''", t)
    return t


def extract_python_defs(path, text):
    try:
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return []
    defs = []
    exported = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        exported |= set(ast.literal_eval(node.value))
                    except Exception:
                        pass

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                defs.append((child.name, child.lineno,
                             child.name in exported))
            elif isinstance(child, ast.ClassDef):
                walk(child)

    walk(tree)
    return defs


def extract_js_defs(text):
    patterns = [
        (r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", True),
        (r"^\s*export\s+(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)", True),
        (r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", False),
        (r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", False),
    ]
    defs = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, is_export in patterns:
            m = re.match(pat, line)
            if m:
                defs.append((m.group(1), i, is_export))
                break
    return defs


def find_orphans(root="."):
    files = collect_files(root)
    counts = defaultdict(Counter)
    for f in files:
        stripped = _strip_for_counting(_read(f))
        for name in re.findall(r"[A-Za-z_$][\w$]*", stripped):
            counts[name][f] += 1

    orphans = []
    for f in files:
        text = _read(f)
        defs = (extract_python_defs(f, text) if f.suffix == ".py"
                else extract_js_defs(text))
        for name, lineno, exported in defs:
            total = sum(counts[name].values())
            if total <= 1:
                orphans.append({
                    "file": str(f), "name": name, "line": lineno,
                    "exported": exported,
                    "private": name.startswith("_") or name.startswith("#"),
                })

    DUNDER = re.compile(r"^__.*__$")
    orphans = [o for o in orphans
               if not DUNDER.match(o["name"])
               and not o["name"].startswith("test_")]
    orphans.sort(key=lambda o: (o["file"], o["line"]))
    return orphans


def format_report(orphans, json_out=False):
    import json as _json
    if json_out:
        print(_json.dumps(orphans, indent=2))
        return
    print("=" * 72)
    print(f"ORPHANED FUNCTIONS: {len(orphans)}")
    print("(name appears exactly once — at its own definition)")
    print("=" * 72)
    cur = None
    for o in orphans:
        if o["file"] != cur:
            cur = o["file"]
            print(f"\n-- {cur} --")
        flags = []
        if o["exported"]:
            flags.append("EXPORTED")
        if o["private"]:
            flags.append("private")
        tag = f"   [{', '.join(flags)}]" if flags else ""
        print(f"  L{o['line']:<5} {o['name']}{tag}")
'@
    New-Utf8File $orphansPath $orphansContent
}

# ── 4. pre_work.py ───────────────────────────────────────────────────────
$preworkPath = Join-Path $targetDir "pre_work.py"
if (Test-Path -LiteralPath $preworkPath) {
    Write-Host "  exists: $preworkPath  (skipping)" -ForegroundColor Cyan
} else {
    $preworkContent = @'
"""
Pre-work check: before starting a task, ask git whether anyone has already
done it. Searches commit messages, branches, file history, and open PRs.
"""
import re
import subprocess
from shutil import which

try:
    from recon_semantic import _git_run
except ImportError:
    def _git_run(root, *args, timeout=8):
        try:
            r = subprocess.run(["git"] + list(args), cwd=root,
                               capture_output=True, text=True,
                               timeout=timeout)
            return r.returncode, (r.stdout or ""), (r.stderr or "")
        except Exception as e:
            return 1, "", str(e)


def _git(root, *args):
    return _git_run(root, *args)


def _extract_tokens(task):
    stop = {"the", "a", "an", "in", "on", "at", "for", "to", "of", "and",
            "or", "is", "are", "fix", "add", "make", "change", "update"}
    return [w for w in re.findall(r"[a-zA-Z0-9_]{3,}", task.lower())
            if w not in stop]


def search_commits(tokens, max_per_token=5):
    results = {}
    for tok in tokens:
        rc, out, _ = _git(".", "log", "--all", "--oneline",
                          f"--grep={tok}", "-i", f"-{max_per_token}")
        if out.strip():
            results[tok] = out.strip().splitlines()
    return results


def search_branches_for_file(filepath):
    rc, out, _ = _git(".", "branch", "-a", "--contains", "HEAD")
    branches = [b.strip().lstrip("* ") for b in out.splitlines() if b.strip()]
    results = {}
    for b in branches:
        rc2, log, _ = _git(".", "log", "--oneline",
                           f"HEAD..{b}", "--", filepath)
        if log.strip():
            results[b] = log.strip().splitlines()[:5]
    return results


def check_main_has_fix(filepath, tokens):
    if not filepath:
        return []
    hits = []
    for tok in tokens:
        rc, out, _ = _git(".", "log", "--oneline",
                          "HEAD..origin/main",
                          f"--grep={tok}", "-i", "--", filepath)
        if out.strip():
            hits.extend(out.strip().splitlines())
    return list(set(hits))[:10]


def check_open_prs(tokens):
    if not which("gh"):
        return {"available": False}
    rc, out, _ = _git(".", "pr", "list", "--state=open",
                      "--json", "number,title,headRefName")
    if not out.strip():
        return {"available": True, "prs": []}
    import json
    try:
        prs = json.loads(out)
    except Exception:
        return {"available": True, "prs": []}
    matched = [p for p in prs
               if not tokens
               or any(tok in p.get("title", "").lower() for tok in tokens)]
    return {"available": True, "prs": matched}


def run_pre_work(task, filepath=None):
    tokens = _extract_tokens(task)
    print(f"\n== PRE-WORK CHECK: {task!r} ==")
    print(f"tokens: {tokens}\n")

    commits = search_commits(tokens)
    print("-- COMMITS MENTIONING THIS TASK (all branches) --")
    if commits:
        for tok, lines in commits.items():
            print(f"\n  token: {tok}")
            for l in lines:
                print(f"    {l}")
    else:
        print("  (none)")

    if filepath:
        print(f"\n-- BRANCHES WITH UNMERGED CHANGES TO {filepath} --")
        branches = search_branches_for_file(filepath)
        if branches:
            for b, lines in branches.items():
                print(f"\n  {b}:")
                for l in lines:
                    print(f"    {l}")
        else:
            print("  (none)")

        print(f"\n-- MAIN'S COMMITS TO {filepath} NOT IN HEAD --")
        main_hits = check_main_has_fix(filepath, tokens)
        if main_hits:
            for l in main_hits:
                print(f"    {l}")
        else:
            print("  (none)")

    print("\n-- OPEN PRS MENTIONING THIS TASK --")
    prs = check_open_prs(tokens)
    if not prs.get("available"):
        print("  (gh CLI not installed)")
    elif not prs["prs"]:
        print("  (none)")
    else:
        for p in prs["prs"]:
            print(f"    #{p['number']}  {p['title']}  [{p['headRefName']}]")

    print("\n-- ACTION --")
    if commits or prs.get("prs"):
        print("  ! Relevant work may already exist.")
    else:
        print("  OK No existing work found - safe to begin.")
'@
    New-Utf8File $preworkPath $preworkContent
}

Write-Host ""
Write-Host "Batch 2 complete." -ForegroundColor Cyan
Write-Host "Next: paste verbatim playbook text into playbook.py." -ForegroundColor Yellow
exit 0