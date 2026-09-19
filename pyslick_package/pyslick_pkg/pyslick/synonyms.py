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
    "cache": ["caching", "offline", "storage", "prefetch", "persist"],
    "caching": ["cache", "offline", "storage", "prefetch", "persist"],
    "offline": ["cache", "caching", "sync", "sw", "serviceworker", "disconnected"],
    "online": ["network", "connected", "sync", "fetch"],
    "sync": ["synchronize", "queue", "outbox", "offline", "online", "syncengine"],
    "worker": ["serviceworker", "background", "sw", "thread", "serwist"],
    "serviceworker": ["worker", "sw", "cache", "offline", "serwist"],
    "route": ["endpoint", "handler", "api", "url"],
    "session": ["auth", "login", "cookie", "token", "jwt", "user"],
    "download": ["export", "save", "fetch", "apk", "file"],
    "upload": ["import", "parse", "file", "csv"],
    "deck": ["flashcards", "study", "cards", "quiz"],
    "card": ["flashcard", "deck", "study"],
    "quiz": ["flashcards", "test", "multiple-choice", "study"],
    "notification": ["onesignal", "push", "alert", "message"],
    "apk": ["android", "build", "gradle", "app", "mobile"],
    "qr": ["qrcode", "scan", "code", "barcode"],
    "supabase": ["database", "postgres", "sql", "client", "createClient", "createBrowserSupabase", "createServerSupabase", "createAdminSupabase", "rpc", "auth"],
    "postgres": ["supabase", "database", "sql", "table", "schema", "rpc", "migration"],
    "postgresql": ["supabase", "database", "sql", "table", "schema", "rpc", "migration"],
    "rpc": ["procedure", "function", "supabase", "postgres", "rpc"],
    "rls": ["policy", "security", "permission", "row level security", "supabase"],
    "policy": ["rls", "security", "permission", "supabase", "postgres"],
    "migration": ["migrations", "schema", "sql", "table", "supabase", "ddl"],
    "migrations": ["migration", "schema", "sql", "table", "supabase", "ddl"],
    "client": ["browser", "server", "admin", "createClient", "createBrowserClient", "createServerClient", "instance"],
    "admin": ["service_role", "createAdminSupabase", "role", "permissions"],
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