"""Question decomposition.

Regex-first splitter for common compound-question shapes, T5 fallback only
when regex can't split and the marker count says the input is compound.
"""

from __future__ import annotations

import re

_MODEL_NAME = "thenHung/question_decomposer_t5"
_SEP = "[SEP]"

_MARKERS = (
    "where", "what", "who", "when", "why", "how",
    "find", "show", "locate", "which",
)

_MARKER_ALT = "|".join(_MARKERS)

_SPLIT_ORDER = (
    re.compile(r"\s*;\s*"),
    re.compile(r"\s*\?\s*(?=[A-Za-z])"),
    re.compile(r"\s+and\s+(?=(?:" + _MARKER_ALT + r")\b)", re.IGNORECASE),
    re.compile(r"\s*\n+\s*"),
    re.compile(r"\s+(?=(?:" + _MARKER_ALT + r")\b)", re.IGNORECASE),
)

_model = None
_tokenizer = None
_load_attempted = False


def _load():
    global _model, _tokenizer, _load_attempted
    if _load_attempted:
        return _model, _tokenizer
    _load_attempted = True
    try:
        from transformers import T5ForConditionalGeneration, AutoTokenizer
        _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        _model = T5ForConditionalGeneration.from_pretrained(_MODEL_NAME)
        _model.eval()
    except Exception:
        _model = None
        _tokenizer = None
    return _model, _tokenizer


def _looks_compound(query: str) -> bool:
    ql = query.lower()
    total = 0
    distinct = set()
    for m in _MARKERS:
        n = len(re.findall(rf"\b{re.escape(m)}\b", ql))
        total += n
        if n:
            distinct.add(m)
    if total >= 2 or len(distinct) >= 2:
        return True
    if " and " in ql and ql.count("?") >= 2:
        return True
    return False


def _regex_split(query: str) -> list[str]:
    for pat in _SPLIT_ORDER:
        parts = [p.strip() for p in pat.split(query)]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return parts
    return []


def decompose(query: str) -> list[str]:
    query = (query or "").strip()
    if not query:
        return []
    if not _looks_compound(query):
        return [query]

    parts = _regex_split(query)
    if len(parts) >= 2:
        return parts

    model, tok = _load()
    if not model or not tok:
        return [query]

    try:
        inp = tok(query, return_tensors="pt", truncation=True, max_length=256)
        out = model.generate(**inp, max_new_tokens=128)
        text = tok.decode(out[0], skip_special_tokens=True)
    except Exception:
        return [query]

    parts = [p.strip() for p in text.split(_SEP) if p.strip()]
    if len(parts) < 2:
        return [query]
    return parts