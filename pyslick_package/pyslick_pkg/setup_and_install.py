#!/usr/bin/env python3
"""
setup_and_install.py — PySlick one-shot installer
===================================================
Run this once from the pyslick_package folder:

    python setup_and_install.py

What it does, in order:
  1. Ensures Python ≥ 3.10
  2. pip install -e pyslick_pkg            (core + rapidfuzz)
  3. pip install rapidfuzz                 (in case editable install skipped it)
  4. pip install tree-sitter tree-sitter-typescript  (optional TSX support)
  5. pip install llama-cpp-python          (optional local LLM expand)
  6. Download SmolLM2-135M-Instruct GGUF  (~90 MB) to ~/.pyslick/models/
  7. Wire the llm-status command in (patches __init__.py if needed)
  8. Smoke-test: pyslick --help
"""

import os
import sys
import subprocess
import urllib.request
import shutil
from pathlib import Path

# Set UTF-8 encoding for stdout/stderr (Windows PowerShell safe)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
GREEN = "\033[92m"
CYAN  = "\033[96m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

def ok(msg):   print(f"{GREEN}  ✔ {msg}{RST}")
def warn(msg): print(f"{YELL}  ⚠ {msg}{RST}")
def err(msg):  print(f"{RED}  ✖ {msg}{RST}")
def hdr(n, t): print(f"\n{BOLD}{CYAN}━━  Step {n}  {RST}{BOLD}{t}{RST}\n{DIM}{'─'*58}{RST}")

# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def pip(*pkgs, editable_path=None):
    """Run pip install, optionally with -e."""
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"]
    if editable_path:
        cmd += ["-e", str(editable_path)]
    else:
        cmd += list(pkgs)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        warn(f"pip returned non-zero: {result.stderr.strip()[-300:]}")
    return result.returncode == 0


def run_check(cmd: list[str]) -> bool:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def download_file(url: str, dest: Path, label: str) -> bool:
    """Download url → dest with a simple progress indicator."""
    print(f"  Downloading {label} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        def reporthook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, downloaded * 100 // total_size)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                mb = downloaded / 1_048_576
                print(f"\r    [{bar}] {pct:3d}%  {mb:.1f} MB", end="", flush=True)
        urllib.request.urlretrieve(url, dest, reporthook=reporthook)
        print()  # newline after progress bar
        ok(f"Saved to {dest}")
        return True
    except Exception as e:
        print()
        warn(f"Download failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────
# Step 1 — Python version
# ─────────────────────────────────────────────────────────────────────────

hdr(1, "Checking Python version")
if sys.version_info < (3, 10):
    err(f"Python 3.10+ required — you have {sys.version}")
    sys.exit(1)
ok(f"Python {sys.version.split()[0]}")


# ─────────────────────────────────────────────────────────────────────────
# Step 2 — Core install (editable)
# ─────────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).parent.resolve()
PKG_DIR     = SCRIPT_DIR / "pyslick_pkg"

hdr(2, "Installing pyslick core (editable)")
if not PKG_DIR.exists():
    err(f"Expected package directory not found: {PKG_DIR}")
    sys.exit(1)

if pip(editable_path=PKG_DIR):
    ok("pyslick installed (editable)")
else:
    err("Core install failed — cannot continue.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# Step 3 — rapidfuzz (core dep)
# ─────────────────────────────────────────────────────────────────────────

hdr(3, "Installing rapidfuzz")
if pip("rapidfuzz>=3.0"):
    ok("rapidfuzz ready")
else:
    warn("rapidfuzz install may have failed — query/recon will not work.")


# ─────────────────────────────────────────────────────────────────────────
# Step 4 — tree-sitter (optional, for TS/TSX files)
# ─────────────────────────────────────────────────────────────────────────

hdr(4, "Installing tree-sitter (optional — enables graphify-sitter for .ts/.tsx)")
ts_ok = pip("tree-sitter>=0.21", "tree-sitter-typescript>=0.21")
if ts_ok:
    ok("tree-sitter + tree-sitter-typescript ready")
else:
    warn("tree-sitter unavailable — graphify-sitter will be skipped. "
         "Everything else still works.")


# ─────────────────────────────────────────────────────────────────────────
# Step 5 — llama-cpp-python (optional, for local LLM query expansion)
# ─────────────────────────────────────────────────────────────────────────

hdr(5, "Installing llama-cpp-python (optional — enables local LLM query expansion)")
print(f"  {DIM}This compiles a C extension and may take 30–90 seconds…{RST}")
lcpp_ok = pip("llama-cpp-python")
if lcpp_ok:
    ok("llama-cpp-python installed")
else:
    warn("llama-cpp-python unavailable — LLM query expansion disabled. "
         "pyslick still works fine without it.")


# ─────────────────────────────────────────────────────────────────────────
# Step 6 — Download SmolLM2-135M GGUF model (~90 MB)
# ─────────────────────────────────────────────────────────────────────────

hdr(6, "Downloading SmolLM2-135M-Instruct GGUF model (~90 MB)")

MODEL_DIR  = Path.home() / ".pyslick" / "models"
MODEL_FILE = MODEL_DIR / "SmolLM2-135M-Instruct-Q4_K_M.gguf"

# Primary URL — HuggingFace CDN (direct file link)
MODEL_URL = (
    "https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct-GGUF"
    "/resolve/main/smollm2-135m-instruct-q4_k_m.gguf"
)

if not lcpp_ok:
    warn("Skipping model download — llama-cpp-python not available, model would be unused.")
elif MODEL_FILE.exists():
    size_mb = MODEL_FILE.stat().st_size / 1_048_576
    ok(f"Model already present ({size_mb:.0f} MB) — skipping download.")
else:
    downloaded = download_file(MODEL_URL, MODEL_FILE, "SmolLM2-135M-Instruct Q4_K_M")
    if not downloaded:
        warn("Could not auto-download the model.")
        print(f"  {DIM}Manual steps:{RST}")
        print(f"  1. Go to: https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct-GGUF")
        print(f"  2. Download the Q4_K_M .gguf file")
        print(f"  3. Place it in: {MODEL_DIR}")


# ─────────────────────────────────────────────────────────────────────────
# Step 7 — Wire llm-status + agentic AI into __init__.py
# ─────────────────────────────────────────────────────────────────────────

hdr(7, "Wiring llm-status command and agentic AI dispatch")

INIT_PATH = PKG_DIR / "pyslick" / "__init__.py"
init_src = INIT_PATH.read_text(encoding="utf-8")

# Patch 7a: add import_llm() helper if not present
LLM_IMPORT_HELPER = '''
def import_llm():
    from .llm import status_report, is_available, maybe_expand_query
    return status_report, is_available, maybe_expand_query

def import_agent():
    from .agent import main as agent_main
    return agent_main
'''

if "import_llm" not in init_src:
    # Insert before the git operations section
    insert_before = "# ─────────────────────────────────────────────────────────────────────────\n# Git operations"
    init_src = init_src.replace(insert_before, LLM_IMPORT_HELPER + "\n" + insert_before)
    ok("Added import_llm() and import_agent() helpers")
else:
    ok("import_llm() already present")

# Patch 7b: add llm-status and agent dispatch to main()
LLM_STATUS_DISPATCH = '''        elif command == "llm-status":
            try:
                status_report, _, _ = import_llm()
                print(status_report())
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

        elif command == "agent":
            if not args:
                print('Error: agent requires a directive, e.g. pyslick agent "resize the mic button"')
                sys.exit(1)
            try:
                agent_main = import_agent()
                sys.argv = ["agent"] + args
                agent_main()
            except Exception as e:
                print(f"Error: {e}")
                sys.exit(1)

'''

if '"llm-status"' not in init_src:
    # Insert after the "log" command block
    insert_after = '        elif command == "log":\n            git_log()\n'
    init_src = init_src.replace(insert_after, insert_after + LLM_STATUS_DISPATCH)
    ok("Added llm-status and agent commands to dispatch")
else:
    ok("llm-status already wired")

# Patch 7c: update WRITE_COMMANDS to include "agent"
if '"agent"' not in init_src and "agent" not in init_src.split("WRITE_COMMANDS")[1][:50]:
    init_src = init_src.replace(
        'WRITE_COMMANDS = {"patchit", "recon"}',
        'WRITE_COMMANDS = {"patchit", "recon", "agent"}'
    )
    ok("Added 'agent' to WRITE_COMMANDS (triggers git checkpoint)")

# Patch 7d: update help text
AGENT_HELP_ENTRY = """
    agent <directive>
        Full agentic AI session powered by Claude. Gives the AI a
        vocabulary of PowerShell-equivalent tool calls (get-file,
        scan-names, scan-lines, find-blocks, grep, ls) and lets it
        plan, inspect, and propose patches autonomously before asking
        for your confirmation.
        Example: pyslick agent \"make the mic button 40% larger\"
"""

if "agent <directive>" not in init_src:
    init_src = init_src.replace(
        "Guided Agent Command:",
        AGENT_HELP_ENTRY + "\nGuided Agent Command:"
    )
    ok("Updated help text with agent command")

INIT_PATH.write_text(init_src, encoding="utf-8")
ok("__init__.py patched successfully")


# ─────────────────────────────────────────────────────────────────────────
# Step 8 — Smoke test
# ─────────────────────────────────────────────────────────────────────────

hdr(8, "Smoke test — pyslick --help")
result = subprocess.run(
    [sys.executable, "-m", "pyslick", "--help"],
    capture_output=True, text=True,
    cwd=str(PKG_DIR)
)
if result.returncode == 0 or "PySlick" in (result.stdout + result.stderr):
    ok("pyslick responding to --help")
else:
    warn("pyslick --help returned an error — check output above.")
    print(result.stderr[:500])


# ─────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────

print(f"""
{BOLD}{CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}
{BOLD}  Installation complete.{RST}

  Core commands (always available):
    pyslick query  \"<directive>\"   — smart fuzzy code search
    pyslick recon  \"<directive>\"   — guided find → patch pipeline
    pyslick agent  \"<directive>\"   — ✨ NEW agentic AI session
    pyslick ls / lines / grep      — PowerShell-equivalent tools
    pyslick llm-status             — check local LLM state

  Optional features:
    LLM query expansion : {GREEN+"READY" if (MODEL_FILE.exists() and lcpp_ok) else YELL+"manual setup needed"}
{RST}    tree-sitter (TSX)  : {GREEN+"ready" if ts_ok else YELL+"unavailable"}
{RST}
  Run from your project root:
    {BOLD}pyslick agent "make the mic button bigger"{RST}
{DIM}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}
""")
