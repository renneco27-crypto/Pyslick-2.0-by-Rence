#!/usr/bin/env python3
"""
query.py — PySlick's "smart agent, no LLM" entry point.

    pyslick query "what do I need to change to make the mic button bigger"

Pipeline (all local, all fast — safe on a slow CPU, no model to load):

  1. Build (or reuse) a lightweight file index of the project — skips
     node_modules/.git/dist/build/.next/__pycache__ so it never scans the
     whole repo, just candidate source files.
  2. Rank candidate files against the directive using rapidfuzz (string
     similarity against filename + relative path tokens). If a graphify
     graph (graphify-out/graph.json) exists, that's used first for a
     richer node-level match; otherwise falls back to the raw file scan.
  3. For every top-ranked *.py file, run graphify's AST call-graph query
     (graphify.query) to pull out only the matched function/class plus
     its direct callers/callees (depth=2 by default) — i.e. the
     "necessary end-to-end" surface, not the whole file.
  4. Print a compact report: file, symbol, line range, one-line doc,
     what it calls, what calls it. Use --full to also dump source.

This is read-only. It never writes, patches, or commits anything —
that's what `pyslick patchit` / `pyslick recon` are for.
"""

import os
import sys
import json
import argparse
from pathlib import Path

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from graphify import query as graphify_query, map_symbols as graphify_map_symbols

try:
    from repomap import query_multilang, rank_top_files, list_definitions, is_supported as _repomap_supports
    _HAS_REPOMAP = True
except ImportError:
    _HAS_REPOMAP = False

try:
    import llm
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False

# ── colours ────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out",
}

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}
GRAPH_PATH = os.path.join("graphify-out", "graph.json")


# ─────────────────────────────────────────────────────────────────────────
# Step 1 — cheap file index (no parsing, just a directory walk)
# ─────────────────────────────────────────────────────────────────────────
def build_file_index(root: str = ".") -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix in CODE_EXTS:
                files.append(os.path.normpath(os.path.join(dirpath, fn)))
    return files


# ─────────────────────────────────────────────────────────────────────────
# Step 2 — rank candidates
# ─────────────────────────────────────────────────────────────────────────
def rank_files_by_name(directive: str, files: list[str], top_n: int = 6) -> list[tuple[str, float]]:
    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio

    # score against "dir/dir/filename" with separators turned to spaces,
    # so tokens like "mic", "button", "size" match path/file words.
    labels = [f.replace(os.sep, " ").replace("_", " ").replace("-", " ") for f in files]
    results = process.extract(directive, labels, scorer=WRatio, limit=top_n)
    return [(files[idx], score) for _label, score, idx in results]


def rank_via_graph(directive: str, top_n: int = 6, root: str = ".", _retried: bool = False):
    """Use graphify-out/graph.json (root + any pnpm/lerna workspace packages)
    for a richer node-level match. Self-heals a missing/thin graph via a
    code-only extract before giving up — see find_nearest_nodes.ensure_graph_freshness.
    """
    try:
        from find_nearest_nodes import (
            load_all_graph_nodes, load_graphify_vocab, expand_query_with_vocab,
            ensure_graph_freshness, LOW_CONFIDENCE_SCORE,
        )
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio

        _, workspace_dirs = ensure_graph_freshness(root, verbose=False)
        nodes = load_all_graph_nodes(root, workspace_dirs)
        if not nodes:
            return None
        vocab = load_graphify_vocab()
        expanded = expand_query_with_vocab(directive, vocab)

        labels = [n["label"] for n in nodes]
        raw = process.extract(expanded, labels, scorer=WRatio, limit=top_n)

        # Thin/low-confidence result: force one uncached re-extract and retry,
        # same self-repair contract as find_nearest_nodes.main(). Only once,
        # to avoid looping if the repo genuinely has little to find.
        top_score = raw[0][1] if raw else 0
        if (not raw or top_score < LOW_CONFIDENCE_SCORE or len(raw) <= 3) and not _retried:
            from find_nearest_nodes import _run_graphify_extract, _discover_workspace_dirs
            _run_graphify_extract(root, quiet=True)
            for wdir in _discover_workspace_dirs(root):
                _run_graphify_extract(wdir, quiet=True)
            return rank_via_graph(directive, top_n=top_n, root=root, _retried=True)

        candidate_files = []
        seen = set()
        for _match, score, idx in raw:
            node = nodes[idx]
            # prefer the node's own recorded source file (works for any
            # language/module layout), falling back to the old id-guessing
            # heuristic only if source_file wasn't captured on this node.
            src = node.get("source_file", "")
            if src and os.path.isfile(src) and src not in seen:
                seen.add(src)
                candidate_files.append((src, score))
                continue
            nid = node["id"]
            parts = nid.split("_")
            if len(parts) >= 2:
                module = parts[1]
                cand = f"{module}.py"
                if os.path.isfile(cand) and cand not in seen:
                    seen.add(cand)
                    candidate_files.append((cand, score))
        return candidate_files or None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────
# Step 3 — AST call-graph query per candidate .py file
# ─────────────────────────────────────────────────────────────────────────
def inspect_file(py_file: str, directive: str, top_k: int, depth: int):
    try:
        return graphify_query(py_file, directive, top_k=top_k, depth=depth, direction="both")
    except FileNotFoundError:
        return []
    except SyntaxError:
        return []
    except Exception:
        return []


def inspect_file_multilang(file_path: str, directive: str, top_k: int):
    """Non-Python AST inspection via repomap.py's tree-sitter tag extraction.
    This is what used to be a dead end — non_py files were only ever listed
    by name, never actually parsed. Returns a list of dicts normalized to
    the same shape print_report expects, or [] if unsupported/unparseable."""
    if not _HAS_REPOMAP or not _repomap_supports(file_path):
        return []
    try:
        results = query_multilang(file_path, directive, top_k=top_k)
    except Exception:
        return []
    normalized = []
    for r in results:
        normalized.append({
            "type": r.kind, "name": r.name, "parent": None,
            "start_line": r.start_line, "end_line": r.end_line,
            "docstring": "", "score": r.score,
            "callees": set(r.calls), "callers": set(),
            "code": r.code, "connections": [],
        })
    return normalized


def semantic_fallback_for_file(file_path: str, directive: str, top_k: int = 3) -> list[dict]:
    """When token/fuzzy matching (graphify.py for .py, repomap.py for
    everything else) comes back completely empty for a file that the
    file-ranking step still considered a strong candidate, try local
    embedding-based semantic search instead — this is what catches
    "resize mic button bigger" matching `handleMicScale()` with zero
    shared words. Returns [] silently if sentence-transformers or the
    embedding model isn't available; this is always a best-effort extra,
    never a required step."""
    if not _HAS_LLM or not llm.is_semantic_available():
        return []

    try:
        if file_path.endswith(".py"):
            all_syms = graphify_map_symbols(file_path)
            texts = [f"{s['name']} {s['signature']} {s.get('docstring', '')}".strip() for s in all_syms]
            meta = all_syms
        elif _HAS_REPOMAP and _repomap_supports(file_path):
            all_syms = list_definitions(file_path)
            texts = [f"{s['name']} {s['code'].splitlines()[0] if s['code'] else ''}".strip() for s in all_syms]
            meta = all_syms
        else:
            return []
    except Exception:
        return []

    if not texts:
        return []

    ranked = llm.semantic_rank(directive, texts, top_k=top_k)

    results = []
    for idx, score in ranked:
        if score <= 0.15:  # near/no similarity — not worth surfacing as a "match"
            continue
        m = meta[idx]
        results.append({
            "type": m.get("type", m.get("kind", "symbol")), "name": m["name"],
            "parent": m.get("parent"), "start_line": m["start_line"], "end_line": m["end_line"],
            "docstring": m.get("docstring", ""),
            "score": round(score * 100, 1),  # display on the same 0-100-ish scale as the other tiers
            "callees": set(m.get("callees", []) or []), "callers": set(),
            "code": m["code"], "connections": [], "semantic": True,
        })
    return results


def _normalize_py_result(r) -> dict:
    """Flatten a graphify.SymbolResult into the same plain-dict shape
    inspect_file_multilang produces, so print_report can handle both
    without caring which engine (Python AST vs tree-sitter) ran."""
    sym = r.symbol
    return {
        "type": sym["type"], "name": sym["name"], "parent": sym.get("parent"),
        "start_line": sym["start_line"], "end_line": sym["end_line"],
        "docstring": sym.get("docstring", ""), "score": r.score,
        "callees": sym.get("callees", set()), "callers": sym.get("callers", set()),
        "code": r.code, "connections": r.connections,
    }


# ─────────────────────────────────────────────────────────────────────────
# Step 4 — report
# ─────────────────────────────────────────────────────────────────────────
def print_report(directive: str, hits: list, full: bool):
    if not hits:
        print(f"{YELL}No matching symbols found for:{RST} \"{directive}\"")
        print(f"{DIM}Tip: run this from your project root, and make sure the relevant "
              f"files end in .py (JS/TS files are located but not AST-parsed yet).{RST}")
        return

    print(f"\n{BOLD}{CYAN}Query:{RST} \"{directive}\"")
    print(f"{DIM}{'─'*70}{RST}")

    for file_path, results in hits:
        if not results:
            continue
        print(f"\n{BOLD}{file_path}{RST}")
        for sym in results:
            parent = f" (in {sym['parent']})" if sym.get("parent") else ""
            tag = f" {CYAN}[semantic]{RST}" if sym.get("semantic") else ""
            print(f"  {GREEN}[{sym['type']}]{RST} {BOLD}{sym['name']}{RST}{parent}{tag}  "
                  f"{DIM}L{sym['start_line']}-{sym['end_line']}  score={sym['score']:.1f}{RST}")
            if sym.get("docstring"):
                print(f"    {DIM}\"{sym['docstring'][:100]}\"{RST}")

            callees = sorted(sym.get("callees", set()))
            callers = sorted(sym.get("callers", set()))
            if callees:
                print(f"    {CYAN}calls   →{RST} {', '.join(callees)}")
            if callers:
                print(f"    {YELL}called by ←{RST} {', '.join(callers)}")

            for c in sym.get("connections", []):
                csym = c.symbol if hasattr(c, "symbol") else c
                print(f"      {DIM}· [{csym['type']}] {csym['name']} "
                      f"L{csym['start_line']}-{csym['end_line']}{RST}")

            if full:
                print(f"{DIM}    ── source ──{RST}")
                for line in sym["code"].splitlines():
                    print(f"    {line}")

    print(f"\n{DIM}{'─'*70}{RST}")
    print(f"{BOLD}Next step:{RST} pyslick patchit <file> -f   (or -r / -i)  to edit one of the spots above.")
    print(f"{DIM}Or run 'pyslick recon \"{directive}\"' for the guided find→patch→checkpoint flow.{RST}")


# ─────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────
def run(directive: str, root: str = ".", top_files: int = 3, top_symbols: int = 3,
        depth: int = 2, full: bool = False):
    graph_ranked = rank_via_graph(directive, top_n=top_files * 2, root=root)

    if graph_ranked:
        candidates = graph_ranked
    else:
        all_files = build_file_index(root)
        if not all_files:
            print(f"{RED}No source files found under '{root}'.{RST}")
            return
        candidates = rank_files_by_name(directive, all_files, top_n=top_files * 3)

    # keep only .py for AST inspection, top N
    py_candidates = [f for f, _score in candidates if f.endswith(".py")][:top_files]
    non_py = [f for f, _score in candidates if not f.endswith(".py")][:top_files]

    hits = []
    for py_file in py_candidates:
        results = inspect_file(py_file, directive, top_k=top_symbols, depth=depth)
        normalized = [_normalize_py_result(r) for r in results] or semantic_fallback_for_file(py_file, directive, top_k=top_symbols)
        hits.append((py_file, normalized))

    # Non-Python matches used to be listed by filename only ("not AST-parsed
    # yet"). repomap.py's tree-sitter path closes that gap.
    still_unparsed = []
    for f in non_py:
        ml_results = inspect_file_multilang(f, directive, top_k=top_symbols)
        if not ml_results:
            ml_results = semantic_fallback_for_file(f, directive, top_k=top_symbols)
        if ml_results:
            hits.append((f, ml_results))
        else:
            still_unparsed.append(f)

    print_report(directive, hits, full)

    if still_unparsed:
        print(f"\n{DIM}Matched by filename only (language not supported by repomap):{RST}")
        for f in still_unparsed:
            print(f"  {DIM}· {f}{RST}")


def print_top_connected(root: str, n: int):
    if not _HAS_REPOMAP:
        print(f"{RED}repomap.py not available — install tree-sitter-language-pack and networkx.{RST}")
        return
    ranked = rank_top_files(root, top_n=n)
    if not ranked:
        print(f"{YELL}No files could be parsed under '{root}'.{RST}")
        return
    print(f"\n{BOLD}{CYAN}Most connected files{RST} {DIM}(PageRank over call/reference graph){RST}")
    print(f"{DIM}{'─'*70}{RST}")
    for f, score in ranked:
        print(f"  {GREEN}{score:.4f}{RST}  {f}")


def main():
    parser = argparse.ArgumentParser(prog="pyslick query", add_help=True)
    parser.add_argument("directive", nargs="*", help="natural language description of what you want to change")
    parser.add_argument("--root", default=".", help="project root to scan (default: cwd)")
    parser.add_argument("--top-files", type=int, default=3, help="max candidate files to inspect (default 3)")
    parser.add_argument("--top-symbols", type=int, default=3, help="max matched symbols per file (default 3)")
    parser.add_argument("--depth", type=int, default=2, help="call-graph depth to follow (default 2)")
    parser.add_argument("--full", action="store_true", help="also print full matched source code")
    parser.add_argument("--connected", action="store_true",
                         help="skip directive matching — just show the N most connected files "
                              "in the project (PageRank over the repo-wide call graph)")
    args = parser.parse_args()

    if args.connected:
        print_top_connected(args.root, n=args.top_files)
        return

    if not args.directive:
        parser.error("directive is required unless --connected is given")

    directive = " ".join(args.directive)
    run(directive, root=args.root, top_files=args.top_files,
        top_symbols=args.top_symbols, depth=args.depth, full=args.full)


if __name__ == "__main__":
    main()
