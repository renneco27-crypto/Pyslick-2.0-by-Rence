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
import llm as local_llm

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


def _find_match_ranges(lines: list[str], terms: list[str], context: int) -> list[tuple[int, int]]:
    """Return merged 0-indexed (lo, hi) ranges around every line that
    matches any search term (case-insensitive substring or regex)."""
    compiled = []
    for t in terms:
        try:
            compiled.append(re.compile(t, re.IGNORECASE))
        except re.error:
            compiled.append(re.compile(re.escape(t), re.IGNORECASE))

    hits = []
    for i, line in enumerate(lines):
        if any(rx.search(line) for rx in compiled):
            lo = max(0, i - context)
            hi = min(len(lines) - 1, i + context)
            hits.append((lo, hi))
    return _merge_ranges(hits)


def pack_file(path: str, terms: list[str], budget: dict) -> dict | None:
    """Decide full-file vs snippet mode for one file and build its entry."""
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

    ranges = _find_match_ranges(lines, terms, budget["snippet_context_lines"])
    if not ranges:
        # No direct term hits in an oversized file — better to say so
        # explicitly than to silently omit it or dump the whole thing.
        return {
            "path": path,
            "mode": "skipped_no_match",
            "line_count": total,
            "note": f"File has {total} lines (over the {budget['whole_file_max_lines']}-line "
                    f"full-include threshold) and no search terms matched directly. "
                    f"Omitted — was likely only included via fuzzy graph score.",
        }

    ranges = ranges[: budget["max_snippets_per_file"]]
    snippets = []
    for lo, hi in ranges:
        snippet_lines = [f"{i+1:5d}: {lines[i]}" for i in range(lo, hi + 1)]
        snippets.append({
            "start_line": lo + 1,
            "end_line": hi + 1,
            "text": "\n".join(snippet_lines),
        })

    return {
        "path": path,
        "mode": "snippet",
        "line_count": total,
        "snippet_count": len(snippets),
        "snippets": snippets,
    }


def gather_candidate_files(directive: str, expanded: str) -> list[str]:
    """Reuse recon's existing search machinery to rank candidate files —
    same fuzzy graph match + comment-block scan, no new matching logic."""
    nodes = load_graph_nodes() or []

    comment_nodes_raw = scan_project_for_comment_blocks(os.getcwd())
    comment_nodes = comment_nodes_as_graph_nodes(comment_nodes_raw)

    all_nodes = nodes + comment_nodes
    if not all_nodes:
        return []

    from rapidfuzz import process
    from rapidfuzz.fuzz import WRatio

    labels = [n["label"] for n in all_nodes]
    raw_results = process.extract(expanded, labels, scorer=WRatio, limit=20)

    ranked_paths = []
    for match, score, index in raw_results:
        node = all_nodes[index]
        if node["type"] in ("marker_block", "descriptive_block"):
            path = node["_comment_node"].file
        else:
            # node ids from graph nodes are expected to carry a file hint;
            # fall back to skipping if we can't resolve one cleanly.
            path = node.get("file") or node.get("path")
        if path and os.path.isfile(path) and path not in ranked_paths:
            ranked_paths.append(path)

    return ranked_paths


def build_pack(directive: str, budget: dict) -> dict:
    hdr("Phase 1", "Expand — local LLM query expansion")
    expanded = local_llm.maybe_expand_query(directive)
    if expanded != directive:
        ok(f"Expanded: {expanded}")
    else:
        warn("Local model unavailable or made no change — using directive as-is "
             "(run `pyslick llm-status` to check setup)")

    vocab = load_graphify_vocab()
    fully_expanded = expand_query_with_vocab(expanded, vocab)

    hdr("Phase 2", "Locate — fuzzy graph match + comment-block scan")
    candidates = gather_candidate_files(directive, fully_expanded)
    if not candidates:
        err("No candidate files found. Run: graphify extract . --code-only")
        return {}
    ok(f"{len(candidates)} candidate file(s) found")

    search_terms = list({directive, expanded, *fully_expanded.split()})

    hdr("Phase 3", "Pack — deciding full-file vs snippet per budget")
    packed_files = []
    total_lines_used = 0
    for path in candidates:
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
            warn(f"max_total_lines ({budget['max_total_lines']}) reached — "
                 f"stopping before {path}")
            break

        total_lines_used += entry_lines
        packed_files.append(entry)

        mode_label = {"full": "full file", "snippet": f"{entry.get('snippet_count', 0)} snippet(s)",
                      "skipped_no_match": "skipped (no match, too large)"}[entry["mode"]]
        print(f"  {DIM}·{RST} {path}  {DIM}({entry['line_count']} lines){RST} → {mode_label}")

    return {
        "directive": directive,
        "expanded_query": expanded,
        "search_terms_used": search_terms,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "budget": budget,
        "files": packed_files,
    }


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
        warn("Could not auto-copy (clipboard module unavailable) — "
             "open the file and copy it manually.")


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
    if not pack.get("files"):
        err("Nothing packed — no output written.")
        sys.exit(1)

    hdr("Phase 4", "Write")
    out_path = write_pack(pack)
    ok(f"Written: {out_path}")

    text = json.dumps(pack, indent=2)
    _try_copy_to_clipboard(text)

    print(f"\n{DIM}  {len(pack['files'])} file(s), "
          f"~{sum(f.get('line_count', 0) for f in pack['files'])} source lines referenced.{RST}")
    print(f"{DIM}  Paste this into your web AI along with what you want changed.{RST}")


if __name__ == "__main__":
    main()
