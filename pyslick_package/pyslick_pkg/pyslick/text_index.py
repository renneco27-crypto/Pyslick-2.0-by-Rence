"""BM25 text index over comments, strings, CSS classes, and JSX/HTML attributes.

Sits alongside the symbol graph (graphify-out/graph.json), not inside it.
The graph indexes symbols. This indexes the words around them. Concept
queries like "where is the purple cursor" hit this; symbol queries like
"where is parseCSVLine defined" still hit find-nearest-nodes.

No dependencies. No files written to disk. Index lives in memory.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict

from repomap import collect_files

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORD = re.compile(r"[a-zA-Z0-9]+")

# NLTK stopwords + Snowball stemmer. Loaded lazily so import never fails
# hard if the data files are missing — falls back to a small inline set.
_STOPWORDS: set[str] | None = None
_STEMMER = None
_STEM_FALLBACK = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "has", "have", "how", "in", "is", "it", "its", "me", "my",
    "of", "on", "or", "that", "the", "this", "to", "was", "were", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
})


def _load_nltk():
    global _STOPWORDS, _STEMMER
    if _STOPWORDS is not None:
        return
    try:
        from nltk.corpus import stopwords as _sw
        from nltk.stem import SnowballStemmer
        _STOPWORDS = set(_sw.words("english"))
        _STEMMER = SnowballStemmer("english")
    except Exception:
        _STOPWORDS = set(_STEM_FALLBACK)
        _STEMMER = None


def _tokenize_for_index(text: str) -> list[str]:
    """Lowercase, split camelCase and snake_case, strip stopwords, stem.
    Also preserves original compound token so exact symbol queries match strongly.
    """
    _load_nltk()
    raw_words = _WORD.findall(text)
    out = []
    for raw in raw_words:
        low = raw.lower()
        if len(low) > 2 and low not in _STOPWORDS:
            # Include compound token (e.g. createbrowserclient)
            out.append(low)

        # Also split camelCase and snake_case
        split_text = _CAMEL.sub(r"\1 \2", raw).replace("_", " ").replace("-", " ")
        sub_words = _WORD.findall(split_text)
        if len(sub_words) > 1:
            for sw in sub_words:
                slw = sw.lower()
                if len(slw) > 1 and slw not in _STOPWORDS:
                    out.append(_STEMMER.stem(slw) if _STEMMER else slw)
        else:
            out.append(_STEMMER.stem(low) if _STEMMER else low)
    return out


# ---------------------------------------------------------------------------
# Text extraction per file type
# ---------------------------------------------------------------------------

_LINE_COMMENT = {
    ".py": re.compile(r"#\s*(.+)$", re.MULTILINE),
    ".js": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".jsx": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".ts": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".tsx": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".css": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".scss": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".go": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".rs": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".java": re.compile(r"//\s*(.+)$", re.MULTILINE),
    ".rb": re.compile(r"#\s*(.+)$", re.MULTILINE),
    ".sh": re.compile(r"#\s*(.+)$", re.MULTILINE),
    ".yaml": re.compile(r"#\s*(.+)$", re.MULTILINE),
    ".yml": re.compile(r"#\s*(.+)$", re.MULTILINE),
}

_BLOCK_COMMENT = re.compile(r"/\*+(.*?)\*+/", re.DOTALL)
_PY_DOCSTRING = re.compile(r'("""|\'\'\')(.*?)\1', re.DOTALL)

# Quoted strings (single, double, backtick). Non-greedy, one line max for
# single-line strings; docstrings handled separately above.
_QUOTED = re.compile(r'("([^"\\\n]|\\.)*"|\'([^\'\\\n]|\\.)*\'|`([^`\\\n]|\\.)*`)')

# JSX className="..." / class="..." / id="..."
_JSX_ATTR = re.compile(r'\b(?:className|class|id)\s*=\s*["\']([^"\']+)["\']')

# CSS selectors and custom properties: .cursor-dot, --cursor-color, #cursorId
_CSS_SELECTOR = re.compile(r"(?:^|[{};,\s])([.#][A-Za-z_][\w-]*|--[\w-]+)", re.MULTILINE)


def extract_text_for_index(filepath: str) -> str:
    """Return all searchable text in a file: comments, strings, CSS classes,
    JSX attributes. Never raises; returns "" on any failure.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            src = f.read()
    except Exception:
        return ""

    # Strip data URIs and sourcemaps before extraction to avoid noise
    src = re.sub(r'data:[^;]+;base64,[A-Za-z0-9+/=]+', '', src)
    src = re.sub(r'sourceMappingURL=data:[^\n]+', '', src)

    ext = os.path.splitext(filepath)[1].lower()
    chunks: list[str] = []

    # Comments
    if ext in _LINE_COMMENT:
        for m in _LINE_COMMENT[ext].finditer(src):
            c = m.group(1)
            if len(c) <= 250:
                chunks.append(c)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".go", ".rs", ".java"):
        for m in _BLOCK_COMMENT.finditer(src):
            c = m.group(1)
            if len(c) <= 500:
                chunks.append(c)

    # Python docstrings (including triple-quoted non-docstrings — fine, more text)
    if ext == ".py":
        for m in _PY_DOCSTRING.finditer(src):
            c = m.group(2)
            if len(c) <= 500:
                chunks.append(c)

    # String literals (cap at 150 chars to exclude base64 blobs, SVGs, and bundles)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb"):
        for m in _QUOTED.finditer(src):
            s = m.group(0)
            if 3 <= len(s) <= 150 and not s.startswith(('"data:', "'data:", '`data:')):
                chunks.append(s)

    # JSX / HTML attributes
    if ext in (".jsx", ".tsx", ".html", ".htm", ".vue", ".svelte"):
        for m in _JSX_ATTR.finditer(src):
            a = m.group(1)
            if len(a) <= 150:
                chunks.append(a)

    # CSS selectors and custom properties
    if ext in (".css", ".scss", ".sass", ".less"):
        chunks.extend(m.group(1) for m in _CSS_SELECTOR.finditer(src))

    # Code identifiers (functions, classes, interfaces, exported symbols, imports)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb", ".cs", ".kt", ".c", ".cpp"):
        ident_tokens = set(_WORD.findall(src))
        keywords = {"const", "let", "var", "function", "return", "import", "export",
                    "default", "from", "class", "interface", "type", "async", "await",
                    "public", "private", "protected", "if", "else", "for", "while",
                    "switch", "case", "break", "continue", "try", "catch", "finally",
                    "throw", "new", "this", "super", "null", "undefined", "true", "false", "void"}
        valid_idents = [w for w in ident_tokens if w.lower() not in keywords and 3 <= len(w) <= 60]
        if valid_idents:
            chunks.append(" ".join(valid_idents))

    # Always include the filename tokens — CustomCursor.tsx should be findable
    # by "custom cursor" even if the file body has nothing.
    chunks.append(os.path.basename(filepath))

    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Index build and BM25 search
# ---------------------------------------------------------------------------

_K1 = 1.5
_B = 0.75


def build_text_index(root: str = ".") -> dict:
    """Walk the repo and build an in-memory BM25 index.

    Shape:
      {
        "postings": {token: {filepath: tf}},
        "doc_len":  {filepath: int},
        "avg_len":  float,
        "n_docs":   int,
      }
    """
    files = collect_files(root)
    postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    doc_len: dict[str, int] = {}

    for path in files:
        text = extract_text_for_index(path)
        if not text:
            continue
        toks = _tokenize_for_index(text)
        if not toks:
            continue
        doc_len[path] = len(toks)
        for tok in toks:
            postings[tok][path] += 1

    n_docs = len(doc_len)
    avg_len = (sum(doc_len.values()) / n_docs) if n_docs else 0.0

    return {
        "postings": {t: dict(f) for t, f in postings.items()},
        "doc_len": doc_len,
        "avg_len": avg_len,
        "n_docs": n_docs,
    }

def find_files_by_name(query: str, files: list[str], top_k: int = 5) -> list[tuple[str, float, list[str]]]:
    """Filename-first search. Returns files whose basename matches a
    filename-shaped token in the query. Tight matching: exact basename,
    or prefix with . or _ separator. No loose substring.

    Returns [(filepath, score, matched_tokens)], or [] if no filename tokens.
    """
    import re as _re
    import os as _os

    ext_matches = _re.findall(r"[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,6}\b", query)
    underscore_matches = _re.findall(r"\b[a-z]+_[a-z_]+\b", query)
    candidates = list(dict.fromkeys(ext_matches + underscore_matches))

    # CamelCase fallback: require 6+ chars total to avoid JSON/HTML/HTTP false positives.
    if not candidates:
        camel = _re.findall(r"\b[A-Z][a-zA-Z0-9]{5,}\b", query)
        candidates = list(dict.fromkeys(camel))

    if not candidates:
        return []

    hits: list[tuple[str, float, list[str]]] = []
    seen: set = set()
    for c in candidates:
        c_low = c.lower()
        for f in files:
            base = _os.path.basename(f).lower()
            if (base == c_low
                    or base.startswith(c_low + ".")
                    or base.startswith(c_low + "_")):
                if f in seen:
                    continue
                seen.add(f)
                hits.append((f, 1.0, [c_low]))
                break
    return hits[:top_k]


def _has_non_filename_token(query: str) -> bool:
    """True if query has meaningful tokens beyond a bare filename/identifier.
    Used to decide whether to merge filename match with BM25 results."""
    import re as _re
    tokens = [t.lower() for t in _re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query) if len(t) >= 3]
    if not tokens:
        return False
    stop = {"where", "what", "how", "the", "is", "are", "for", "and", "find",
            "show", "locate", "which", "does", "did", "in", "on", "at"}
    meaningful = [t for t in tokens if t not in stop]
    # A single CamelCase-looking token isn't "non-filename" — it might be the file.
    if len(meaningful) == 1 and meaningful[0][0].isupper():
        return False
    return len(meaningful) >= 2

def search_text_index(query: str, index: dict, top_k: int = 5) -> list[tuple[str, float, list[str]]]:
    """BM25 over the built index. Returns [(filepath, score, matched_tokens)],
    sorted descending by score.
    """
    if not index or not index.get("n_docs"):
        return []

    q_tokens = _tokenize_for_index(query)
    if not q_tokens:
        return []

    n_docs = index["n_docs"]
    avg_len = index["avg_len"] or 1.0
    postings = index["postings"]
    doc_len = index["doc_len"]

    scores: dict[str, float] = defaultdict(float)
    matched: dict[str, set] = defaultdict(set)

    for tok in q_tokens:
        files = postings.get(tok)
        if not files and len(tok) >= 4:
            # Prefix fallback: "auth" should match "authentication",
            # "authorization", "authToken". Merge all matching postings.
            merged: dict[str, int] = {}
            for p_tok, p_files in postings.items():
                if p_tok.startswith(tok):
                    for fp, tf in p_files.items():
                        merged[fp] = merged.get(fp, 0) + tf
            if merged:
                files = merged
        if not files:
            continue
        df = len(files)
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        for filepath, tf in files.items():
            dl = doc_len.get(filepath, avg_len)
            denom = tf + _K1 * (1 - _B + _B * dl / avg_len)
            scores[filepath] += idf * (tf * (_K1 + 1)) / denom
            matched[filepath].add(tok)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [(fp, sc, sorted(matched[fp])) for fp, sc in ranked]


def format_text_results(results: list[tuple[str, float, list[str]]]) -> None:
    """Print results in a shape consistent with find-nearest-nodes output."""
    print("\n--- Text Index Matches (BM25) ---")
    if not results:
        print("  No text matches found.")
        return
    top = results[0][1] or 1.0
    for filepath, score, toks in results:
        pct = (score / top) * 100 if top else 0.0
        shown = ", ".join(toks[:6])
        print(f"  [{pct:5.1f}%]  {filepath}  <- matched: {shown}")
    print()
def format_text_results(results: list[tuple[str, float, list[str]]]) -> None:
    """Print results in a shape consistent with find-nearest-nodes output."""
    print("\n--- Text Index Matches (BM25) ---")
    if not results:
        print("  No text matches found.")
        return
    # Require at least one real token match per result. Stopword-only
    # matches ("is", "the") collapse to zero tokens after tokenization,
    # but belt-and-suspenders in case the fallback stopword set is active.
    results = [r for r in results if r[2]]
    if not results:
        print("  No text matches found.")
        return
    top = results[0][1] or 1.0
    for filepath, score, toks in results:
        pct = (score / top) * 100 if top else 0.0
        shown = ", ".join(toks[:6])
        print(f"  [{pct:5.1f}%]  {filepath}  <- matched: {shown}")
    print()
    # ---------------------------------------------------------------------------
# YAKE auto-expansion
# ---------------------------------------------------------------------------

_YAKE_EXTRACTOR = None


def _load_yake():
    global _YAKE_EXTRACTOR
    if _YAKE_EXTRACTOR is not None:
        return _YAKE_EXTRACTOR
    try:
        import yake
        _YAKE_EXTRACTOR = yake.KeywordExtractor(lan="en", n=2, top=20, dedupLim=0.9)
    except Exception:
        _YAKE_EXTRACTOR = False
    return _YAKE_EXTRACTOR

def find_files_by_name(query: str, files: list[str], top_k: int = 5) -> list[tuple[str, float, list[str]]]:
    """Filename-first search. If the query contains a filename-shaped token
    (activity_main.xml, MainActivity.java, activity_main), return files whose
    basename matches. Runs before BM25 so exact file lookups don't get lost
    in content-token noise.

    Returns [(filepath, score, matched_tokens)], or [] if no filename tokens.
    """
    import re as _re

    # Filename-shaped tokens: have an extension, OR use underscores.
    ext_matches = _re.findall(r"[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,6}\b", query)
    underscore_matches = _re.findall(r"\b[a-z]+_[a-z_]+\b", query)
    candidates = list(dict.fromkeys(ext_matches + underscore_matches))

    # Bare CamelCase names (MainActivity, UrlNavigation) as fallback.
    if not candidates:
        camel = _re.findall(r"\b[A-Z][a-zA-Z0-9]{3,}\b", query)
        candidates = list(dict.fromkeys(camel))

    if not candidates:
        return []

    import os as _os
    hits: list[tuple[str, float, list[str]]] = []
    seen: set = set()
    for c in candidates:
        c_low = c.lower()
        # Avoid common directory/noise words matching random files
        if c_low in ("src", "app", "lib", "components", "pages", "util", "utils", "index", "main"):
            continue
        for f in files:
            base = _os.path.basename(f).lower()
            # Exact match without ext or exact full filename match
            name_without_ext = _os.path.splitext(base)[0]
            if base == c_low or name_without_ext == c_low or base.startswith(c_low + "."):
                if f in seen:
                    continue
                seen.add(f)
                hits.append((f, 1.0, [c_low]))
                break
    return hits[:top_k]


def search_text_index_auto(
    query: str, index: dict, top_k: int = 5, seed_k: int = 5, max_keywords: int = 12
) -> tuple[list[tuple[str, float, list[str]]], list[str]]:
    """BM25 with automatic YAKE expansion when the seed result set is narrow.

    - 0 or 1 seed file  -> nothing to expand from, return as-is
    - 2 to 5 seed files -> run YAKE on them, re-search with combined query
    - 6+ seed files     -> already broad, return as-is

    Returns (results, expanded_keywords_used). Keywords are [] when no
    expansion happened.
    """
    # Filename-first: if the query names a file, return it directly UNLESS
    # the query also has content tokens that should be searched.
    try:
        _files = list(index.get("doc_len", {}).keys())
        _by_name = find_files_by_name(query, _files, top_k=top_k)
    except Exception:
        _by_name = []

    if _by_name and not _has_non_filename_token(query):
        # Pure filename query: return as-is.
        return _by_name, []

    seed = search_text_index(query, index, top_k=seed_k)

    # If we have filename hits AND BM25 hits, merge (filename rank boosted).
    if _by_name:
        # Filename matches always rank first. BM25 results follow in their
        # natural order. Don't compare scores across the two sources —
        # they're on different scales and BM25 raw scores can exceed the
        # filename sentinel of 1.0, producing >100% normalized values.
        seen = {fp for fp, _s, _t in _by_name}
        # Normalize filename scores to sit above any plausible BM25 score
        # so the display's top-hit divisor works cleanly.
        _top_bm25 = seed[0][1] if seed else 1.0
        _boost = max(_top_bm25, 1.0) * 1.1
        merged = [(fp, _boost, tk) for fp, _s, tk in _by_name]
        for fp, sc, tk in seed:
            if fp not in seen:
                merged.append((fp, sc, tk))
        seed = merged[:top_k]
    if len(seed) != 2:
        return seed, []

    extractor = _load_yake()
    if not extractor:
        return seed, []

    chunks = []
    for fp, _sc, _tk in seed:
        txt = extract_text_for_index(fp)
        if txt:
            chunks.append(txt)
    if not chunks:
        return seed, []

    try:
        pairs = extractor.extract_keywords("\n".join(chunks))
    except Exception:
        return seed, []

    q_tokens = set(_tokenize_for_index(query))
    extra: list[str] = []
    seen: set[str] = set()
    for kw, _score in pairs:  # lower score = more important
        kw_norm = kw.strip().lower()
        if not kw_norm or kw_norm in seen:
            continue
        kw_toks = set(_tokenize_for_index(kw_norm))
        if not kw_toks or kw_toks.issubset(q_tokens):
            continue
        seen.add(kw_norm)
        extra.append(kw_norm)
        if len(extra) >= max_keywords:
            break

    if not extra:
        return seed, []

    combined = query + " " + " ".join(extra)
    expanded = search_text_index(combined, index, top_k=top_k)

    by_file: dict[str, tuple[float, list[str]]] = {}
    for fp, sc, tk in seed:
        by_file[fp] = (sc, tk)
    for fp, sc, tk in expanded:
        prev = by_file.get(fp)
        if prev is None or sc > prev[0]:
            by_file[fp] = (sc, tk)

    merged = sorted(
        ((fp, sc, tk) for fp, (sc, tk) in by_file.items()),
        key=lambda r: r[1],
        reverse=True,
    )[:top_k]
    return merged, extra


def format_auto_results(
    results: list[tuple[str, float, list[str]]], keywords: list[str]
) -> None:
    """Same shape as format_text_results, plus a note when YAKE ran."""
    if keywords:
        print("\n--- Text Index Matches (BM25 + YAKE auto-expand) ---")
        print(f"  expanded with: {', '.join(keywords)}")
    else:
        print("\n--- Text Index Matches (BM25) ---")
    if not results:
        print("  No text matches found.")
        return
    results = [r for r in results if r[2]]
    if not results:
        print("  No text matches found.")
        return
    top = results[0][1] or 1.0
    for filepath, score, toks in results:
        pct = (score / top) * 100 if top else 0.0
        shown = ", ".join(toks[:6])
        print(f"  [{pct:5.1f}%]  {filepath}  <- matched: {shown}")
    print()