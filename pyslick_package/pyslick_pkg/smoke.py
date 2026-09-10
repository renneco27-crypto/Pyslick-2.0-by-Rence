#!/usr/bin/env python3
"""
smoke.py — PySlick Smoke Tester, Build Validator & Auto-Debugger
=================================================================

Run after ANY pyslick change to verify nothing is broken.

WHAT IT DOES:
    1. Static checks  — imports, syntax, API surface, missing stubs
    2. Unit tests     — every pyslick command on a self-contained fake project
    3. Build test     — pnpm build / pnpm dev smoke (if a Next.js/Vite project is here)
    4. Runtime errors — auto-catches tracebacks, offers a fix prompt via Claude API
    5. Report         — colour-coded PASS/FAIL per check with actionable next steps

Usage:
    python smoke.py                      # full suite, auto-detect project root
    python smoke.py --root ./my-app      # run against a specific project
    python smoke.py --only static        # just imports + API surface
    python smoke.py --only unit          # just command-level tests
    python smoke.py --only build         # just pnpm build + dev boot
    python smoke.py --fix                # pipe failures through Claude auto-fixer
    python smoke.py --watch              # re-run on any pyslick file change
"""

import os
import sys
import ast
import re
import time
import json
import shutil
import signal
import textwrap
import tempfile
import argparse
import subprocess
import traceback
import importlib
from pathlib import Path
from datetime import datetime
from typing import Optional

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

PASS = f"{GREEN}PASS{RST}"
FAIL = f"{RED}FAIL{RST}"
SKIP = f"{YELL}SKIP{RST}"
WARN = f"{YELL}WARN{RST}"

# ── config ──────────────────────────────────────────────────────────────────
PYSLICK_MODULES = [
    "__init__", "patchit", "recon", "query", "toolbox",
    "graphify", "graphify_sitter", "indentation", "jsx_tag_checker",
    "find_nearest_nodes", "find_stray_symbols",
]

# The public API surface each module MUST expose. If these are missing,
# other commands will crash at runtime even though `import` succeeds.
REQUIRED_API = {
    "patchit": [
        "mode_show_lines", "mode_find_replace", "mode_find",
        "mode_paste", "mode_regex", "mode_insert",
        "write_with_safety", "backup_file", "validate_python_syntax",
        "read_file", "reindent_to_match",
    ],
    "toolbox": ["mode_ls", "mode_lines", "mode_grep"],
    "graphify": ["query"],
    "indentation": ["analyze_indentation_scopes"],
    "jsx_tag_checker": ["check_jsx_tags"],
    "find_nearest_nodes": [
        "load_graph_nodes", "load_graphify_vocab",
        "expand_query_with_vocab", "find_closest_graph_nodes",
    ],
    "__init__": [
        "main", "git_checkpoint", "git_log",
        "git_status", "git_rollback", "git_stash",
    ],
}

# CLI commands and the argv they expect; we check they don't crash on --help
CLI_SMOKE = [
    (["pyslick", "--help"],        "help text"),
    (["pyslick", "ls", "--help"],  None),          # may not have --help, just shouldn't crash
    (["pyslick", "query", "--help"], "directive"),
]

# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
class Result:
    """Tracks pass/fail/skip counts and accumulates failure details."""
    def __init__(self):
        self.passed = self.failed = self.skipped = self.warned = 0
        self.failures: list[dict] = []

    def record(self, label: str, ok: bool, detail: str = "", warn: bool = False):
        marker = PASS if ok else (WARN if warn else FAIL)
        print(f"  {marker}  {label}")
        if detail and not ok:
            for line in detail.strip().splitlines():
                print(f"        {DIM}{line}{RST}")
        if ok:
            self.passed += 1
        elif warn:
            self.warned += 1
        else:
            self.failed += 1
            self.failures.append({"label": label, "detail": detail})

    def skip(self, label: str, reason: str = ""):
        print(f"  {SKIP}  {label}" + (f"  {DIM}({reason}){RST}" if reason else ""))
        self.skipped += 1

    def summary(self):
        total = self.passed + self.failed + self.skipped + self.warned
        bar = (f"{GREEN}{'█' * self.passed}{RST}"
               f"{RED}{'█' * self.failed}{RST}"
               f"{YELL}{'░' * self.warned}{RST}"
               f"{DIM}{'·' * self.skipped}{RST}")
        print(f"\n{BOLD}{'━'*60}{RST}")
        print(f"{bar}")
        print(f"{BOLD}Results:{RST}  "
              f"{GREEN}{self.passed} passed{RST}  "
              f"{RED}{self.failed} failed{RST}  "
              f"{YELL}{self.warned} warnings{RST}  "
              f"{DIM}{self.skipped} skipped{RST}  "
              f"/ {total} total")
        if self.failures:
            print(f"\n{BOLD}Failures:{RST}")
            for i, f in enumerate(self.failures, 1):
                print(f"  {RED}{i}. {f['label']}{RST}")
                if f['detail']:
                    short = f['detail'].strip().splitlines()[-1][:120]
                    print(f"     {DIM}{short}{RST}")
        return self.failed == 0


def section(title: str):
    print(f"\n{BOLD}{CYAN}━━  {title}  {RST}")


def run_cmd(args: list[str], timeout: int = 15, cwd: str = None) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            args, capture_output=True, text=True,
            timeout=timeout, cwd=cwd or os.getcwd()
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"TIMEOUT after {timeout}s"
    except FileNotFoundError as e:
        return -2, "", str(e)
    except Exception as e:
        return -3, "", str(e)


def find_pyslick_root() -> Optional[str]:
    """Find the pyslick package directory on sys.path."""
    for p in sys.path:
        candidate = os.path.join(p, "pyslick")
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "__init__.py")):
            return candidate
    # also check the cwd and parent
    for check in [os.getcwd(), os.path.dirname(os.getcwd())]:
        candidate = os.path.join(check, "pyslick")
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "__init__.py")):
            return candidate
    return None


# ─────────────────────────────────────────────────────────────────────────
# Phase 1 — Static checks (no subprocess, pure AST + importlib)
# ─────────────────────────────────────────────────────────────────────────
def phase_static(res: Result, pyslick_dir: str):
    section("Phase 1 — Static Checks (imports, API surface, syntax)")

    for mod_name in PYSLICK_MODULES:
        mod_path = os.path.join(pyslick_dir, f"{mod_name}.py")
        if not os.path.exists(mod_path):
            res.skip(f"syntax: {mod_name}.py", "file not found")
            continue

        # Syntax check via ast.parse (catches IndentationError, SyntaxError)
        try:
            with open(mod_path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            ast.parse(src, filename=mod_path)
            res.record(f"syntax: {mod_name}.py", True)
        except SyntaxError as e:
            res.record(f"syntax: {mod_name}.py", False,
                       f"SyntaxError at line {e.lineno}: {e.msg}")
            continue

        # Import check
        # recon.py uses `from . import ...` (relative) so must be loaded as
        # part of the package, not as a standalone file. Same for __init__.
        sys.path.insert(0, os.path.dirname(pyslick_dir))
        try:
            if mod_name in ("recon", "__init__", "__main__"):
                pkg_target = "pyslick" if mod_name == "__init__" else f"pyslick.{mod_name}"
                # Invalidate any stale cached import so we always re-import
                for k in list(sys.modules.keys()):
                    if k == "pyslick" or k.startswith("pyslick."):
                        del sys.modules[k]
                mod = importlib.import_module(pkg_target)
            else:
                _spec = importlib.util.spec_from_file_location(f"_smoke_{mod_name}", mod_path)
                mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(mod)
            res.record(f"import: {mod_name}", True)
        except Exception as e:
            tb = traceback.format_exc()
            res.record(f"import: {mod_name}", False, tb)
            continue

        # API surface check
        required = REQUIRED_API.get(mod_name, [])
        for attr in required:
            has_it = hasattr(mod, attr)
            res.record(f"api: {mod_name}.{attr}", has_it,
                       f"'{attr}' is missing from {mod_name}.py — other modules will crash at runtime")


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 — Fake project + CLI unit tests
# ─────────────────────────────────────────────────────────────────────────
def _build_fake_project(tmp: str):
    """Write a minimal fake project in `tmp` for command-level tests."""
    src = os.path.join(tmp, "src")
    os.makedirs(src, exist_ok=True)

    # A Python file with a real function and docstring
    (Path(src) / "mic_button.py").write_text(textwrap.dedent("""\
        def load_mic_icon(size=24):
            \"\"\"Load the microphone SVG icon at the given size.\"\"\"
            return f"<svg width='{size}' height='{size}'></svg>"

        def build_button_widget(label="Mic"):
            \"\"\"Build the button DOM node.\"\"\"
            icon = load_mic_icon()
            return f'<button class="mic-btn">{icon}{label}</button>'

        def render_mic_button():
            \"\"\"Public entry — renders the mic button into the toolbar.\"\"\"
            widget = build_button_widget()
            render_call_toolbar(widget)

        def render_call_toolbar(content):
            \"\"\"Render the whole call toolbar.\"\"\"
            print(f'<div class="toolbar">{content}</div>')
    """), encoding="utf-8")

    # A tsx file for non-Python match testing
    (Path(src) / "MicButton.tsx").write_text(textwrap.dedent("""\
        import React from 'react';
        export function MicButton({ size = 24 }: { size?: number }) {
          return (
            <button className="mic-btn rounded-full p-2">
              <svg width={size} height={size} />
            </button>
          );
        }
    """), encoding="utf-8")

    # A CSS file
    (Path(src) / "buttons.css").write_text(textwrap.dedent("""\
        .mic-btn {
          background: #1a1a2e;
          border-radius: 50%;
          padding: 8px;
        }
        .toolbar {
          display: flex;
          gap: 8px;
        }
    """), encoding="utf-8")

    # An HTML file
    (Path(src) / "index.html").write_text(textwrap.dedent("""\
        <!DOCTYPE html>
        <html>
        <head><link rel="stylesheet" href="buttons.css"></head>
        <body>
          <div id="call-toolbar" class="toolbar">
            <button id="mic-btn" class="mic-btn" onclick="toggleMic()">Mic</button>
          </div>
          <script src="main.js"></script>
        </body>
        </html>
    """), encoding="utf-8")

    # A JS file
    (Path(src) / "main.js").write_text(textwrap.dedent("""\
        function toggleMic() {
          const btn = document.getElementById('mic-btn');
          btn.classList.toggle('active');
        }
        function resizeMicButton(size) {
          const btn = document.querySelector('.mic-btn');
          btn.style.width = size + 'px';
          btn.style.height = size + 'px';
        }
    """), encoding="utf-8")

    return tmp


def phase_unit(res: Result, pyslick_dir: str):
    section("Phase 2 — Unit Tests (commands on a fake project)")

    with tempfile.TemporaryDirectory(prefix="pyslick_smoke_") as tmp:
        _build_fake_project(tmp)

        # ── pyslick ls ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "ls"],
            cwd=tmp
        )
        res.record("pyslick ls: exits 0", rc == 0, err)
        res.record("pyslick ls: finds mic_button.py",
                   "mic_button.py" in out,
                   f"stdout was:\n{out[:500]}")

        # ── pyslick ls --full-path ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "ls", "--full-path"],
            cwd=tmp
        )
        res.record("pyslick ls --full-path: includes path separators",
                   os.sep in out or "/" in out, f"stdout:\n{out[:300]}")

        # ── pyslick lines ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "lines", "src/mic_button.py"],
            cwd=tmp
        )
        res.record("pyslick lines: exits 0", rc == 0, err)
        res.record("pyslick lines: numbered output",
                   re.search(r"^\d+:", out, re.MULTILINE) is not None,
                   f"stdout:\n{out[:300]}")

        # ── pyslick grep ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "grep", "src/mic_button.py", "toolbar"],
            cwd=tmp
        )
        res.record("pyslick grep: exits 0", rc == 0, err)
        res.record("pyslick grep: finds 'toolbar'",
                   "toolbar" in out, f"stdout:\n{out[:300]}")

        # ── pyslick grep multiple patterns ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "grep", "src/mic_button.py",
             "render_mic_button", "load_mic_icon", "--context", "2"],
            cwd=tmp
        )
        res.record("pyslick grep multi-pattern: finds both",
                   "render_mic_button" in out and "load_mic_icon" in out,
                   f"stdout:\n{out[:400]}")

        # ── pyslick query (no graphify graph — falls back to file scan) ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "query",
             "make the mic button bigger", "--root", "."],
            cwd=tmp
        )
        res.record("pyslick query: exits 0", rc == 0,
                   f"stderr:\n{err[:400]}\nstdout:\n{out[:400]}")
        res.record("pyslick query: finds mic_button.py",
                   "mic_button" in out.lower(),
                   f"stdout:\n{out[:600]}")
        res.record("pyslick query: non-Python files listed in footer",
                   "MicButton.tsx" in out or "Non-Python" in out,
                   f"stdout:\n{out[:600]}")

        # ── pyslick patchit -l (read-only, safe to test) ──
        mic_py = os.path.join(tmp, "src", "mic_button.py")
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "patchit", mic_py, "-l"],
            cwd=tmp
        )
        res.record("pyslick patchit -l: exits 0", rc == 0, err)
        # Strip ANSI codes before matching — patchit -l uses colour for line numbers
        out_plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        res.record("pyslick patchit -l: shows line numbers",
                   re.search(r"\s+\d+\s+\S", out_plain) is not None,
                   f"stdout (plain):\n{out_plain[:300]}")

        # ── pyslick jsx-check ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "jsx-check", "src/MicButton.tsx"],
            cwd=tmp
        )
        # jsx-check may exit 0 or 1; we just want no crash (not -1 / -2 / -3)
        res.record("pyslick jsx-check: doesn't crash",
                   rc in (0, 1), f"rc={rc} stderr:\n{err[:300]}")

        # ── pyslick graphify-query on a .py file ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "graphify-query",
             "src/mic_button.py", "mic button size"],
            cwd=tmp
        )
        res.record("pyslick graphify-query: exits 0", rc == 0,
                   f"stderr:\n{err[:300]}")

        # ── pyslick indentation ──
        rc, out, err = run_cmd(
            [sys.executable, "-m", "pyslick", "indentation",
             "src/mic_button.py"],
            cwd=tmp
        )
        res.record("pyslick indentation: exits 0", rc == 0,
                   f"stderr:\n{err[:300]}")
        res.record("pyslick indentation: reports clean or lists issues",
                   ("OK" in out or "No scope" in out or "indentation" in out.lower()),
                   f"stdout:\n{out[:300]}")

        # ── patchit internal: write_with_safety blocks bad Python ──
        _test_syntax_guard(res, tmp)

        # ── patchit internal: reindent_to_match ──
        _test_reindent(res)


def _test_syntax_guard(res: Result, tmp: str):
    """Directly test that write_with_safety refuses invalid Python (unit-level)."""
    sys.path.insert(0, find_pyslick_root() or tmp)
    try:
        import importlib.util
        patchit_path = os.path.join(find_pyslick_root() or tmp, "patchit.py")
        spec = importlib.util.spec_from_file_location("_smoke_patchit", patchit_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        err = mod.validate_python_syntax("test.py", "def foo(:\n    pass\n")
        res.record("patchit.validate_python_syntax: catches bad Python",
                   err is not None, f"returned: {err!r}")

        err_ok = mod.validate_python_syntax("test.py", "def foo():\n    pass\n")
        res.record("patchit.validate_python_syntax: passes good Python",
                   err_ok is None, f"returned: {err_ok!r}")

        err_skip = mod.validate_python_syntax("test.tsx", "this is not python at all |||")
        res.record("patchit.validate_python_syntax: skips non-.py files",
                   err_skip is None, f"returned: {err_skip!r}")
    except Exception as e:
        res.record("patchit.validate_python_syntax: unit test", False,
                   traceback.format_exc())


def _test_reindent(res: Result):
    """Test reindent_to_match doesn't destroy indentation."""
    try:
        patchit_dir = find_pyslick_root()
        if not patchit_dir:
            res.skip("patchit.reindent_to_match", "pyslick not found on path")
            return
        import importlib.util
        patchit_path = os.path.join(patchit_dir, "patchit.py")
        spec = importlib.util.spec_from_file_location("_smoke_patchit2", patchit_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        target = "    def foo(self):\n        pass\n"
        replacement = "def foo(self):\n    return 42\n"
        result = mod.reindent_to_match(target, replacement)
        starts_with_4_spaces = result.startswith("    ")
        res.record("patchit.reindent_to_match: preserves base indentation",
                   starts_with_4_spaces, f"got: {result!r}")
    except Exception:
        res.record("patchit.reindent_to_match: unit test", False,
                   traceback.format_exc())


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 — Build tests (pnpm / npm / next)
# ─────────────────────────────────────────────────────────────────────────
def _detect_js_project(root: str) -> Optional[str]:
    """Return 'pnpm', 'npm', or None based on lockfile."""
    if os.path.exists(os.path.join(root, "pnpm-lock.yaml")):
        return "pnpm"
    if os.path.exists(os.path.join(root, "package-lock.json")):
        return "npm"
    if os.path.exists(os.path.join(root, "package.json")):
        return "npm"
    return None


def _read_package_json(root: str) -> dict:
    pkg = os.path.join(root, "package.json")
    if not os.path.exists(pkg):
        return {}
    try:
        with open(pkg, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def phase_build(res: Result, project_root: str):
    section("Phase 3 — Build Tests (pnpm build / dev boot check)")

    pm = _detect_js_project(project_root)
    if not pm:
        res.skip("JS/TS build", "no package.json found — not a JS/TS project")
        return

    pkg = _read_package_json(project_root)
    scripts = pkg.get("scripts", {})

    # ── type-check (tsc --noEmit) — fastest, no output written ──
    has_tsc = shutil.which("tsc") is not None or os.path.exists(
        os.path.join(project_root, "node_modules", ".bin", "tsc")
    )
    tsconfig = os.path.exists(os.path.join(project_root, "tsconfig.json"))

    if has_tsc and tsconfig:
        tsc_bin = shutil.which("tsc") or os.path.join(project_root, "node_modules", ".bin", "tsc")
        rc, out, err = run_cmd(
            [tsc_bin, "--noEmit"], timeout=60, cwd=project_root
        )
        detail = (out + err)[:600]
        res.record("tsc --noEmit (type check)", rc == 0, detail)
    else:
        res.skip("tsc --noEmit", "tsc not found or no tsconfig.json")

    # ── pnpm/npm build ──
    if "build" in scripts:
        print(f"\n  {DIM}Running: {pm} run build (may take 30–90s)…{RST}")
        rc, out, err = run_cmd(
            [pm, "run", "build"], timeout=120, cwd=project_root
        )
        detail = (out + err)[-800:]
        res.record(f"{pm} run build: exits 0", rc == 0, detail)

        # Check for common Next.js error patterns in output
        combined = (out + err).lower()
        if rc == 0:
            has_type_err = "type error" in combined or "typeerror" in combined
            has_module_err = "module not found" in combined or "cannot find module" in combined
            if has_type_err:
                res.record("build: no TypeScript errors in output", False,
                           "Build succeeded but output contains 'type error'")
            if has_module_err:
                res.record("build: no missing module errors", False,
                           "Build succeeded but output contains 'module not found'")
    else:
        res.skip(f"{pm} run build", "no 'build' script in package.json")

    # ── dev server boot check (10-second boot probe) ──
    if "dev" in scripts:
        _probe_dev_server(res, pm, project_root)
    else:
        res.skip(f"{pm} run dev boot probe", "no 'dev' script in package.json")

    # ── ESLint (if configured) ──
    eslint_cfg = any(
        os.path.exists(os.path.join(project_root, f))
        for f in [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.cjs", "eslint.config.js"]
    )
    eslint_bin = (shutil.which("eslint") or
                  os.path.join(project_root, "node_modules", ".bin", "eslint"))

    if eslint_cfg and os.path.exists(eslint_bin):
        rc, out, err = run_cmd(
            [eslint_bin, "src", "--ext", ".ts,.tsx,.js,.jsx", "--max-warnings", "0"],
            timeout=60, cwd=project_root
        )
        res.record("eslint: 0 warnings/errors", rc == 0, (out + err)[:600])
    else:
        res.skip("eslint", "no eslint config or binary found")


def _probe_dev_server(res: Result, pm: str, cwd: str):
    """Start dev server, wait up to 12s, check it binds a port, kill it."""
    print(f"\n  {DIM}Probing dev server boot (12s timeout)…{RST}")
    proc = None
    try:
        proc = subprocess.Popen(
            [pm, "run", "dev"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=cwd, text=True, bufsize=1
        )

        deadline = time.time() + 12
        booted = False
        boot_markers = [
            "ready", "local:", "localhost", "started server",
            "✓ ready", "listening on", "http://",
        ]
        output_lines = []

        while time.time() < deadline:
            line = ""
            try:
                import select
                if select.select([proc.stdout], [], [], 0.5)[0]:
                    line = proc.stdout.readline()
            except Exception:
                time.sleep(0.5)
                try:
                    line = proc.stdout.readline()
                except Exception:
                    pass

            if line:
                output_lines.append(line.rstrip())
            if any(m in line.lower() for m in boot_markers):
                booted = True
                break
            if proc.poll() is not None:
                break

        res.record("dev server: boots within 12s", booted,
                   "Last output:\n" + "\n".join(output_lines[-5:]))

    except Exception as e:
        res.record("dev server boot probe", False, str(e))
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 — Extension / browser console log check
# ─────────────────────────────────────────────────────────────────────────
def phase_extension_check(res: Result, project_root: str):
    """
    Checks for known patterns that cause Chrome extension / Electron errors:
    - console.error / window.onerror patterns left in source
    - Missing electronAPI references in renderer files
    - Unhandled promise rejections in main process
    - webpack/vite chunk errors

    This is a STATIC pass — it scans source text, not a live browser.
    For live browser console errors, hook window.onerror in your app and
    POST them to a local endpoint; smoke.py can poll that endpoint if you
    add --live-check to the dev probe above.
    """
    section("Phase 4 — Extension / Console Error Patterns (static scan)")

    src_exts = {".ts", ".tsx", ".js", ".jsx"}
    scan_dirs_skip = {
        "node_modules", ".git", ".next", "dist", "build",
        "__pycache__", ".turbo", ".cache",
    }

    src_files = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in scan_dirs_skip]
        for fn in filenames:
            if Path(fn).suffix in src_exts:
                src_files.append(os.path.join(dirpath, fn))

    if not src_files:
        res.skip("extension/console scan", "no TS/JS source files found")
        return

    # Patterns that reliably cause runtime console errors
    BAD_PATTERNS = [
        (r"window\.electronAPI\b", "electronAPI ref",
         "electronAPI used — verify preload exposes it (will throw in browser env)"),
        (r"process\.env\b", "process.env in browser",
         "process.env in a browser file — use import.meta.env for Vite or Next.js"),
        (r"\.catch\s*\(\s*\)", "empty .catch()",
         "Swallowed promise rejection — errors will be invisible at runtime"),
        (r"console\.error\s*\(['\"]TODO", "TODO console.error",
         "Left-in TODO console.error calls"),
        (r"throw new Error\(['\"]NOT_IMPLEMENTED", "NOT_IMPLEMENTED throw",
         "Unimplemented stub will throw at runtime"),
    ]

    flagged = []
    for fpath in src_files:
        rel = os.path.relpath(fpath, project_root)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception:
            continue
        for pattern, label, note in BAD_PATTERNS:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    flagged.append((rel, i, label, note, line.strip()))

    if flagged:
        grouped: dict[str, list] = {}
        for rel, lineno, label, note, line in flagged:
            grouped.setdefault(label, []).append((rel, lineno, note, line))
        for label, hits in grouped.items():
            detail = "\n".join(f"  {r}:{n}  →  {ln}" for r, n, note, ln in hits[:5])
            note = hits[0][2]
            res.record(f"ext scan: {label}", False,
                       f"{note}\n{detail}")
    else:
        res.record("extension/console scan: no risky patterns", True)


# ─────────────────────────────────────────────────────────────────────────
# Phase 5 — Auto-fixer (Claude API)
# ─────────────────────────────────────────────────────────────────────────
def phase_autofix(results: Result, pyslick_dir: str):
    """Send failures to Claude API and print suggested fixes."""
    if not results.failures:
        print(f"\n{GREEN}Nothing to fix — all checks passed!{RST}")
        return

    section("Phase 5 — Auto-Fixer (Claude API)")

    failures_text = "\n".join(
        f"FAILURE {i+1}: {f['label']}\n{f['detail']}"
        for i, f in enumerate(results.failures)
    )

    # Read relevant source files to give Claude context
    context_snippets = []
    for mod_name in PYSLICK_MODULES[:6]:  # keep prompt reasonable
        mp = os.path.join(pyslick_dir, f"{mod_name}.py")
        if os.path.exists(mp):
            with open(mp, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            # truncate very long files
            if len(src) > 3000:
                src = src[:1500] + "\n...[truncated]...\n" + src[-1500:]
            context_snippets.append(f"=== {mod_name}.py ===\n{src}")
    context = "\n\n".join(context_snippets[:4])  # cap total context

    prompt = f"""You are debugging pyslick, a Python CLI tool for code editing.
Here are the smoke test failures:

{failures_text}

Here is the relevant source code:

{context}

For each failure, give:
1. Root cause (1 sentence)
2. Exact fix — the minimal code change needed (show the old line and new line)
3. Which file to edit

Be specific and brief. Format as:
FAILURE 1:
  Root cause: ...
  Fix: In <file>, change:
    OLD: <old line>
    NEW: <new line>

Only suggest fixes you are confident about."""

    try:
        import urllib.request
        payload = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            text = "".join(
                block["text"] for block in data.get("content", [])
                if block.get("type") == "text"
            )
            print(f"\n{BOLD}Suggested fixes:{RST}")
            print(text)
    except Exception as e:
        print(f"{YELL}Auto-fixer unavailable: {e}{RST}")
        print(f"{DIM}(The fixer uses the Anthropic API — it works when smoke.py runs inside a Claude Artifact.){RST}")
        print(f"\n{BOLD}Manual triage:{RST}")
        for i, f in enumerate(results.failures, 1):
            print(f"\n{RED}{i}. {f['label']}{RST}")
            if f['detail']:
                print(textwrap.indent(f['detail'].strip()[:400], "   "))


# ─────────────────────────────────────────────────────────────────────────
# Phase 6 — Watch mode
# ─────────────────────────────────────────────────────────────────────────
def watch_mode(args, pyslick_dir: str, project_root: str):
    """Re-run smoke suite whenever a pyslick .py file changes."""
    print(f"{CYAN}Watch mode — monitoring {pyslick_dir} for changes. Ctrl-C to quit.{RST}\n")

    def get_mtimes():
        mtimes = {}
        for f in Path(pyslick_dir).glob("*.py"):
            mtimes[str(f)] = f.stat().st_mtime
        return mtimes

    last = get_mtimes()
    try:
        while True:
            time.sleep(1)
            current = get_mtimes()
            changed = [f for f in current if current[f] != last.get(f)]
            if changed:
                print(f"\n{YELL}Changed: {', '.join(os.path.basename(f) for f in changed)}{RST}")
                print(f"{DIM}{datetime.now().strftime('%H:%M:%S')} — re-running smoke tests…{RST}\n")
                _run_suite(args, pyslick_dir, project_root)
                last = current
    except KeyboardInterrupt:
        print(f"\n{DIM}Watch stopped.{RST}")


# ─────────────────────────────────────────────────────────────────────────
# Suite runner
# ─────────────────────────────────────────────────────────────────────────
def _run_suite(args, pyslick_dir: str, project_root: str) -> bool:
    res = Result()
    only = getattr(args, "only", None)

    if not only or only == "static":
        phase_static(res, pyslick_dir)
    if not only or only == "unit":
        phase_unit(res, pyslick_dir)
    if not only or only == "build":
        phase_build(res, project_root)
        phase_extension_check(res, project_root)

    ok = res.summary()

    if getattr(args, "fix", False) and res.failures:
        phase_autofix(res, pyslick_dir)

    # Machine-readable exit code — useful for CI
    return ok


# ─────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        prog="python smoke.py",
        description="PySlick smoke tester, build validator & auto-debugger",
    )
    parser.add_argument("--root", default=".",
                        help="Project root for build tests (default: cwd)")
    parser.add_argument("--only", choices=["static", "unit", "build"],
                        help="Run only one phase")
    parser.add_argument("--fix", action="store_true",
                        help="On failure, call Claude API for suggested fixes")
    parser.add_argument("--watch", action="store_true",
                        help="Watch pyslick source files and re-run on change")
    args = parser.parse_args()

    project_root = os.path.abspath(args.root)
    pyslick_dir = find_pyslick_root()

    print(f"{BOLD}{CYAN}")
    print("╔══════════════════════════════════════════════════╗")
    print("║   PySlick Smoke Tester & Build Validator  v1.0  ║")
    print("╚══════════════════════════════════════════════════╝")
    print(RST)
    print(f"  pyslick dir : {pyslick_dir or DIM+'not found'+RST}")
    print(f"  project root: {project_root}")
    print(f"  time        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not pyslick_dir:
        print(f"\n{RED}ERROR: pyslick package not found on sys.path.{RST}")
        print("Make sure you've run:  pip install -e .")
        print("Or run smoke.py from inside your pyslick_pkg directory.")
        sys.exit(1)

    if args.watch:
        watch_mode(args, pyslick_dir, project_root)
    else:
        ok = _run_suite(args, pyslick_dir, project_root)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
