#!/usr/bin/env python3
"""
recon_pack.py — PySlick local context packer

Problem this solves:
    `agent` needs ANTHROPIC_API_KEY and does full autonomous tool-calling +
    JSON patch synthesis via the cloud Claude API. The local 135M GGUF model
    (see llm.py) is genuinely too small to drive that loop reliably — it
    can't hold a whole file in its 512-token context, can't do multi-step
    tool planning, and can't be trusted to emit exact-substring find/replace
    without risking a bad match.

    What the local model IS good at: turning a vague directive into a few
    extra search terms. That's all `maybe_expand_query()` in llm.py does,
    and it's reliable at that scale.

    recon_pack bridges the gap: it uses the local model only for query
    expansion, runs the SAME proven local search machinery `recon.py`
    already uses (fuzzy graph match + comment-block scan), and instead of
    asking any model to produce a patch, it writes a curated JSON bundle of
    exactly the file content a human would want to paste into a web AI
    (Claude.ai, ChatGPT, whatever) to get a patch proposal back manually.

    No API key needed. No autonomous writes. Output is inert JSON.

Run:
    pyslick recon-pack "connect bing extension websocket to electron main"
    pyslick recon-pack "resize mic button" --max-files 4 --max-lines 800

Output:
    .pyslick_context/<timestamp>.json
    Also copied to clipboard automatically (same mechanism as `pyslick copy`).

Design note — the "budget table":
    Whether a file goes in as FULL CONTENT or as SNIPPETS ONLY is decided by
    a fixed, deterministic budget (CONTEXT_BUDGET below), not by asking the
    local LLM to judge file size. A 135M model judging "is this file too
    big" is exactly the kind of soft-reasoning call it's unreliable at;
    counting lines against a threshold is not.
"""

import sys
import os
import json
import re
from datetime import datetime

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

from find_nearest_nodes import (
    load_graph_nodes,
    load_graphify_vocab,
    expand_query_with_vocab,
)
from comment_blocks import (
    scan_project_for_comment_blocks,
    comment_nodes_as_graph_nodes,
)
from repomap import (
    is_overview_query,
    get_codebase_overview,
    query_multilang,
    is_supported as repomap_is_supported,
    is_generated_or_minified_file,
)
from decompose import decompose
from relations import is_relation_query, resolve_relation
import llm as local_llm

try:
    from synonyms import expand as _syn_expand
    _HAS_SYNONYMS = True
except ImportError:
    _HAS_SYNONYMS = False

try:
    from text_index import build_text_index, search_text_index_auto
    _HAS_TEXT_INDEX = True
except ImportError:
    _HAS_TEXT_INDEX = False

BOLD  = "\033[1m"
CYAN  = "\033[96m"
GREEN = "\033[92m"
YELL  = "\033[93m"
RED   = "\033[91m"
DIM   = "\033[2m"
RST   = "\033[0m"

def hdr(phase: str, title: str):
    print(f"\n{BOLD}{CYAN}━━  {phase}  {RST}{BOLD}{title}{RST}")
    print(f"{DIM}{'─' * 58}{RST}")

def ok(msg):  print(f"{GREEN}  ✔ {msg}{RST}")
def warn(msg): print(f"{YELL}  ⚠ {msg}{RST}")
def err(msg):  print(f"{RED}  ✖ {msg}{RST}")


# ─────────────────────────────────────────────────────────────────────────
# The budget table (deterministic, not model-judged)
# ─────────────────────────────────────────────────────────────────────────
CONTEXT_BUDGET = {
    "whole_file_max_lines": 300,   # <= this many lines -> include file in full
    "snippet_context_lines": 8,    # lines of context around each match, each side
    "max_files": 6,                # cap on number of files packed
    "max_total_lines": 1200,       # hard cap across the whole bundle
    "max_snippets_per_file": 5,    # cap runaway matches inside one file
}

OUT_DIR = ".pyslick_context"


def _read_lines(path: str) -> list[str] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return None


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent (lo, hi) 0-indexed line ranges."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [ranges[0]]
    for lo, hi in ranges[1:]:
        plo, phi = merged[-1]
        if lo <= phi + 1:
            merged[-1] = (plo, max(phi, hi))
        else:
            merged.append((lo, hi))
    return merged


def _find_match_ranges(lines: list[str], terms: list[str], context: int, file_path: str = "") -> list[tuple[int, int, str]]:
    """Return merged 0-indexed (lo, hi, matched_term) ranges around every
    line that matches any search term. If tree-sitter or AST is available,
    snaps match lines to their complete enclosing functions."""
    compiled = []
    for t in terms:
        if len(t) < 3 or t.lower() in _STOPWORDS:
            continue
        try:
            compiled.append((t, re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)))
        except re.error:
            compiled.append((t, re.compile(re.escape(t), re.IGNORECASE)))

    if not compiled:
        return []

    hit_lines = []
    for i, line in enumerate(lines):
        # Skip raw base64 or minified lines
        if len(line) > 1500 or "data:image/" in line or "data:application/" in line:
            continue
        for term, rx in compiled:
            if rx.search(line):
                hit_lines.append((i + 1, term))
                break


    if not hit_lines:
        return []

    # Check for AST function spans if available
    ast_ranges = []
    if file_path and repomap_is_supported(file_path):
        try:
            from repomap import extract_tags
            tags = extract_tags(file_path) or []
            defs = [t for t in tags if t.is_def and t.end_line > t.start_line]
            for hline, hterm in hit_lines:
                snapped = False
                for d in defs:
                    if d.start_line <= hline <= d.end_line:
                        ast_ranges.append((max(0, d.start_line - 1), min(len(lines) - 1, d.end_line - 1), hterm))
                        snapped = True
                        break
                if not snapped:
                    lo = max(0, hline - 1 - context)
                    hi = min(len(lines) - 1, hline - 1 + context)
                    ast_ranges.append((lo, hi, hterm))
        except Exception:
            ast_ranges = []

    if not ast_ranges:
        for hline, hterm in hit_lines:
            lo = max(0, hline - 1 - context)
            hi = min(len(lines) - 1, hline - 1 + context)
            ast_ranges.append((lo, hi, hterm))

    ast_ranges.sort(key=lambda h: h[0])
    merged: list[tuple[int, int, str]] = []
    for lo, hi, term in ast_ranges:
        if merged and lo <= merged[-1][1] + 1:
            prev_lo, prev_hi, prev_term = merged[-1]
            merged[-1] = (prev_lo, max(prev_hi, hi), prev_term)
        else:
            merged.append((lo, hi, term))
    return merged


def pack_file(path: str, terms: list[str], budget: dict) -> dict | None:
    """Decide full-file vs snippet mode for one file and build its entry."""
    if is_generated_or_minified_file(path):
        return None

    lines = _read_lines(path)
    if lines is None:
        return None

    total = len(lines)

    if total <= budget["whole_file_max_lines"]:
        return {
            "path": path,
            "mode": "full",
            "line_count": total,
            "content": "\n".join(lines),
        }

    ranges = _find_match_ranges(lines, terms, budget["snippet_context_lines"], file_path=path)
    if not ranges:
        return {
            "path": path,
            "mode": "skipped_no_match",
            "line_count": total,
            "note": f"File has {total} lines (over the {budget['whole_file_max_lines']}-line "
                    f"full-include threshold) and no search terms matched directly.",
        }

    ranges = ranges[: budget["max_snippets_per_file"]]
    snippets = []
    for lo, hi, matched_term in ranges:
        snippet_lines = []
        for i in range(lo, hi + 1):
            line_str = lines[i]
            if len(line_str) > 220:
                line_str = line_str[:217] + "..."
            snippet_lines.append(f"{i+1:5d}: {line_str}")
        snippets.append({
            "start_line": lo + 1,
            "end_line": hi + 1,
            "matched_term": matched_term,
            "text": "\n".join(snippet_lines),
        })

    return {
        "path": path,
        "mode": "snippet",
        "line_count": total,
        "snippet_count": len(snippets),
        "snippets": snippets,
    }


MIN_GRAPH_MATCH_SCORE = 45

FALLBACK_SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", "graphify-out",
    ".pyslick_backups", ".pyslick_context", ".gradle", "intermediates", "outputs",
}
FALLBACK_SKIP_EXTS = {
    ".css", ".lock", ".svg", ".png", ".jpg", ".jpeg", ".gif",
    ".pdf", ".ico", ".map", ".bak", ".woff", ".woff2", ".ttf",
}

_STOPWORDS = {
    "what", "does", "do", "is", "are", "the", "a", "an", "this", "that",
    "these", "those", "how", "why", "when", "where", "which", "who",
    "work", "works", "use", "used", "using", "make", "makes", "made",
    "get", "gets", "got", "have", "has", "had", "can", "could", "would",
    "should", "will", "be", "been", "being", "or", "and", "but", "if",
    "then", "than", "so", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "as", "it", "its", "my", "your", "our", "their",
}


def _grep_fallback_search(search_terms: list[str], already_found: list[str]) -> list[str]:
    """Direct file-content search across the project."""
    terms = [t.lower() for t in search_terms if len(t) > 2 and t.lower() not in _STOPWORDS]
    if not terms:
        return []

    scored: list[tuple[int, str]] = []
    for dirpath, dirnames, filenames in os.walk(os.getcwd()):
        dirnames[:] = [d for d in dirnames
                       if d not in FALLBACK_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or any(fn.endswith(e) for e in FALLBACK_SKIP_EXTS):
                continue
            fpath = os.path.normpath(os.path.join(dirpath, fn))
            if fpath in already_found or is_generated_or_minified_file(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read().lower()
            except Exception:
                continue
            hits = sum(1 for t in terms if t in content)
            if hits >= 1:
                scored.append((hits, fpath))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in scored]


def gather_candidate_files(directive: str, expanded: str) -> list[str]:
    """Rank candidate files using BM25 semantic text index + AST graph match + comment blocks."""
    ranked_paths: list[str] = []
    seen_paths = set()

    def _add_path(p: str):
        if not p or not os.path.isfile(p) or is_generated_or_minified_file(p):
            return
        try:
            rel = os.path.normpath(os.path.relpath(p, "."))
        except Exception:
            rel = os.path.normpath(p)
        if rel not in seen_paths:
            seen_paths.add(rel)
            ranked_paths.append(rel)

    # 1. BM25 Semantic Text Index (docstrings, comments, code identifiers)
    if _HAS_TEXT_INDEX:
        try:
            t_idx = build_text_index(".")
            hits, _ = search_text_index_auto(expanded or directive, t_idx)
            for h in hits:
                _add_path(h.file)
        except Exception:
            pass

    # 2. AST Graph Nodes + Comment Blocks
    nodes = load_graph_nodes() or []
    comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
    comment_nodes = comment_nodes_as_graph_nodes(comment_nodes_raw)
    all_nodes = nodes + comment_nodes

    if all_nodes:
        from rapidfuzz import process
        from rapidfuzz.fuzz import WRatio

        # Stem matching: any directive token matching filename stem gets priority
        directive_tokens = [t for t in re.split(r"[^a-zA-Z0-9_]+", directive.lower()) if len(t) > 2 and t not in _STOPWORDS]
        stems: dict[str, str] = {}
        for n in all_nodes:
            sf = n.get("source_file") or n.get("file") or n.get("path") or ""
            if sf:
                stem = os.path.splitext(os.path.basename(sf))[0].lower()
                stems.setdefault(stem, sf)

        for tok in directive_tokens:
            for stem, sf in stems.items():
                if tok == stem or tok == stem.replace("_", ""):
                    _add_path(sf)

        labels = [n["label"] for n in all_nodes]
        raw_results = process.extract(expanded, labels, scorer=WRatio, limit=20)

        for match, score, index in raw_results:
            if score < MIN_GRAPH_MATCH_SCORE:
                continue
            node = all_nodes[index]
            if node["type"] in ("marker_block", "descriptive_block"):
                path = node["_comment_node"].file
            else:
                path = node.get("source_file") or node.get("file") or node.get("path")
            _add_path(path)

    # 3. Fallback direct grep if candidate pool is thin
    if len(ranked_paths) < 2:
        search_terms = list({directive, expanded, *expanded.split(), *directive.split()})
        fallback_paths = _grep_fallback_search(search_terms, ranked_paths)
        for fp in fallback_paths:
            _add_path(fp)

    return ranked_paths


def build_pack(directive: str, budget: dict) -> dict:
    # ── 1. Codebase Overview Intent ───────────────────────────────────────
    if is_overview_query(directive):
        hdr("Overview", "Generating App Architecture & God-Node Overview")
        overview = get_codebase_overview(".")
        ok("Extracted PageRank top files and project descriptors")

        overview_files = []
        # Add top connected files to the pack
        top_paths = [tf["path"] for tf in overview.get("top_files", [])] + overview.get("entry_points", [])
        seen = set()
        for tp in top_paths:
            if tp in seen or not os.path.isfile(tp):
                continue
            seen.add(tp)
            entry = pack_file(tp, ["export", "function", "class", "const"], budget)
            if entry:
                overview_files.append(entry)
                if len(overview_files) >= budget["max_files"]:
                    break

        return {
            "directive": directive,
            "mode": "codebase_overview",
            "overview_markdown": overview.get("overview_text", ""),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "budget": budget,
            "files": overview_files,
        }

    # ── 2. Compound Query Decomposition ───────────────────────────────────
    subqueries = decompose(directive)
    if len(subqueries) > 1:
        hdr("Decompose", f"Split compound query into {len(subqueries)} parts:")
        for sq in subqueries:
            print(f"  {DIM}· {sq}{RST}")
    else:
        subqueries = [directive]

    # ── 3. Relationship Check ─────────────────────────────────────────────
    relation_info = None
    if is_relation_query(directive):
        hdr("Relation", "Resolving entity connection graph")
        try:
            relation_info = resolve_relation(directive)
            if relation_info.get("confidence") != "none":
                ok(f"Found connection: {relation_info.get('note')}")
        except Exception:
            relation_info = None

    # ── 4. Query Expansion (Synonyms + Local LLM) ─────────────────────────
    hdr("Phase 1", "Expand — domain synonyms & query expansion")
    all_expanded_terms = []
    all_candidates = []

    for sq in subqueries:
        exp_terms = [sq]
        if _HAS_SYNONYMS:
            try:
                syn_b = _syn_expand(sq)
                for t in syn_b.get("terms", []):
                    if t not in exp_terms:
                        exp_terms.append(t)
            except Exception:
                pass

        try:
            llm_exp = local_llm.maybe_expand_query(sq)
            if llm_exp and llm_exp != sq:
                exp_terms.append(llm_exp)
        except Exception:
            pass

        full_exp = " ".join(exp_terms)
        all_expanded_terms.extend(exp_terms)

        # Gather candidates for this subquery
        cands = gather_candidate_files(sq, full_exp)
        for c in cands:
            if c not in all_candidates:
                all_candidates.append(c)

    ok(f"{len(all_candidates)} candidate file(s) found across {len(subqueries)} sub-query(s)")

    search_terms = list(dict.fromkeys(
        [t for t in (all_expanded_terms + directive.split()) if len(t) > 2 and t.lower() not in _STOPWORDS]
    ))

    # ── 5. Pack Files ─────────────────────────────────────────────────────

    hdr("Phase 2", "Pack — deciding full-file vs snippet per budget")
    packed_files = []
    total_lines_used = 0

    for path in all_candidates:
        if len(packed_files) >= budget["max_files"]:
            warn(f"max_files ({budget['max_files']}) reached — stopping")
            break

        entry = pack_file(path, search_terms, budget)
        if entry is None:
            continue

        entry_lines = entry.get("line_count", 0) if entry["mode"] == "full" else sum(
            s["end_line"] - s["start_line"] + 1 for s in entry.get("snippets", [])
        )

        if total_lines_used + entry_lines > budget["max_total_lines"] and packed_files:
            warn(f"max_total_lines ({budget['max_total_lines']}) reached — stopping before {path}")
            break

        total_lines_used += entry_lines
        packed_files.append(entry)

        mode_label = {
            "full": "full file",
            "snippet": f"{entry.get('snippet_count', 0)} snippet(s)",
            "skipped_no_match": "skipped (no match, too large)",
        }[entry["mode"]]
        print(f"  {DIM}·{RST} {path}  {DIM}({entry['line_count']} lines){RST} → {mode_label}")
        if entry["mode"] == "snippet":
            for s in entry.get("snippets", []):
                print(f"      {DIM}lines {s['start_line']}-{s['end_line']}: matched '{s.get('matched_term', '?')}'{RST}")

    result = {
        "directive": directive,
        "subqueries": subqueries if len(subqueries) > 1 else None,
        "search_terms_used": search_terms,
        "relation": relation_info,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "budget": budget,
        "files": packed_files,
    }
    return result


def write_pack(pack: dict) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUT_DIR, f"context_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=2)
    return out_path


def _try_copy_to_clipboard(text: str):
    try:
        from clipboard import copy_to_clipboard
        copy_to_clipboard(text)
        ok("Copied to clipboard — paste into your web AI.")
    except Exception:
        warn("Could not auto-copy (clipboard module unavailable) — open the file and copy manually.")


def _parse_argv(argv: list[str]) -> tuple[str, dict]:
    budget = dict(CONTEXT_BUDGET)
    words = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--max-files" and i + 1 < len(argv):
            budget["max_files"] = int(argv[i + 1]); i += 2
        elif arg == "--max-lines" and i + 1 < len(argv):
            budget["max_total_lines"] = int(argv[i + 1]); i += 2
        elif arg == "--whole-file-max-lines" and i + 1 < len(argv):
            budget["whole_file_max_lines"] = int(argv[i + 1]); i += 2
        elif arg == "--context-lines" and i + 1 < len(argv):
            budget["snippet_context_lines"] = int(argv[i + 1]); i += 2
        else:
            words.append(arg); i += 1
    return " ".join(words), budget


def main():
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════╗")
    print(f"║      PySlick Recon-Pack (local, no API key)      ║")
    print(f"╚══════════════════════════════════════════════════╝{RST}")

    if len(sys.argv) < 2:
        err('Usage: pyslick recon-pack "<directive>" [--max-files N] [--max-lines N]')
        sys.exit(1)

    directive, budget = _parse_argv(sys.argv[1:])
    if not directive:
        err("No directive given.")
        sys.exit(1)

    pack = build_pack(directive, budget)
    if not pack.get("files") and not pack.get("overview_markdown"):
        err("Nothing packed — no output written.")
        sys.exit(1)

    hdr("Phase 3", "Write")
    out_path = write_pack(pack)
    ok(f"Written: {out_path}")

    text = json.dumps(pack, indent=2)
    _try_copy_to_clipboard(text)

    files_count = len(pack.get("files", []))
    lines_ref = sum(f.get("line_count", 0) for f in pack.get("files", []))
    print(f"\n{DIM}  {files_count} file(s), ~{lines_ref} source lines referenced.{RST}")
    print(f"{DIM}  Paste this into your web AI along with what you want changed.{RST}")


if __name__ == "__main__":
    main()

