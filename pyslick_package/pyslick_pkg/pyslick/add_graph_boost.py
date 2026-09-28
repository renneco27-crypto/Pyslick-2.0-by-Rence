from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("gboost-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

p = ROOT / "toolbox.py"
raw = p.read_bytes()
if raw.startswith(b"\xef\xbb\xbf"):
    raw = raw[3:]
text = raw.decode("utf-8", errors="strict")
eol = "\r\n" if "\r\n" in text else "\n"

if "_graph_degree_boost" in text:
    print("already patched")
    sys.exit(0)

# -----------------------------------------------------------------------
# 1. Add a helper module-level function before mode_semantic_grep.
# -----------------------------------------------------------------------
helper = '''

def _load_graph_degrees(root: str = "."):
    """Return (degree_by_file, node_by_id, graph) from graph.json, or ({}, {}, {}).

    degree_by_file: filepath -> total call edges touching nodes in that file
    node_by_id:     node_id -> node dict
    """
    import json as _json
    import os as _os
    path = _os.environ.get("GRAPHIFY_GRAPH", "graphify-out/graph.json")
    if not _os.path.isabs(path):
        path = _os.path.join(root, path)
    if not _os.path.isfile(path):
        return {}, {}, {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            g = _json.load(f)
    except Exception:
        return {}, {}, {}
    nodes = g.get("nodes", [])
    links = g.get("links", [])
    by_id = {n.get("id"): n for n in nodes if n.get("id")}
    deg = {}
    for l in links:
        if l.get("relation") != "calls":
            continue
        for endpoint in (l.get("source"), l.get("target")):
            n = by_id.get(endpoint)
            if not n:
                continue
            f = n.get("source_file")
            if f:
                # Normalize to the same shape mode_semantic_grep uses
                f_norm = f.replace("\\\\", "/")
                deg[f_norm] = deg.get(f_norm, 0) + 1
    return deg, by_id, g


def _graph_degree_boost(filepath: str, deg: dict) -> float:
    """Return a multiplier in [1.0, 1.5] based on how connected the file is.

    A file with 50+ total call edges gets the full 1.5x boost; a leaf file
    with 0 edges gets 1.0x. Never drops a score.
    """
    f = filepath.replace("\\\\", "/")
    d = deg.get(f, 0)
    if d <= 0:
        return 1.0
    # Saturating: min(degree / 50, 1.0) gives the boost fraction
    return 1.0 + min(d / 50.0, 0.5)


def _recommended_reads(query: str, by_id: dict, top_n: int = 5) -> list:
    """Top graph functions whose label shares tokens with the query.

    Returns list of (label, file, line, caller_count, callee_count).
    """
    import re as _re
    if not by_id:
        return []
    # Query tokens, drop very common words
    stop = {"where", "what", "how", "the", "is", "are", "for", "and",
            "find", "show", "does", "did", "in", "on", "at", "with", "of",
            "to", "a", "an", "this", "that", "work", "works"}
    qt = [t.lower() for t in _re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query)
          if len(t) >= 3 and t.lower() not in stop]
    if not qt:
        return []
    # Build in/out degree per node id
    in_deg = {}
    out_deg = {}
    # Rebuild from graph if needed -- by_id only gives us nodes. We need links.
    # Cheap workaround: recompute via the graph object passed into _load_graph_degrees.
    # For this helper, just score by label token overlap.
    scored = []
    for nid, n in by_id.items():
        label = (n.get("label") or "").lower()
        if not label:
            continue
        # Count token overlap
        label_tokens = set(_re.findall(r"[A-Za-z_][A-Za-z0-9_]*", label))
        overlap = sum(1 for t in qt if any(t in lt or lt in t for lt in label_tokens))
        if overlap == 0:
            continue
        scored.append((overlap, nid, n))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(n.get("label") or nid, n.get("source_file") or "?", n.get("source_location") or "?")
            for _, nid, n in scored[:top_n]]

'''

# Insert helper before mode_semantic_grep definition.
anchor = "def mode_semantic_grep("
if anchor not in text:
    print("mode_semantic_grep not found")
    sys.exit(1)

text = text.replace(anchor, helper + "\n" + anchor, 1)

# -----------------------------------------------------------------------
# 2. Inside mode_semantic_grep: after the intent/decompose prints and
#    before "idx = build_text_index(root)", insert the graph load and
#    intent label.
# -----------------------------------------------------------------------

old_idx = '''    idx = build_text_index(root)
    merged_results = []'''

new_idx = '''    # Print intent label (top-level classification)
    _intent_label = "keyword"
    try:
        from relations import is_relation_query as _is_rel
        from repomap import is_overview_query as _is_ov
        if _is_ov(query):
            _intent_label = "overview"
        elif _is_rel(query):
            _intent_label = "relationship"
        elif len(subqueries) > 1:
            _intent_label = "compound"
    except Exception:
        pass
    print(f"{DIM}intent: {_intent_label}{RST}")

    # Load graph degrees for ranking boost + recommended reads
    _deg_by_file, _nodes_by_id, _graph_obj = _load_graph_degrees(root)

    idx = build_text_index(root)
    merged_results = []'''

if old_idx not in text:
    print("anchor for build_text_index block not found")
    sys.exit(1)
text = text.replace(old_idx, new_idx, 1)

# -----------------------------------------------------------------------
# 3. After "results = merged_results[:top_k]", apply the boost.
# -----------------------------------------------------------------------

old_results = '''    results = merged_results[:top_k]
    keywords = list(dict.fromkeys(all_keywords))
    syn_terms = list(dict.fromkeys(all_syn_terms))'''

new_results = '''    results = merged_results[:top_k]
    keywords = list(dict.fromkeys(all_keywords))
    syn_terms = list(dict.fromkeys(all_syn_terms))

    # Apply graph-degree boost and re-rank
    if _deg_by_file:
        _boosted = []
        for fp, sc, mt in results:
            _mult = _graph_degree_boost(fp, _deg_by_file)
            _boosted.append((fp, sc * _mult, mt))
        _boosted.sort(key=lambda r: r[1], reverse=True)
        results = _boosted'''

if old_results not in text:
    print("anchor for results block not found")
    sys.exit(1)
text = text.replace(old_results, new_results, 1)

# -----------------------------------------------------------------------
# 4. Before the final "def main():" of the file, add Recommended reads
#    print at the end of mode_semantic_grep. Find the "        if total_shown >= top_k:"
#    block that ends the function and insert after it.
# -----------------------------------------------------------------------

old_tail = '''        total_shown += 1
        if total_shown >= top_k:
            break
'''

new_tail = '''        total_shown += 1
        if total_shown >= top_k:
            break

    # Recommended reads block from graph labels
    if _nodes_by_id:
        recs = _recommended_reads(query, _nodes_by_id, top_n=5)
        if recs:
            print(f"\\n{BOLD}{CYAN}Recommended reads (from call graph):{RST}")
            for label, sfile, sloc in recs:
                print(f"  {GREEN}*{RST} {BOLD}{label}{RST}  {DIM}({sfile}:{sloc}){RST}")
            print()
'''

if old_tail not in text:
    print("anchor for tail block not found")
    sys.exit(1)
text = text.replace(old_tail, new_tail, 1)

# -----------------------------------------------------------------------
# Write + verify
# -----------------------------------------------------------------------
shutil.copy2(p, BK / p.name)
p.write_bytes(text.encode("utf-8"))

r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                   capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BK / p.name, p)
    print("py_compile failed, rolled back:")
    print(r.stderr or r.stdout)
    sys.exit(1)

print("APPLIED. Backups:", BK)
