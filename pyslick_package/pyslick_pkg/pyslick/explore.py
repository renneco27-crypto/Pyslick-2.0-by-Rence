"""explore.py — local LLM recon loop.

Read-only. Locked to the repo root. No edits, no writes except the final
findings file. Prompt-encoded tool protocol (see _SYSTEM below):

  <cmd>Get-ChildItem src</cmd>     -> run the command, feed output back
  <done>...your answer...</done>   -> stop, write findings

The model drives; we gate every command against a whitelist and refuse
anything that could mutate the repo or escape it.

Commit 1 scope: load the model and do ONE plain round-trip, no tools.
This is the smoke test that llama-cpp + Qwen actually responds via our
chat wrapper. The tool loop lands in the next commit.
"""

import os
import re
import sys
import time
from pathlib import Path

# ── Model load (reuse llm.py's finder + singleton behaviour) ──────────────
_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".pyslick", "models")


def _find_model() -> str | None:
    import glob
    cands = sorted(glob.glob(os.path.join(_MODEL_DIR, "*.gguf")))
    if not cands:
        return None
    # prefer instruct models if several are present
    instruct = [c for c in cands if "instruct" in os.path.basename(c).lower()]
    return (instruct or cands)[0]


def _load_model():
    path = _find_model()
    if not path:
        return None, None
    try:
        from llama_cpp import Llama
    except ImportError:
        return None, None
    llm = Llama(
        model_path=path,
        n_ctx=2048,           # bigger than llm.py's 512: we feed tool output
        n_threads=max(1, os.cpu_count() or 2),
        verbose=False,
    )
    return llm, path


# ── Prompt scaffolding (commit 1: no tool loop yet, just framing) ────────
_SYSTEM = (
    "You are a code reconnaissance assistant. You answer questions about "
    "a local source repository by running READ-ONLY shell commands and "
    "reading their output.\n\n"
    "To run a command, output it inside <cmd>...</cmd> tags on its own line. "
    "One command per turn. Allowed commands: Get-ChildItem, Get-Content, "
    "Select-String, rg, ls, cat, find, tree. Nothing else.\n\n"
    "When you have enough information, output <done>...</done> with your "
    "final findings inside the tags. Do not summarise before you are done.\n"
)


def _format_chat(llm, system: str, user: str) -> str:
    """Ask llama-cpp to apply the model's own chat template, if it knows
    one. Falls back to a plain prompt if the template is missing."""
    try:
        return llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=256,
            temperature=0.1,
        )["choices"][0]["message"]["content"]
    except Exception:
        # fall back to completion interface with a hand-rolled prompt
        prompt = f"{system}\n\nUser: {user}\nAssistant:"
        return llm(prompt, max_tokens=256, temperature=0.1)["choices"][0]["text"]


def main(question: str) -> int:
    llm, path = _load_model()
    if llm is None:
        print("explore: no model found or llama-cpp-python not installed.")
        print(f"         looked in {_MODEL_DIR}")
        return 1
    print(f"explore: model = {os.path.basename(path)}")
    print(f"explore: question = {question!r}")
    print("─" * 60)

    t0 = time.time()
    reply = _format_chat(llm, _SYSTEM, question)
    dt = time.time() - t0

    print(f"[{dt:.1f}s] model replied:")
    print(reply)
    print("─" * 60)
    print("explore: commit 1 smoke test only — no tool loop wired yet.")
    return 0


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Say hello in one short sentence."
    sys.exit(main(q))