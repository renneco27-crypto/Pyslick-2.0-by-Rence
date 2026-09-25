"""
Fast streaming search with verified output. One pass, no temp files, no BOM.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    with ThreadPoolExecutor(max_workers=jobs) as ex:
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