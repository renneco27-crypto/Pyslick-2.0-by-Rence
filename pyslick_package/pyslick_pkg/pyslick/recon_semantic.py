#!/usr/bin/env python3
"""
recon_semantic.py ΓÇö Universal semantic recon for pyslick.

Tier 1: semantic_rank (embeddings) or rapidfuzz over the index.
Tier 2: ensure graphify-out/graph.json exists, expand via graph edges.

Also provides run_full_recon() ΓÇö comprehensive read-only recon that
adds centrality, per-file symbols with line ranges, comment blocks,
LLM-derived keyword terms, git state, git log, git stash, and an
auto-created stash snap (checkpoint) if the tree is dirty.
"""

import os
import re
import sys
import json
import time
import subprocess
from collections import Counter
from datetime import datetime

# Minimum rapidfuzz partial_ratio score for a candidate to be kept.
# partial_ratio finds the best matching *substring*, so a generic English
# question ("what does this codebase do") scores >0 against nearly every
# docstring in the repo. Without a floor, every unit ties and the top-K
# is arbitrary. 60 keeps genuine partial matches, drops incidental ones.
_MIN_FUZZ_SCORE = 60

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import llm as local_llm
from comment_blocks import (
    scan_project_for_comment_blocks,
    comment_nodes_as_graph_nodes,
)

try:
    from find_nearest_nodes import load_graph_nodes
except ImportError:
    def load_graph_nodes():
        return []

try:
    from graphify import query as graphify_query, map_symbols as graphify_map_symbols
    _HAS_GRAPHIFY = True
except ImportError:
    _HAS_GRAPHIFY = False

try:
    from synonyms import expand as _syn_expand
    _HAS_SYNONYMS = True
except ImportError:
    _HAS_SYNONYMS = False


INDEX_DIR = ".pyslick"
INDEX_PATH = os.path.join(INDEX_DIR, "index.json")
GRAPH_PATH = os.path.join("graphify-out", "graph.json")
CHECKPOINT_REGISTRY = os.path.join(INDEX_DIR, "checkpoints.json")

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", "graphify-out",
    ".pyslick", ".pyslick_context", ".pyslick_backups",
}
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx"}

TIER1_MIN_HITS = 3
_graph_ensured_this_process = False

DEFAULT_BUDGET = {
    "whole_file_max_lines": 300,
    "snippet_context_lines": 8,
    "max_files": 6,
    "max_total_lines": 1200,
    "max_snippets_per_file": 5,
}


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# Index building
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

def _walk_code_files(root: str = ".") -> list:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in CODE_EXTS:
                out.append(os.path.join(base, f))
    return out


def _index_comments_and_files() -> tuple:
    units = []
    rollups = {}
    try:
        raw = scan_project_for_comment_blocks(os.getcwd()) or []
    except Exception:
        raw = []

    for node in raw:
        fpath = getattr(node, "file", None) or getattr(node, "path", "")
        start = getattr(node, "start_line", 0)
        text  = getattr(node, "comment_text", "") or getattr(node, "text", "")
        kind  = getattr(node, "type", "comment")
        if not fpath:
            continue
        rel = os.path.relpath(fpath, os.getcwd())
        units.append({"file": rel, "line": start, "text": text.strip(), "kind": kind})
        rollups.setdefault(rel, {"opening_comments": []})
        if len(rollups[rel]["opening_comments"]) < 3 and text.strip():
            rollups[rel]["opening_comments"].append(text.strip())
    return units, rollups


def _index_functions() -> list:
    units = []
    if not _HAS_GRAPHIFY:
        return units
    for path in _walk_code_files(os.getcwd()):
        if not path.endswith(".py"):
            continue
        try:
            syms = graphify_map_symbols(path) or []
        except Exception:
            continue
        rel = os.path.relpath(path, os.getcwd())
        for s in syms:
            units.append({
                "name": s.get("name", ""),
                "file": rel,
                "line": s.get("start_line", 0),
                "end": s.get("end_line", 0),
                "signature": s.get("signature", ""),
                "docstring": s.get("docstring", ""),
                "type": s.get("type", "function"),
            })
    return units


def snap_range_to_function(
    lo: int, hi: int, filepath: str, functions: list
) -> tuple[int, int]:
    """Given a 1-based line range (lo, hi) inside filepath, expand it to
    the enclosing function's full extent if the range falls inside (or
    overlaps) one. This is what stops a mid-function grep hit from
    showing the AI a truncated body — the seed line is grep's job, the
    extent is the AST's job.

    `functions` is index["functions"]: dicts with keys name, file,
    line (start), end. Matching is on basename to tolerate relative-vs-
    absolute path drift. Returns (lo, hi) unchanged if no function
    contains the range.
    """
    if not functions:
        return (lo, hi)

    base = os.path.basename(filepath)
    best = None
    for f in functions:
        ff = f.get("file") or ""
        if os.path.basename(ff) != base:
            continue
        f_start = f.get("line") or 0
        f_end = f.get("end") or 0
        if f_start <= 0 or f_end < f_start:
            continue
        # overlap test: does the snippet range intersect the function span?
        if hi < f_start or lo > f_end:
            continue
        # prefer the tightest enclosing function (smallest span)
        span = f_end - f_start
        if best is None or span < best[2]:
            best = (f_start, f_end, span)

    if best is None:
        return (lo, hi)
    return (min(lo, best[0]), max(hi, best[1]))


def build_index(root: str = ".") -> dict:
    comments, rollups = _index_comments_and_files()
    functions = _index_functions()
    files = sorted({c["file"] for c in comments} | {f["file"] for f in functions})
    return {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "root": os.path.abspath(root),
        "files": files,
        "comments": comments,
        "functions": functions,
        "rollups": rollups,
    }


def _write_index(index: dict) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)


def _index_is_stale() -> bool:
    try:
        idx_mtime = os.path.getmtime(INDEX_PATH)
    except OSError:
        return True
    for dirpath, dirnames, filenames in os.walk(os.getcwd()):
        dirnames[:] = [d for d in dirnames
                       if d not in {"node_modules", ".git", ".next", "dist",
                                    "build", "__pycache__", ".venv", "venv",
                                    ".pyslick", ".pyslick_context", ".pyslick_backups",
                                    "graphify-out", "models", "miamico"}]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            try:
                if os.path.getmtime(os.path.join(dirpath, fn)) > idx_mtime:
                    return True
            except OSError:
                continue
    return False


def load_or_build_index(root: str = ".", rebuild: bool = False) -> dict:
    if not rebuild and os.path.isfile(INDEX_PATH) and not _index_is_stale():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as fh:
                idx = json.load(fh)
            if idx.get("files"):
                return idx
        except Exception:
            pass
    idx = build_index(root)
    try:
        _write_index(idx)
    except Exception:
        pass
    return idx


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# Ranking
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

def _rapidfuzz_rank(directive: str, candidates: list, top_k: int = 10) -> list:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return []
    scored = []
    for c in candidates:
        text = c.get("text") or c.get("name") or c.get("file") or ""
        if not text:
            continue
        s = fuzz.partial_ratio(directive.lower(), text.lower())
        if s >= _MIN_FUZZ_SCORE:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(c, s) for s, c in scored[:top_k]]


def _rank_units(directive: str, units: list, top_k: int = 10) -> list:
    ranked = _rapidfuzz_rank(directive, units, top_k=top_k)
    out = []
    for u, s in ranked:
        h = dict(u)
        h["score"] = s
        out.append(h)
    return out


def _tier1(directive: str, expanded: str, index: dict) -> list:
    hits = []

    fn_units = [
        {
            "file": f["file"],
            "line": f["line"],
            "name": f["name"],
            "text": (f.get("signature") or "") + " " + (f.get("docstring") or ""),
            "kind": "function",
        }
        for f in index.get("functions", [])
    ]
    hits += _rank_units(expanded or directive, fn_units, top_k=15)

    cm_units = [
        {
            "file": c["file"],
            "line": c["line"],
            "text": c.get("text", ""),
            "kind": c.get("kind", "comment"),
        }
        for c in index.get("comments", [])
    ]
    hits += _rank_units(expanded or directive, cm_units, top_k=15)

    toks = [t for t in re.split(r"[^a-zA-Z0-9_]+", (expanded or directive).lower()) if len(t) > 2]
    for _f in index.get("files", []):
        f = _f.get("file") or _f.get("path") or _f.get("name") if isinstance(_f, dict) else _f
        if not f:
            continue
        stem = os.path.splitext(os.path.basename(f))[0].lower()
        for tok in toks:
            if tok == stem or tok == stem.replace("_", ""):
                hits.append({"file": f, "line": 1, "text": f, "kind": "file", "score": 90})
                break
    seen = set()
    deduped = []
    for h in hits:
        k = (h.get("file"), h.get("line"), h.get("name"))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(h)
    return deduped


def _ensure_graph_once() -> bool:
    global _graph_ensured_this_process
    if _graph_ensured_this_process:
        return os.path.isfile(GRAPH_PATH)
    _graph_ensured_this_process = True
    if os.path.isfile(GRAPH_PATH):
        return True
    return False


def _expand_from_graph(directive: str, existing_hits: list, index: dict) -> list:
    if not os.path.isfile(GRAPH_PATH):
        return existing_hits
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8", errors="replace") as fh:
            g = json.load(fh)
    except Exception:
        return existing_hits

    seed_files = {h.get("file") for h in existing_hits if h.get("file")}
    nodes = g.get("nodes") or []
    edges = g.get("edges") or g.get("links") or []

    seed_ids = set()
    for n in nodes:
        nid = n.get("id") or n.get("name") or n.get("label")
        nfile = n.get("source_file") or n.get("file") or n.get("path") or ""
        if nid and nfile and nfile in seed_files:
            seed_ids.add(nid)

    neighbor_ids = set()
    for e in edges:
        s = e.get("source") or e.get("from") or e.get("src")
        t = e.get("target") or e.get("to") or e.get("dst")
        if s in seed_ids and t:
            neighbor_ids.add(t)
        if t in seed_ids and s:
            neighbor_ids.add(s)

    id2n = {}
    for n in nodes:
        nid = n.get("id") or n.get("name") or n.get("label")
        if nid:
            id2n[nid] = n

    extra_hits = []
    for nid in neighbor_ids:
        n = id2n.get(nid, {})
        f = n.get("source_file") or n.get("file") or n.get("path") or ""
        if not f:
            continue
        extra_hits.append({
            "file": f,
            "line": n.get("line") or n.get("start_line") or 1,
            "name": n.get("name") or nid,
            "text": n.get("name") or "",
            "kind": "graph_neighbor",
            "score": 30,
        })

    # Cap graph-neighbor spillover. When a query seeds on a large file
    # (e.g. agent.py), every node in it becomes a neighbor and the pack
    # fills with low-signal "graph_neighbor" entries. 12 keeps a useful
    # fan-out without drowning the real hits.
    extra_hits = extra_hits[:12]
    return existing_hits + extra_hits


def ensure_graph_or_expand(directive: str, existing_hits: list, root: str = ".") -> list:
    _ensure_graph_once()
    index = load_or_build_index(root)
    return _expand_from_graph(directive, existing_hits, index)


def _hits_to_files(hits: list, budget: dict, functions: list = None) -> list:
    functions = functions or []
    by_file = {}
    for h in hits:
        fpath = h.get("file", "")
        if not fpath:
            continue
        by_file.setdefault(fpath, []).append(h)

    ranked = sorted(
        by_file.items(),
        key=lambda kv: max(x.get("score", 0) for x in kv[1]),
        reverse=True,
    )[: budget["max_files"]]

    packed = []
    for fpath, fhits in ranked:
        abs_path = fpath if os.path.isabs(fpath) else os.path.join(os.getcwd(), fpath)
        line_count = 0
        content_lines = []
        if os.path.isfile(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    content_lines = fh.read().splitlines()
                line_count = len(content_lines)
            except Exception:
                content_lines = []

        if line_count and line_count <= budget["whole_file_max_lines"]:
            packed.append({
                "path": fpath, "mode": "full",
                "line_count": line_count,
                "content": "\n".join(content_lines),
                "hits": fhits[:10],
            })
            continue

        snippet_ranges = []
        ctx = budget["snippet_context_lines"]
        for h in fhits:
            ln = h.get("line", 0)
            if not ln:
                continue
            lo = max(0, ln - 1 - ctx)
            hi = min(line_count - 1, ln - 1 + ctx) if line_count else ln + ctx
            # Snap the window to the enclosing function so a mid-body hit
            # shows the whole function, not 8 lines each side. seed line
            # is grep's job; extent is the AST's job.
            snap_lo, snap_hi = snap_range_to_function(
                lo + 1, hi + 1, fpath, functions
            )
            lo = max(0, snap_lo - 1)
            hi = min(line_count - 1, snap_hi - 1) if line_count else snap_hi - 1
            term = h.get("name") or (h.get("text", "") or "")[:40] or "match"
            snippet_ranges.append((lo, hi, term))

        snippet_ranges.sort(key=lambda r: r[0])
        merged_ranges = []
        for lo, hi, term in snippet_ranges:
            if merged_ranges and lo <= merged_ranges[-1][1] + 1:
                plo, phi, pterm = merged_ranges[-1]
                merged_ranges[-1] = (plo, max(phi, hi), pterm)
            else:
                merged_ranges.append((lo, hi, term))
        merged_ranges = merged_ranges[: budget["max_snippets_per_file"]]

        if merged_ranges and content_lines:
            snippets = []
            for lo, hi, term in merged_ranges:
                body = "\n".join(
                    f"{i+1:5d}: {content_lines[i]}" for i in range(lo, hi + 1)
                )
                snippets.append({
                    "start_line": lo + 1, "end_line": hi + 1,
                    "matched_term": term, "text": body,
                })
            packed.append({
                "path": fpath, "mode": "snippet",
                "line_count": line_count,
                "snippet_count": len(snippets),
                "snippets": snippets,
                "hits": fhits[:10],
            })
        else:
            packed.append({
                "path": fpath, "mode": "index",
                "line_count": line_count,
                "hits": fhits[:10],
            })

    return packed


def _pack_md(pack: dict) -> str:
    lines = [f"# Recon: {pack.get('directive', '')}", ""]
    lines.append(f"_tier={pack.get('tier_used')}, "
                 f"files={len(pack.get('files', []))}_")
    lines.append("")
    for f in pack.get("files", []):
        lines.append(f"## {f.get('path')}  ({f.get('mode')})")
        if f.get("mode") == "full":
            lines.append("```")
            lines.append(f.get("content", ""))
            lines.append("```")
        else:
            for s in f.get("snippets", []) or []:
                lines.append(f"### lines {s.get('start_line')}-{s.get('end_line')}")
                lines.append("```")
                lines.append(s.get("text", ""))
                lines.append("```")
        lines.append("")
    return "\n".join(lines)

def run_universal_recon(
    directive: str,
    root: str = ".",
    force_graph: bool = False,
    pack_format: str = "json",
    budget: dict = None,
) -> dict:
    budget = dict(budget or DEFAULT_BUDGET)

    synonyms_block = None
    expanded = directive
    if _HAS_SYNONYMS:
        try:
            synonyms_block = _syn_expand(directive)
            extra = [t for t in synonyms_block.get("terms", []) if t != directive]
            if extra:
                expanded = directive + " " + " ".join(extra)
        except Exception:
            synonyms_block = None

    if expanded == directive:
        try:
            llm_expanded = local_llm.maybe_expand_query(directive)
            if llm_expanded and llm_expanded != directive:
                expanded = llm_expanded
        except Exception:
            pass

    index = load_or_build_index(root)

    hits = _tier1(directive, expanded, index)
    tier_used = 1

    # Only expand via graph when the seed contains at least one real hit
    # (a named function or a filename-stem match). If the only seeds are
    # comment-kind or file-header-kind hits, expanding fans out into every
    # neighbor of a weak file — e.g. "where is the mic handler" seeds on
    # agent.py via its docstring, then spills into half the repo. A weak
    # seed should stay weak.
    _strong_seed = any(
        h.get("kind") in ("function", "file")
        for h in hits
    )
    if (len(hits) < TIER1_MIN_HITS and _strong_seed) or force_graph:
        hits = ensure_graph_or_expand(directive, hits, root=root)
        tier_used = 2

    if not hits:
        return {
            "directive": directive,
            "expanded_query": expanded,
            "synonyms": synonyms_block,
            "tier_used": tier_used,
            "graph_used": tier_used == 2,
            "degraded": True,
            "search_terms_used": [directive],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "budget": budget,
            "files": [],
            "note": "No matches found in index or graph.",
        }

    files = _hits_to_files(hits, budget, functions=index.get("functions", []))

    pack = {
        "directive": directive,
        "expanded_query": expanded,
        "synonyms": synonyms_block,
        "tier_used": tier_used,
        "graph_used": tier_used == 2,
        "search_terms_used": [directive] + (
            [t for t in (synonyms_block or {}).get("terms", []) if t != directive]
        ),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "budget": budget,
        "files": files,
        "relation": __import__("relations").resolve_relation(directive),
    }

    if pack_format == "md":
        pack["pack_md"] = _pack_md(pack)

    return pack


def rank_candidate_files(directive: str, expanded: str = None) -> list:
    index = load_or_build_index(".")
    hits = _tier1(directive, expanded or directive, index)
    files = _hits_to_files(hits, DEFAULT_BUDGET, functions=index.get("functions", []))
    return [f.get("path") for f in files]


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# PART 2 FOLLOWS BELOW THIS LINE
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# Git helpers (read-only) + snap (stash checkpoint)
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

def _git_run(root, *args, timeout=8):
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=root, capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 1, "", str(e)


def _git_state(root="."):
    def g(*args):
        code, out, err = _git_run(root, *args)
        return out.strip() if code == 0 else ""

    branch = g("rev-parse", "--abbrev-ref", "HEAD")
    remote = g("config", "--get", "remote.origin.url")
    upstream = g("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = behind = 0
    if upstream:
        code, out, _ = _git_run(root, "rev-list", "--left-right", "--count",
                                f"{upstream}...HEAD")
        if code == 0 and out.strip():
            parts = out.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass

    code, status_out, _ = _git_run(root, "status", "--porcelain")
    dirty = [ln for ln in (status_out or "").splitlines() if ln.strip()]
    last = g("log", "-1", "--format=%h %s")

    return {
        "is_repo": bool(branch),
        "branch": branch or "(not a repo)",
        "remote_url": remote or "(no remote)",
        "upstream": upstream or "(none)",
        "ahead": ahead,
        "behind": behind,
        "dirty_count": len(dirty),
        "dirty_files": [d[:120] for d in dirty[:20]],
        "last_commit": last,
    }


def _git_log(root=".", n=10):
    code, out, _ = _git_run(
        root, "log", f"-{n}",
        "--pretty=format:%h|%ad|%an|%s",
        "--date=short",
    )
    if code != 0 or not out.strip():
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            rows.append({
                "hash": parts[0],
                "date": parts[1],
                "author": parts[2],
                "subject": parts[3],
            })
    return rows


def _git_stash_list(root="."):
    code, out, _ = _git_run(root, "stash", "list",
                            "--pretty=format:%gd|%s|%ci|%gs")
    if code != 0 or not out.strip():
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            rows.append({
                "ref": parts[0],
                "message": parts[1],
                "date": parts[2],
                "branch_note": parts[3],
            })
    return rows


def _load_registry():
    if not os.path.isfile(CHECKPOINT_REGISTRY):
        return {"snaps": []}
    try:
        with open(CHECKPOINT_REGISTRY, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"snaps": []}


def _save_registry(reg):
    os.makedirs(INDEX_DIR, exist_ok=True)
    with open(CHECKPOINT_REGISTRY, "w", encoding="utf-8") as fh:
        json.dump(reg, fh, indent=2)


def create_snap(name=None, root="."):
    state = _git_state(root)
    if not state["is_repo"]:
        return None
    if state["dirty_count"] == 0:
        return None

    snap_name = name or f"pyslick-recon-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    code, out, err = _git_run(root, "stash", "push", "-m", snap_name)
    if code != 0:
        return {"name": snap_name, "error": (err or out or "stash failed")[:200]}

    reg = _load_registry()
    entry = {
        "name": snap_name,
        "ref_at_creation": "stash@{0}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "branch": state["branch"],
        "dirty_count_at_snap": state["dirty_count"],
        "dirty_files_at_snap": state["dirty_files"],
    }
    reg["snaps"].insert(0, entry)
    reg["snaps"] = reg["snaps"][:100]
    _save_registry(reg)
    return entry


def list_snaps(root="."):
    reg = _load_registry()
    live = {s["message"]: s["ref"] for s in _git_stash_list(root)}
    out = []
    for e in reg.get("snaps", []):
        e = dict(e)
        e["live_ref"] = live.get(e["name"], "")
        out.append(e)
    return out


def restore_snap(name="last", root="."):
    snaps = list_snaps(root)
    if not snaps:
        return {"ok": False, "error": "no snaps recorded"}
    target = snaps[0] if name in ("last", None, "") else next(
        (s for s in snaps if s["name"] == name), None
    )
    if not target:
        return {"ok": False, "error": f"no snap named {name!r}"}
    ref = target.get("live_ref")
    if not ref:
        return {"ok": False, "error": f"snap {target['name']!r} no longer exists in git stash"}
    code, out, err = _git_run(root, "stash", "apply", ref)
    return {
        "ok": code == 0,
        "ref": ref,
        "name": target["name"],
        "stdout": (out or "")[:400],
        "stderr": (err or "")[:400],
    }


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# Enrichment helpers
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

def _symbols_for_file(rel_path, index):
    out = []
    for fn in index.get("functions", []) or []:
        if fn.get("file") != rel_path:
            continue
        name = fn.get("name") or ""
        start = fn.get("line") or 0
        end = fn.get("end") or 0
        if not name or not start:
            continue
        kind = fn.get("type") or "function"
        label = f"{name}()" if kind == "function" else name
        out.append({
            "name": name,
            "label": label,
            "signature": fn.get("signature") or "",
            "file": rel_path,
            "start_line": start,
            "end_line": end,
            "range": f"{rel_path}:{start}-{end}" if end else f"{rel_path}:{start}",
            "type": kind,
            "docstring": (fn.get("docstring") or "")[:200],
        })
    return out


def _comments_for_file(rel_path, index):
    out = []
    for c in index.get("comments", []) or []:
        if c.get("file") != rel_path:
            continue
        line = c.get("line") or 0
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "file": rel_path,
            "line": line,
            "range": f"{rel_path}:{line}",
            "kind": c.get("kind") or "comment",
            "text": text[:300],
        })
    return out


def _centrality(root=".", top_k=15):
    gpath = os.path.join(root, GRAPH_PATH) if root != "." else GRAPH_PATH
    if not os.path.isfile(gpath):
        return []
    try:
        with open(gpath, "r", encoding="utf-8", errors="replace") as fh:
            g = json.load(fh)
    except Exception:
        return []
    edges = g.get("edges") or g.get("links") or []
    nodes = g.get("nodes") or []
    counts = Counter()
    for e in edges:
        s = e.get("source") or e.get("from") or e.get("src")
        t = e.get("target") or e.get("to") or e.get("dst")
        if s: counts[s] += 1
        if t: counts[t] += 1
    id2n = {}
    for n in nodes:
        nid = n.get("id") or n.get("name") or n.get("label")
        if nid: id2n[nid] = n
    out = []
    for nid, deg in counts.most_common(top_k):
        n = id2n.get(nid, {})
        out.append({
            "id": nid,
            "degree": deg,
            "file": n.get("file") or n.get("path") or "",
            "line": n.get("line") or n.get("start_line") or 0,
            "type": n.get("type") or n.get("kind") or "node",
        })
    return out


def _llm_keywords(directive, max_terms=8):
    try:
        gen = getattr(local_llm, "generate", None)
        if not callable(gen):
            return []
        prompt = (
            "Extract up to 8 short keyword terms (single words or snake_case "
            "identifiers) from this request that would match Python function "
            "names or comments. Reply with comma-separated terms only.\n\n"
            f"Request: {directive}\nTerms:"
        )
        out = gen(prompt, max_tokens=64)
        if not out:
            return []
        terms = [t.strip().strip(".,;:") for t in out.split(",")]
        return [t for t in terms if 2 <= len(t) <= 40][:max_terms]
    except Exception:
        return []


# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ
# Main: full recon
# ΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉΓòÉ

def run_full_recon(directive, root=".", budget=None, checkpoint=False):
    """
    Comprehensive READ-ONLY recon. Superset of run_universal_recon().
    If the git tree is dirty, creates a named stash snap first.
    No add / pull / push. No commits.
    """
    pack = run_universal_recon(directive, root=root, budget=budget)
    pack["mode"] = "full_recon"

    snap = None
    if checkpoint:
        snap = create_snap(root=root)
    pack["snap"] = snap

    index = load_or_build_index(root)
    keywords = _llm_keywords(directive)

    for entry in pack.get("files", []) or []:
        rel = entry.get("path", "")
        entry["symbols"] = _symbols_for_file(rel, index)
        entry["comments"] = _comments_for_file(rel, index)
        entry["functions_index"] = [s["range"] for s in entry["symbols"]]

    kw_hits = []
    if keywords:
        lowered = [k.lower() for k in keywords]
        for fn in index.get("functions", []) or []:
            name = (fn.get("name") or "").lower()
            doc = (fn.get("docstring") or "").lower()
            for k in lowered:
                if k in name or k in doc:
                    rel = fn.get("file") or ""
                    start = fn.get("line") or 0
                    end = fn.get("end") or 0
                    kw_hits.append({
                        "name": fn.get("name") or "",
                        "label": f"{fn.get('name')}()",
                        "file": rel,
                        "start_line": start,
                        "end_line": end,
                        "range": f"{rel}:{start}-{end}" if end else f"{rel}:{start}",
                        "matched_keyword": k,
                    })
                    break

    pack["keyword_terms"] = keywords
    pack["keyword_functions"] = kw_hits[:30]
    pack["centrality"] = _centrality(root=root)
    pack["git"] = _git_state(root=root)
    pack["git_log"] = _git_log(root=root, n=10)
    pack["git_stash"] = _git_stash_list(root=root)
    pack["snaps"] = list_snaps(root=root)

    return pack


