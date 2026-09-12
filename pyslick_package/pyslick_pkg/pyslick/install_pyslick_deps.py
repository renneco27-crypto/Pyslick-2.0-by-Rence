#!/usr/bin/env python3
"""
install_pyslick_deps.py — installs every optional dependency used by
pyslick's no-LLM-required local tooling (upgrade plan steps 1-6).

Every dependency here is OPTIONAL. pyslick works with zero extras
installed -- each feature just falls back to its plain-Python behavior
if its dependency is missing. Run this to get the faster / richer paths:
multi-language repo maps, ast-grep structural matching, semantic search,
ripgrep-backed grep, libcst validation, and filesystem watch mode.

Safe to re-run. Each step is independent -- if one fails (no internet, a
package needs a compiler this machine doesn't have, etc.) the rest still
run. Works the same on Windows, macOS, and Linux.

Usage:
    python install_pyslick_deps.py
    python install_pyslick_deps.py --skip-semantic
    python install_pyslick_deps.py --only ripgrep,libcst,watchdog

Flags:
    --skip-tree-sitter   skip tree-sitter / tree-sitter-language-pack / networkx (step 1)
    --skip-ast-grep      skip ast-grep-cli (step 2)
    --skip-semantic      skip sentence-transformers + model download (step 3, the big one)
    --skip-ripgrep       skip ripgrep (step 4)
    --skip-libcst        skip libcst (step 5)
    --skip-watchdog      skip watchdog (step 6)
    --only NAMES         comma-separated: tree-sitter,ast-grep,semantic,ripgrep,libcst,watchdog
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

# Windows terminals (pre-Win10 1511, and some redirected/piped contexts)
# don't always understand ANSI codes; keep output readable either way.
if platform.system() == "Windows":
    try:
        import colorama  # noqa: F401
        colorama.just_fix_windows_console()
    except ImportError:
        pass


def say(msg: str, color: str = "") -> None:
    print(f"{color}{msg}{RESET}" if color else msg)


def step(msg: str) -> None:
    say(f"\n=== {msg} ===", CYAN)


def ok(msg: str) -> None:
    say(f"  OK: {msg}", GREEN)


def skip(msg: str) -> None:
    say(f"  SKIP: {msg}", DIM)


def fail(msg: str) -> None:
    say(f"  FAILED: {msg}", YELLOW)


def run(cmd: list[str], quiet: bool = False) -> bool:
    """Run a command, return True on success. Never raises -- a failed
    step should not stop the rest of the script."""
    try:
        kwargs = {}
        if quiet:
            kwargs["stdout"] = subprocess.DEVNULL
            kwargs["stderr"] = subprocess.DEVNULL
        res = subprocess.run(cmd, timeout=600, **kwargs)
        return res.returncode == 0
    except (subprocess.TimeoutExpired, OSError, Exception):
        return False


def pip_install(packages: list[str]) -> bool:
    """Try a normal `pip install` first (correct on Windows/macOS and most
    Linux setups). Only if that fails, retry with --break-system-packages,
    which is specifically for Debian/Ubuntu's PEP-668 "externally managed
    environment" guard -- irrelevant on Windows, so this never even fires
    there."""
    base = [sys.executable, "-m", "pip", "install"] + packages
    if run(base):
        return True
    return run(base + ["--break-system-packages"], quiet=True)


def which(name: str) -> str | None:
    return shutil.which(name)


def install_ripgrep() -> None:
    step("ripgrep (agent.py / toolbox.py grep backend)")
    if which("rg"):
        ok("ripgrep already installed")
        return

    system = platform.system()
    if system == "Windows":
        if which("winget"):
            run(["winget", "install", "--id", "BurntSushi.ripgrep.MSVC", "-e",
                 "--accept-source-agreements", "--accept-package-agreements"])
        elif which("choco"):
            run(["choco", "install", "ripgrep", "-y"])
        elif which("scoop"):
            run(["scoop", "install", "ripgrep"])
        else:
            fail("no winget/choco/scoop found on PATH -- install manually: "
                 "https://github.com/BurntSushi/ripgrep#installation")
            return
    elif system == "Darwin":
        if which("brew"):
            run(["brew", "install", "ripgrep"])
        else:
            fail("Homebrew not found -- install manually: "
                 "https://github.com/BurntSushi/ripgrep#installation")
            return
    else:  # Linux
        if which("apt-get"):
            run(["sudo", "apt-get", "install", "-y", "ripgrep"])
        elif which("dnf"):
            run(["sudo", "dnf", "install", "-y", "ripgrep"])
        elif which("pacman"):
            run(["sudo", "pacman", "-S", "--noconfirm", "ripgrep"])
        else:
            fail("no apt-get/dnf/pacman found -- install manually: "
                 "https://github.com/BurntSushi/ripgrep#installation")
            return

    if which("rg"):
        ok("ripgrep installed")
    else:
        fail("install command ran but 'rg' still isn't on PATH -- "
             "you may need to restart your terminal")


def install_semantic() -> None:
    step("sentence-transformers (llm.py semantic search) -- this is the big one")
    if not pip_install(["sentence-transformers"]):
        fail("sentence-transformers -- llm.py's semantic fallback tier "
             "will report unavailable; everything else still works")
        return
    ok("sentence-transformers installed")

    say("  Pre-downloading the embedding model (jinaai/jina-embeddings-v2-base-code)...")
    preload_code = (
        "import os\n"
        "model_name = os.environ.get('PYSLICK_EMBED_MODEL', "
        "'jinaai/jina-embeddings-v2-base-code')\n"
        "try:\n"
        "    from sentence_transformers import SentenceTransformer\n"
        "    SentenceTransformer(model_name, trust_remote_code=True)\n"
        "    print(f'  Model ready: {model_name}')\n"
        "except Exception as e:\n"
        "    print(f'  Could not pre-download model: {e}')\n"
        "    print('  llm.py will download it lazily on first real use instead.')\n"
    )
    run([sys.executable, "-c", preload_code])


def verify() -> None:
    step("Verifying what's actually importable now")
    checks: list[tuple[str, bool]] = []

    def has_module(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            # Catch all exceptions, not just ImportError -- some packages
            # (e.g. sentence_transformers → torchcodec) raise RuntimeError
            # when a native DLL or system library (e.g. FFmpeg) is missing.
            return False

    checks.append(("tree-sitter stack", has_module("tree_sitter")
                   and has_module("tree_sitter_language_pack")
                   and has_module("networkx")))
    checks.append(("libcst", has_module("libcst")))
    checks.append(("watchdog", has_module("watchdog")))
    checks.append(("sentence-transformers", has_module("sentence_transformers")))
    checks.append(("ripgrep (rg binary)", which("rg") is not None))
    checks.append(("ast-grep (ast-grep/sg binary)",
                    which("ast-grep") is not None or which("sg") is not None))

    for name, present in checks:
        mark = f"{GREEN}OK{RESET}" if present else f"{DIM}--{RESET}"
        print(f"  [{mark}] {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-tree-sitter", action="store_true")
    parser.add_argument("--skip-ast-grep", action="store_true")
    parser.add_argument("--skip-semantic", action="store_true")
    parser.add_argument("--skip-ripgrep", action="store_true")
    parser.add_argument("--skip-libcst", action="store_true")
    parser.add_argument("--skip-watchdog", action="store_true")
    parser.add_argument("--only", default="",
                         help="comma-separated subset: tree-sitter,ast-grep,semantic,ripgrep,libcst,watchdog")
    args = parser.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None

    def wanted(name: str, skip_flag: bool) -> bool:
        if only is not None:
            return name in only
        return not skip_flag

    say(f"Using Python {platform.python_version()} ({sys.executable})")

    # Step 1: repomap.py -- multi-language repo map
    if wanted("tree-sitter", args.skip_tree_sitter):
        step("tree-sitter, tree-sitter-language-pack, networkx (repomap.py)")
        if pip_install(["tree-sitter", "tree-sitter-language-pack", "networkx"]):
            ok("tree-sitter stack installed")
        else:
            fail("tree-sitter stack -- repomap.py will report non-.py files "
                 "as filename-only matches")
    else:
        skip("tree-sitter stack")

    # Step 2: patchit.py -- ast-grep structural matching
    if wanted("ast-grep", args.skip_ast_grep):
        step("ast-grep-cli (patchit.py structural matching tier)")
        if pip_install(["ast-grep-cli"]):
            ok("ast-grep-cli installed")
        else:
            fail("ast-grep-cli -- patchit.py will skip the structural-match "
                 "tier and use its text-based tiers only")
    else:
        skip("ast-grep-cli")

    # Step 3: llm.py -- semantic search backend
    if wanted("semantic", args.skip_semantic):
        install_semantic()
    else:
        skip("sentence-transformers + model download")

    # Step 4: ripgrep -- grep backend for agent.py / toolbox.py
    if wanted("ripgrep", args.skip_ripgrep):
        install_ripgrep()
    else:
        skip("ripgrep")

    # Step 5: libcst -- patchit.py validation tier
    if wanted("libcst", args.skip_libcst):
        step("libcst (patchit.py syntax-validation tier)")
        if pip_install(["libcst"]):
            ok("libcst installed")
        else:
            fail("libcst -- patchit.py will validate with ast.parse only "
                 "(still fully functional)")
    else:
        skip("libcst")

    # Step 6: watchdog -- watch.py filesystem watching
    if wanted("watchdog", args.skip_watchdog):
        step("watchdog (watch.py / pyslick watch)")
        if pip_install(["watchdog"]):
            ok("watchdog installed")
        else:
            fail("watchdog -- 'pyslick watch' will print an install hint "
                 "and exit instead of running")
    else:
        skip("watchdog")

    verify()
    say("\nDone. Run 'pyslick llm-status' for a detailed report on the "
        "local-LLM / semantic-search stack specifically.", CYAN)


if __name__ == "__main__":
    main()
