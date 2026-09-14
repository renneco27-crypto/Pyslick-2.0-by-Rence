#!/usr/bin/env python3
"""
synonyms.py - Query expansion for pyslick recon.

Provides expand(directive) -> {"terms": [...]}.
Static map + LLM fallback + on-disk cache. Never raises.
"""

import os
import json
import hashlib

THIS_DIR = os.path.dirname(os.path.abspath(__file__))

CACHE_PATH = os.path.join(".pyslick", "synonyms_cache.json")

STATIC_MAP = {
    "mic": ["microphone", "audio", "record", "voice"],
    "microphone": ["mic", "audio", "record", "voice"],
    "audio": ["mic", "microphone", "sound", "record"],
    "voice": ["mic", "microphone", "audio", "speech"],
    "record": ["recording", "capture", "audio", "mic"],
    "button": ["btn", "control", "widget", "ui"],
    "ui": ["interface", "widget", "layout", "button"],
    "config": ["configuration", "settings", "options"],
    "settings": ["config", "configuration", "options", "prefs"],
    "auth": ["login", "authentication", "signin", "session"],
    "login": ["auth", "signin", "authentication"],
    "db": ["database", "sql", "query", "storage"],
    "database": ["db", "sql", "storage", "query"],
    "api": ["endpoint", "route", "handler", "request"],
    "endpoint": ["api", "route", "handler"],
    "error": ["exception", "fail", "failure", "bug"],
    "bug": ["error", "exception", "fix", "issue"],
    "test": ["spec", "unittest", "pytest", "assert"],
    "build": ["compile", "bundle", "make", "package"],
    "deploy": ["release", "publish", "ship", "push"],
    "recon": ["scan", "search", "index", "map", "overview"],
    "scan": ["recon", "search", "index", "sweep"],
    "search": ["find", "query", "lookup", "grep"],
    "find": ["search", "locate", "grep", "query"],
    "file": ["path", "module", "source"],
    "function": ["def", "method", "func", "routine"],
    "class": ["type", "object", "struct"],
    "git": ["commit", "branch", "stash", "repo"],
    "stash": ["snap", "checkpoint", "save"],
    "snap": ["stash", "checkpoint", "save"],
    "graph": ["node", "edge", "centrality", "topology"],
    "keyword": ["term", "token", "symbol"],
    "comment": ["docstring", "note", "annotation"],
}


def _cache_key(directive):
    return hashlib.sha1(directive.strip().lower().encode("utf-8")).hexdigest()


def _load_cache():
    if not os.path.isfile(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
    except Exception:
        pass


def _static_terms(directive):
    words = directive.lower().replace(",", " ").split()
    out = []
    seen = set()
    for w in words:
        w = w.strip(".,;:!?()[]{}\"'")
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
        for syn in STATIC_MAP.get(w, []):
            if syn not in seen:
                seen.add(syn)
                out.append(syn)
    return out


def _llm_terms(directive, max_terms=8):
    try:
        import llm as local_llm
    except Exception:
        return []
    fn = getattr(local_llm, "maybe_expand_query", None)
    if not callable(fn):
        return []
    try:
        raw = fn(directive)
    except Exception:
        return []
    if not raw or raw == directive:
        return []
    terms = [t.strip() for t in str(raw).replace("\n", " ").replace(",", " ").split()]
    return [t for t in terms if t][:max_terms]


def expand(directive):
    if not directive or not isinstance(directive, str):
        return {"terms": []}

    key = _cache_key(directive)
    cache = _load_cache()
    if key in cache:
        return {"terms": cache[key]}

    terms = _static_terms(directive)

    if len(terms) <= len(directive.split()):
        extra = _llm_terms(directive)
        for t in extra:
            if t not in terms:
                terms.append(t)

    cache[key] = terms
    _save_cache(cache)
    return {"terms": terms}