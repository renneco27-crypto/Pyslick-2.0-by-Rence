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

    Deliberately separate from repomap._tokenize (which is regex [a-z]+ and
    is load-bearing for the existing symbol search). Changing that one would
    risk the symbol path; this one is free to be better.
    """
    _load_nltk()
    text = _CAMEL.sub(r"\1 \2", text)
    text = text.replace("_", " ").replace("-", " ")
    out = []
    for w in _WORD.findall(text):
        lw = w.lower()
        if len(lw) <= 1 or lw in _STOPWORDS:
            continue
        out.append(_STEMMER.stem(lw) if _STEMMER else lw)
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

    ext = os.path.splitext(filepath)[1].lower()
    chunks: list[str] = []

    # Comments
    if ext in _LINE_COMMENT:
        chunks.extend(m.group(1) for m in _LINE_COMMENT[ext].finditer(src))
    if ext in (".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".go", ".rs", ".java"):
        chunks.extend(m.group(1) for m in _BLOCK_COMMENT.finditer(src))

    # Python docstrings (including triple-quoted non-docstrings — fine, more text)
    if ext == ".py":
        chunks.extend(m.group(2) for m in _PY_DOCSTRING.finditer(src))

    # String literals
    if ext in (".js", ".jsx", ".ts", ".tsx", ".py", ".go", ".rs", ".java", ".rb"):
        for m in _QUOTED.finditer(src):
            chunks.append(m.group(0))

    # JSX / HTML attributes
    if ext in (".jsx", ".tsx", ".html", ".htm", ".vue", ".svelte"):
        chunks.extend(m.group(1) for m in _JSX_ATTR.finditer(src))

    # CSS selectors and custom properties
    if ext in (".css", ".scss", ".sass", ".less"):
        chunks.extend(m.group(1) for m in _CSS_SELECTOR.finditer(src))

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