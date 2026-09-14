#!/usr/bin/env python3
"""
llm.py â€” optional, tiny local LLM assist for pyslick query.

This is NOT required. Everything in pyslick works without it (fuzzy match +
AST call-graph + design communities). If present, it's used for exactly one
thing: rewording/expanding your directive into extra search terms before
the fuzzy match runs, e.g. "make the mic bigger" -> also try
"microphone button size scale enlarge". That's it â€” it never writes files,
never sees your whole codebase, and a bad/missing model just means pyslick
falls back to the directive as-is.

Setup (one-time, entirely manual â€” no auto-download, no account/API key):

    1. pip install llama-cpp-python
    2. Download a small instruct GGUF model, e.g. SmolLM2-135M-Instruct
       (Q4 quant is roughly 90MB â€” genuinely the honest floor for "small
       LLM that still follows instructions at all"; if you want noticeably
       better rewording at the cost of size, Qwen2.5-0.5B-Instruct-GGUF
       (~350MB, Q4) is a good next step up).
    3. Put the .gguf file in: ~/.pyslick/models/  (any filename, .gguf ext)
    4. Run: pyslick llm-status   â€” confirms it's wired up correctly.

Runs entirely on CPU, fine on a Ryzen 3 â€” it's ~135M params and we cap
output at ~30 tokens, so a call takes well under a second.
"""

import os
import glob

MODEL_DIR = os.path.join(os.path.expanduser("~"), ".pyslick", "models")

_llm_singleton = None
_load_attempted = False

# â”€â”€ Semantic (embedding) search config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Independent of the llama_cpp causal-model path above. Overridable via env
# var so users can trade the default's quality for size, e.g.:
#   export PYSLICK_EMBED_MODEL="codesage/codesage-small-v2"
DEFAULT_EMBED_MODEL = "jinaai/jina-embeddings-v2-base-code"
_embedder_singleton = None
_embedder_load_attempted = False
_embed_cache: dict[str, "object"] = {}  # text -> embedding tensor, session-lifetime only


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
    """Best-effort query expansion. Always safe to call â€” on any failure
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
        # keep it bounded â€” this is an assist, not a rewrite
        extra_terms = [t.strip() for t in text.split(",") if t.strip()][:8]
        if not extra_terms:
            return directive
        return directive + " " + " ".join(extra_terms)
    except Exception:
        return directive


def maybe_extract_find_replace(directive: str, code_context: str) -> tuple[str | None, str | None]:
    """Use local LLM to extract find/replace blocks from code context.
    Returns (find_string, replace_string) or (None, None) on failure.
    The LLM is given the directive and code, asked to identify the exact
    substring to find and what to replace it with. Falls back to manual
    input if the model isn't available or fails."""
    llm = _load()
    if llm is None:
        return None, None

    # Truncate code context to fit within the 512-token context window
    # Rough estimate: 1 token â‰ˆ 4 characters, so ~2000 chars max for code
    max_code_len = 1500
    if len(code_context) > max_code_len:
        code_context = code_context[:max_code_len] + "\n... (truncated)"

    prompt = f"""You are a code patching assistant. Given a change directive and code context, identify the exact string to find and what to replace it with.

Directive: {directive}

Code context:
{code_context}

Output format (exact, no extra text):
FIND: <exact substring to find>
REPLACE: <exact replacement string>

Rules:
- FIND must be an exact substring from the code
- REPLACE should be the modified version
- Preserve indentation and structure exactly
- If unsure, output FIND: <none> and REPLACE: <none>"""

    try:
        result = llm(
            prompt,
            max_tokens=200,
            temperature=0.1,
            stop=["\n\n"],
        )
        text = result["choices"][0]["text"].strip()

        # Parse the structured output
        find_str = None
        replace_str = None

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("FIND:"):
                find_str = line[5:].strip()
                if find_str == "<none>":
                    find_str = None
            elif line.startswith("REPLACE:"):
                replace_str = line[8:].strip()
                if replace_str == "<none>":
                    replace_str = None

        if find_str and replace_str:
            return find_str, replace_str
        return None, None
    except Exception:
        return None, None


def maybe_classify_intent(directive: str, intent_examples: dict) -> tuple | None:
    """
    Ask the local LLM to pick the best intent for a directive.
    Called only when Python fuzzy score is in the uncertain 55-84% zone.

    intent_examples: {intent_name: [phrase1, phrase2, ...], ...}
      (pass only the top candidates, not the whole vocab, to fit in context)

    Returns (intent_name, confidence_int) or None on any failure.
    The LLM replies with exactly: INTENT: <name> CONFIDENCE: <0-100>
    """
    llm = _load()
    if llm is None:
        return None

    # Build a compact examples block â€” one line per intent, first 3 phrases only
    examples_text = "\n".join(
        f"  {name}: {', '.join(phrases[:3])}"
        for name, phrases in intent_examples.items()
    )

    prompt = (
        f"Choose the best intent category for this user query.\n"
        f"Query: {directive}\n\n"
        f"Intent categories and example phrases:\n{examples_text}\n\n"
        f"Reply with exactly: INTENT: <name> CONFIDENCE: <0-100>\n"
        f"Only output that one line."
    )

    try:
        result = llm(
            prompt,
            max_tokens=20,
            temperature=0.0,   # deterministic â€” this is classification not generation
            stop=["\n"],
        )
        text = result["choices"][0]["text"].strip()
        # Parse: INTENT: connect CONFIDENCE: 87
        import re as _re
        m = _re.search(r"INTENT:\s*(\w+)\s+CONFIDENCE:\s*(\d+)", text, _re.IGNORECASE)
        if m:
            intent_name = m.group(1).lower().strip()
            confidence = min(100, max(0, int(m.group(2))))
            # Validate the intent name is one we know
            if intent_name in intent_examples:
                return intent_name, confidence
    except Exception:
        pass

    return None


def verify_intent_yes_no(directive: str, intent_name: str, examples: list[str]) -> bool:
    """
    Binary semantic verification: checks if `directive` matches `intent_name`
    based on the provided example phrases. Returns True if LLM answers YES, False otherwise.
    """
    llm = _load()
    if llm is None:
        return False

    examples_str = ", ".join(f'"{ex}"' for ex in examples[:4])
    prompt = (
        f"Question: Does the user request \"{directive}\" express the same intention as these examples: {examples_str}?\n"
        f"Answer with ONLY 'YES' or 'NO':\n"
    )

    try:
        result = llm(
            prompt,
            max_tokens=5,
            temperature=0.0,
            stop=["\n", ".", ","],
        )
        text = result["choices"][0]["text"].strip().upper()
        return "YES" in text
    except Exception:
        return False



_semantic_available_cache = None  # memoized across calls in one process


def is_semantic_available(timeout_seconds: float = 5.0) -> bool:
    """Whether sentence-transformers (and its torch dependency) actually
    imports cleanly and quickly. Independent of the llama_cpp/GGUF path â€”
    no model needs to be manually placed anywhere; the embedding model
    downloads once (from Hugging Face) and caches itself the first time
    it's used.

    Runs the import in a worker thread with a timeout, because on some
    machines a broken/slow torch install doesn't raise ImportError â€” it
    just hangs partway through import (seen in practice: get_data() on a
    corrupted/incomplete torch install stalls indefinitely). Previously
    this function only guarded against a clean ImportError, so that kind
    of hang propagated all the way up through query.py's semantic
    fallback with a raw traceback the user had to Ctrl+C out of. Now any
    failure mode â€” ImportError, timeout, or any other exception â€” is
    treated the same way: semantic search is unavailable, fall back
    silently to plain fuzzy/AST matching.
    """
    global _semantic_available_cache
    if _semantic_available_cache is not None:
        return _semantic_available_cache

    if os.environ.get("PYSLICK_SKIP_SEMANTIC") == "1":
        _semantic_available_cache = False
        return False

    import concurrent.futures

    def _try_import():
        import sentence_transformers  # noqa: F401
        return True

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_try_import)
            result = future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        print(
            f"  [pyslick] semantic search skipped: sentence-transformers/torch "
            f"import took longer than {timeout_seconds:.0f}s (likely a slow or "
            f"broken torch install) â€” continuing with fuzzy/AST matching only."
        )
        result = False
    except Exception:
        result = False

    _semantic_available_cache = result
    return result


def _embed_model_name() -> str:
    return os.environ.get("PYSLICK_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def _load_embedder():
    global _embedder_singleton, _embedder_load_attempted
    if _embedder_singleton is not None or _embedder_load_attempted:
        return _embedder_singleton
    _embedder_load_attempted = True
    try:
        from sentence_transformers import SentenceTransformer
        _embedder_singleton = SentenceTransformer(_embed_model_name(), trust_remote_code=True)
    except Exception:
        _embedder_singleton = None
    return _embedder_singleton


def semantic_rank(directive: str, candidates: list[str], top_k: int = 5) -> list[tuple[int, float]]:
    """Rank candidate strings (symbol name + signature + docstring, etc.)
    against a natural-language directive using local code-embedding cosine
    similarity â€” no LLM call, fully offline once the model is cached.

    This is what lets "resize mic button bigger" match a function like
    `handleMicScale()` with zero shared words: token/fuzzy matching (in
    graphify.py and repomap.py) requires literal or near-literal overlap,
    but embeddings capture meaning instead of spelling.

    Returns [(index_into_candidates, similarity_score), ...] sorted
    descending, or [] if sentence-transformers / the model isn't available,
    or candidates is empty. Never raises.
    """
    if not candidates:
        return []
    model = _load_embedder()
    if model is None:
        return []
    try:
        from sentence_transformers import util
        query_emb = model.encode(directive, convert_to_tensor=True)
        cand_emb = model.encode(candidates, convert_to_tensor=True)
        sims = util.cos_sim(query_emb, cand_emb)[0]
        scored = sorted(enumerate(sims.tolist()), key=lambda x: x[1], reverse=True)
        return scored[:top_k]
    except Exception:
        return []


def _semantic_status_lines() -> str:
    lines = []
    if not is_semantic_available():
        lines.append("sentence-transformers: NOT installed  ->  pip install sentence-transformers")
        return "\n".join(lines)
    lines.append("sentence-transformers: installed")
    lines.append(f"embedding model: {_embed_model_name()}")
    lines.append("  (downloads + caches automatically on first semantic query â€” needs")
    lines.append("   internet access to huggingface.co the very first time only)")
    return "\n".join(lines)


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
    lines.append(f"Active: {'YES â€” pyslick query will use it to expand your directive' if is_available() else 'NO â€” pyslick query works fine without it, just less fuzzy on odd phrasing'}")

    lines.append("")
    lines.append("â”€â”€ Semantic search (sentence-transformers, no LLM call) â”€â”€")
    lines.append(_semantic_status_lines())
    return "\n".join(lines)


if __name__ == "__main__":
    print(status_report())
