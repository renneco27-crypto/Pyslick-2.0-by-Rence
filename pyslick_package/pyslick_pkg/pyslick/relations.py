"""relations.py — resolve relationship queries between two named entities
using the pre-built graphify graph. Pure stdlib, no API, no LLM.

Usage:
    from relations import resolve_relation
    result = resolve_relation("how does llm connect with init")
    # -> {"entities": [...], "paths": [...], "evidence": [...], "confidence": "..."}
"""

from __future__ import annotations

import json
import os
import re
from collections import deque

GRAPH_PATH = os.path.join("graphify-out", "graph.json")

# Words that are never entities in a codebase
_STOP = {
    "how", "does", "do", "is", "are", "the", "a", "an", "with", "and",
    "to", "from", "of", "in", "on", "by", "for", "what", "where",
    "when", "why", "which", "who", "connect", "connects", "connected",
    "connection", "link", "links", "linked", "call", "calls", "called",
    "relate", "relates", "related", "relationship", "between", "work",
    "works", "working", "use", "uses", "used", "using", "go", "goes",
    "go through", "flow", "flows", "handle", "handles", "py",
}

# Directives that look like relationship queries
_REL_PATTERNS = [
    r"\bhow\b.*\bconnect",
    r"\bhow\b.*\blink",
    r"\bconnect\b.*\b(?:to|with|and)\b",
    r"\blink\b.*\b(?:to|with|and)\b",
    r"\brelat\w*\b",
    r"\bbetween\b.*\band\b",
    r"\bpath\b.*\bfrom\b.*\bto\b",
    r"\bcall(?:s|ed)?\b.*\bfrom\b",
    r"\bdepends?\b",
]


def is_relation_query(directive: str) -> bool:
    d = directive.lower()
    return any(re.search(p, d) for p in _REL_PATTERNS)


def extract_entities(directive: str) -> list[str]:
    """Pull candidate entity names out of a directive.

    Handles: 'llm', 'llm.py', 'llmpy' (concatenated), 'init', '__init__'.
    Order preserved; duplicates removed.
    """
    d = directive.lower()
    # Split on non-identifier chars, keep alnum + underscore tokens
    tokens = re.findall(r"[a-z_][a-z0-9_]*", d)
    out: list[str] = []
    for t in tokens:
        if t in _STOP or len(t) < 2:
            continue
        if t not in out:
            out.append(t)
    return out


def _load_graph(path: str = GRAPH_PATH) -> tuple[dict, list[dict]]:
    with open(path, "r", encoding="utf-8") as f:
        g = json.load(f)
    return g.get("nodes", []), g.get("links", [])


def _build_index(nodes: list[dict]) -> dict[str, list[dict]]:
    """Map every lowercase alias of a node id to the node dict."""
    idx: dict[str, list[dict]] = {}
    for n in nodes:
        nid = (n.get("id") or "").lower()
        if not nid:
            continue
        idx.setdefault(nid, []).append(n)
        # also index the bare suffix (agent_foo -> foo)
        if "_" in nid:
            tail = nid.rsplit("_", 1)[-1]
            idx.setdefault(tail, []).append(n)
        # and the norm_label
        nl = (n.get("norm_label") or "").lower()
        if nl:
            idx.setdefault(nl, []).append(n)
    return idx


def _match_entity(term: str, index: dict[str, list[dict]]) -> list[dict]:
    """Find graph nodes matching a user-typed term."""
    t = term.lower().strip("._")
    if t in index:
        return index[t]
    # 'llmpy' typed for 'llm.py' -> try trimming a trailing file ext glued on
    for ext in ("py", "js", "ts", "tsx"):
        if t.endswith(ext) and t[: -len(ext)] in index:
            return index[t[: -len(ext)]]
    # substring match (last resort, but bounded)
    hits: list[dict] = []
    for k, v in index.items():
        if t and (t in k or k in t) and abs(len(k) - len(t)) <= 4:
            hits.extend(v)
    return hits[:10]


def _bfs_path(start_ids: set[str], goal_ids: set[str],
              adj: dict[str, list[tuple[str, dict]]],
              max_depth: int = 4) -> list[dict] | None:
    """Shortest edge path from any start node to any goal node."""
    if start_ids & goal_ids:
        return []
    q = deque((s, []) for s in start_ids)
    seen = set(start_ids)
    while q:
        node, path = q.popleft()
        if len(path) >= max_depth:
            continue
        for nxt, edge in adj.get(node, []):
            if nxt in goal_ids:
                return path + [{"from": node, "to": nxt, "edge": edge}]
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, path + [{"from": node, "to": nxt, "edge": edge}]))
    return None


def resolve_relation(directive: str, max_depth: int = 4) -> dict:
    """Main entry point. Returns a structured relationship report."""
    result: dict = {
        "directive": directive,
        "is_relation_query": is_relation_query(directive),
        "entities": [],
        "matched": {},
        "paths": [],
        "evidence": [],
        "confidence": "none",
        "note": "",
    }

    if not result["is_relation_query"]:
        result["note"] = "not a relationship query"
        return result

    terms = extract_entities(directive)
    result["entities"] = terms
    if len(terms) < 2:
        result["note"] = "fewer than 2 entity candidates found"
        return result

    try:
        nodes, links = _load_graph()
    except Exception as exc:
        result["note"] = f"graph load failed: {exc}"
        return result

    index = _build_index(nodes)

    # Take first two terms that actually match something in the graph
    matched: list[tuple[str, list[dict]]] = []
    for t in terms:
        m = _match_entity(t, index)
        if m:
            matched.append((t, m))
        if len(matched) == 2:
            break

    if len(matched) < 2:
        result["note"] = "could not match two entities to graph nodes"
        result["matched"] = {t: [] for t in terms}
        return result

    (a_term, a_nodes), (b_term, b_nodes) = matched
    a_ids = {n["id"] for n in a_nodes}
    b_ids = {n["id"] for n in b_nodes}
    result["matched"] = {
        a_term: [{"id": n["id"], "file": n.get("source_file"), "loc": n.get("source_location")} for n in a_nodes],
        b_term: [{"id": n["id"], "file": n.get("source_file"), "loc": n.get("source_location")} for n in b_nodes],
    }

    # Build adjacency (treat graph as undirected for relationship finding)
    adj: dict[str, list[tuple[str, dict]]] = {}
    for e in links:
        s, t = e.get("source"), e.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, []).append((t, e))
        adj.setdefault(t, []).append((s, e))

    path = _bfs_path(a_ids, b_ids, adj, max_depth=max_depth)
    if path is None:
        # try reverse direction
        path = _bfs_path(b_ids, a_ids, adj, max_depth=max_depth)

    if path is None:
        result["confidence"] = "none"
        result["note"] = f"no path found within depth {max_depth} between {a_term} and {b_term}"
        return result

    result["paths"] = path
    result["evidence"] = [
        {
            "step": i + 1,
            "from": step["from"],
            "to": step["to"],
            "relation": step["edge"].get("relation"),
            "file": step["edge"].get("source_file"),
            "loc": step["edge"].get("source_location"),
            "context": step["edge"].get("context"),
        }
        for i, step in enumerate(path)
    ]
    hops = len(path)
    if hops == 0:
        result["confidence"] = "high"
        result["note"] = f"{a_term} and {b_term} are the same symbol"
    elif hops <= 2:
        result["confidence"] = "high"
        result["note"] = f"{a_term} reaches {b_term} in {hops} hop(s)"
    elif hops <= 4:
        result["confidence"] = "medium"
        result["note"] = f"{a_term} reaches {b_term} in {hops} hop(s)"
    else:
        result["confidence"] = "low"
    return result
