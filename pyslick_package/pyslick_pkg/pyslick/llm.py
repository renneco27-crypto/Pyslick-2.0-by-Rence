#!/usr/bin/env python3
"""
llm.py — optional, tiny local LLM assist for pyslick query.

This is NOT required. Everything in pyslick works without it (fuzzy match +
AST call-graph + design communities). If present, it's used for exactly one
thing: rewording/expanding your directive into extra search terms before
the fuzzy match runs, e.g. "make the mic bigger" -> also try
"microphone button size scale enlarge". That's it — it never writes files,
never sees your whole codebase, and a bad/missing model just means pyslick
falls back to the directive as-is.

Setup (one-time, entirely manual — no auto-download, no account/API key):

    1. pip install llama-cpp-python
    2. Download a small instruct GGUF model, e.g. SmolLM2-135M-Instruct
       (Q4 quant is roughly 90MB — genuinely the honest floor for "small
       LLM that still follows instructions at all"; if you want noticeably
       better rewording at the cost of size, Qwen2.5-0.5B-Instruct-GGUF
       (~350MB, Q4) is a good next step up).
    3. Put the .gguf file in: ~/.pyslick/models/  (any filename, .gguf ext)
    4. Run: pyslick llm-status   — confirms it's wired up correctly.

Runs entirely on CPU, fine on a Ryzen 3 — it's ~135M params and we cap
output at ~30 tokens, so a call takes well under a second.
"""

import os
import glob

MODEL_DIR = os.path.join(os.path.expanduser("~"), ".pyslick", "models")

_llm_singleton = None
_load_attempted = False


def find_model_path() -> str | None:
    if not os.path.isdir(MODEL_DIR):
        return None
    candidates = sorted(glob.glob(os.path.join(MODEL_DIR, "*.gguf")))
    return candidates[0] if candidates else None


def is_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return False
    return find_model_path() is not None


def _load():
    global _llm_singleton, _load_attempted
    if _llm_singleton is not None or _load_attempted:
        return _llm_singleton
    _load_attempted = True

    model_path = find_model_path()
    if not model_path:
        return None
    try:
        from llama_cpp import Llama
        _llm_singleton = Llama(
            model_path=model_path,
            n_ctx=512,
            n_threads=max(1, os.cpu_count() or 2),
            verbose=False,
        )
    except Exception:
        _llm_singleton = None
    return _llm_singleton


def maybe_expand_query(directive: str) -> str:
    """Best-effort query expansion. Always safe to call — on any failure
    (no model, load error, generation error) it just returns `directive`
    unchanged, silently."""
    llm = _load()
    if llm is None:
        return directive

    prompt = (
        "List 6 short keywords related to this software change request. "
        "Comma separated, no explanation, no numbering.\n"
        f"Request: {directive}\nKeywords:"
    )
    try:
        result = llm(
            prompt,
            max_tokens=40,
            temperature=0.2,
            stop=["\n"],
        )
        text = result["choices"][0]["text"].strip()
        if not text:
            return directive
        # keep it bounded — this is an assist, not a rewrite
        extra_terms = [t.strip() for t in text.split(",") if t.strip()][:8]
        if not extra_terms:
            return directive
        return directive + " " + " ".join(extra_terms)
    except Exception:
        return directive


def status_report() -> str:
    lines = []
    try:
        import llama_cpp  # noqa: F401
        lines.append("llama-cpp-python: installed")
    except ImportError:
        lines.append("llama-cpp-python: NOT installed  ->  pip install llama-cpp-python")

    model_path = find_model_path()
    if model_path:
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        lines.append(f"model found: {model_path}  ({size_mb:.0f} MB)")
    else:
        lines.append(f"model: NOT found in {MODEL_DIR}")
        lines.append("  -> download a small *-Instruct GGUF (e.g. SmolLM2-135M-Instruct-Q4,")
        lines.append("     ~90MB) and place the .gguf file in that folder")

    lines.append("")
    lines.append(f"Active: {'YES — pyslick query will use it to expand your directive' if is_available() else 'NO — pyslick query works fine without it, just less fuzzy on odd phrasing'}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(status_report())
