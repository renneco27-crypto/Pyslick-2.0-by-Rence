"""
graphify.py — Query a Python file by description and get back the full
connected code: matched function source + everything it calls (and what
those call), resolved end-to-end within the same file.

Usage:
    from graphify import query, query_json, map_symbols

    # Ask about a function by description
    results = query("myfile.py", "how does parsing work")

    for r in results:
        print(r)              # pretty block: match + all connected code
        print(r.code)         # source of the matched symbol only
        print(r.score)        # relevance score
        print(r.connections)  # list of connected SymbolResult (callees/callers)

    # JSON-serialisable version
    raw = query_json("myfile.py", "extract file and symbol", top_k=3)

    # Full symbol map (no query)
    all_syms = map_symbols("myfile.py")
"""

import ast
import sys
import os
import re
import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# 1. Symbol extraction
# ---------------------------------------------------------------------------

def _build_parent_map(tree: ast.AST) -> dict:
    """Map each node id -> its direct AST parent node."""
    parent_map = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return parent_map


def _extract_symbols(file_path: str, source: str, lines: list[str]) -> dict[str, dict]:
    """
    Parse the file and return a dict of symbol_name -> symbol metadata.
    Each symbol includes its source lines, docstring, calls made inside it,
    and its parent scope.
    """
    tree = ast.parse(source, filename=file_path)
    parent_map = _build_parent_map(tree)

    symbols: dict[str, dict] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        kind = (
            "class" if isinstance(node, ast.ClassDef)
            else "async_function" if isinstance(node, ast.AsyncFunctionDef)
            else "function"
        )

        sig_line = lines[node.lineno - 1].strip()
        end_line = node.end_lineno if hasattr(node, "end_lineno") else max(
            getattr(child, "end_lineno", node.lineno)
            for child in ast.walk(node)
        )

        docstring = ast.get_docstring(node) or ""

        decorators = []
        for dec in getattr(node, "decorator_list", []):
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(dec.attr)

        # Parent scope name
        parent_node = parent_map.get(id(node))
        parent = None
        if isinstance(parent_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parent = parent_node.name

        # Calls made inside this symbol's body
        calls_made: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    calls_made.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    calls_made.add(child.func.attr)

        # All identifiers used (for scoring)
        body_tokens: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                body_tokens.add(child.id)
            elif isinstance(child, ast.Attribute):
                body_tokens.add(child.attr)

        code = "\n".join(lines[node.lineno - 1 : end_line])

        symbols[node.name] = {
            "name": node.name,
            "type": kind,
            "parent": parent,
            "start_line": node.lineno,
            "end_line": end_line,
            "signature": sig_line,
            "docstring": docstring,
            "decorators": decorators,
            "calls_made": calls_made,      # names this fn calls
            "body_tokens": body_tokens,
            "code": code,
        }

    return symbols


# ---------------------------------------------------------------------------
# 2. Call graph
# ---------------------------------------------------------------------------

def _build_call_graph(symbols: dict[str, dict]) -> dict[str, dict]:
    """
    For each symbol, resolve which of its calls_made exist in the same file.
    Adds:
      callees  — symbols this one calls (in-file)
      callers  — symbols that call this one (in-file)
    """
    known = set(symbols.keys())

    for name, sym in symbols.items():
        sym["callees"] = sym["calls_made"] & known
        sym["callers"] = set()

    for name, sym in symbols.items():
        for callee in sym["callees"]:
            symbols[callee]["callers"].add(name)

    return symbols


# ---------------------------------------------------------------------------
# 3. Scoring
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _expand_identifier(name: str) -> list[str]:
    parts = name.replace("-", "_").split("_")
    expanded = []
    for part in parts:
        subs = re.sub(r"([A-Z])", r" \1", part).split()
        expanded.extend(s.lower() for s in subs if s)
    return expanded or [name.lower()]


def _score(query_tokens: list[str], sym: dict) -> float:
    weights = {
        "name":      5.0,   # name match is strongest signal
        "docstring": 3.0,   # docstring describes intent
        "signature": 2.0,   # param names matter
        "callee_names": 2.5, # what it calls is part of its meaning
        "caller_names": 1.5, # what calls it gives context
        "body_tokens": 0.8,  # broad but noisy
        "decorators": 1.0,
    }

    fields: dict[str, list[str]] = {
        "name":        _expand_identifier(sym["name"]),
        "docstring":   _tokenize(sym["docstring"]),
        "signature":   _tokenize(sym["signature"]),
        "callee_names":[t for c in sym.get("callees", set()) for t in _expand_identifier(c)],
        "caller_names":[t for c in sym.get("callers", set()) for t in _expand_identifier(c)],
        "body_tokens": list(sym["body_tokens"]),
        "decorators":  [t for d in sym["decorators"] for t in _expand_identifier(d)],
    }

    score = 0.0
    for token in query_tokens:
        for field_name, tokens in fields.items():
            if not tokens:
                continue
            matches = tokens.count(token)
            if matches:
                score += weights[field_name] * (1 + math.log(matches))
            else:
                partials = sum(1 for t in tokens if token in t or t in token)
                if partials:
                    score += weights[field_name] * 0.35 * partials

    return score


def _score_symbols(question: str, symbols: dict[str, dict]) -> list[tuple[float, dict]]:
    raw_tokens = _tokenize(question)
    expanded   = [w for t in raw_tokens for w in _expand_identifier(t)]
    query_tokens = list(set(raw_tokens + expanded))

    scored = []
    for sym in symbols.values():
        s = _score(query_tokens, sym)
        if s > 0:
            scored.append((s, sym))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# 4. Result type
# ---------------------------------------------------------------------------

@dataclass
class SymbolResult:
    symbol:      dict          # metadata (name, type, parent, lines, sig, doc…)
    score:       float         # relevance score (0 = not matched, higher = better)
    code:        str           # raw source of this symbol
    file:        str           # path to the source file
    connections: list = field(default_factory=list)  # list of connected SymbolResult

    def __repr__(self) -> str:
        sym  = self.symbol
        parent_str = f" (in {sym['parent']})" if sym["parent"] else ""
        header = (
            f"[{sym['type']}] {sym['name']}{parent_str} "
            f"L{sym['start_line']}–{sym['end_line']}  score={self.score:.2f}"
        )
        doc   = f"\n  \"{sym['docstring'][:120]}\"" if sym["docstring"] else ""
        bar   = "═" * min(len(header), 80)

        callees = sym.get("callees", set())
        callers = sym.get("callers", set())
        links = ""
        if callees:
            links += f"\n  calls   → {', '.join(sorted(callees))}"
        if callers:
            links += f"\n  called by <- {', '.join(sorted(callers))}"

        body = f"\n{bar}\n{self.code}\n"

        connected = ""
        if self.connections:
            connected = "\n── connected code ──────────────────────────────────────\n"
            for c in self.connections:
                csym = c.symbol
                clabel = (
                    f"  [{csym['type']}] {csym['name']} "
                    f"L{csym['start_line']}–{csym['end_line']}"
                )
                connected += f"{clabel}\n{'─'*60}\n{c.code}\n\n"

        return f"{header}{doc}{links}{body}{connected}"


# ---------------------------------------------------------------------------
# 5. Connection resolver — walks the call graph to requested depth
# ---------------------------------------------------------------------------

def _resolve_connections(
    sym: dict,
    symbols: dict[str, dict],
    depth: int,
    direction: str,          # "callees", "callers", or "both"
    _visited: set | None = None,
) -> list["SymbolResult"]:
    """
    Recursively collect connected symbols up to `depth` levels.
    direction controls whether we follow callees, callers, or both.
    """
    if depth == 0:
        return []

    _visited = _visited or set()
    _visited.add(sym["name"])

    targets: set[str] = set()
    if direction in ("callees", "both"):
        targets |= sym.get("callees", set())
    if direction in ("callers", "both"):
        targets |= sym.get("callers", set())

    results = []
    for name in sorted(targets):
        if name in _visited or name not in symbols:
            continue
        connected_sym = symbols[name]
        sub = _resolve_connections(connected_sym, symbols, depth - 1, direction, _visited)
        results.append(SymbolResult(
            symbol={k: v for k, v in connected_sym.items()
                    if k not in ("body_tokens", "calls_made")},
            score=0.0,
            code=connected_sym["code"],
            file="",
            connections=sub,
        ))

    return results


# ---------------------------------------------------------------------------
# 6. Public API
# ---------------------------------------------------------------------------

def query(
    file_path: str,
    question: str,
    top_k: int = 5,
    min_score: float = 0.0,
    depth: int = 2,
    direction: str = "both",
) -> list[SymbolResult]:
    """
    Search a Python file by natural language description and return the matching
    symbols with their full source code and connected functions (callees/callers).

    Args:
        file_path:  Path to the .py file to search.
        question:   Natural language query, e.g. "how does parsing work".
        top_k:      Max number of top-level matches to return (default 5).
        min_score:  Drop results below this relevance score (default 0.0).
        depth:      How many call-graph levels to follow for connections (default 2).
                    0 = no connections, 1 = direct calls only, 2 = calls of calls.
        direction:  "callees"  — show what the matched function calls.
                    "callers"  — show what calls the matched function.
                    "both"     — show both (default).

    Returns:
        List of SymbolResult, sorted by relevance descending.
        Each has:
            .code         — source of the matched symbol
            .score        — relevance float
            .symbol       — metadata dict
            .connections  — list of SymbolResult for connected functions

    Example:
        results = query("graphify_sitter.py", "extract file and symbol")
        for r in results:
            print(r)           # full pretty output
            print(r.code)      # matched function source only
            for c in r.connections:
                print(c.code)  # each connected function's source
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    lines   = source.splitlines()
    symbols = _extract_symbols(file_path, source, lines)
    symbols = _build_call_graph(symbols)
    scored  = _score_symbols(question, symbols)

    results = []
    for score, sym in scored[:top_k]:
        if score < min_score:
            continue
        connections = _resolve_connections(sym, symbols, depth=depth, direction=direction)
        clean_sym   = {k: v for k, v in sym.items() if k not in ("body_tokens", "calls_made")}
        results.append(SymbolResult(
            symbol=clean_sym,
            score=round(score, 4),
            code=sym["code"],
            file=file_path,
            connections=connections,
        ))

    return results


def query_json(
    file_path: str,
    question: str,
    top_k: int = 5,
    min_score: float = 0.0,
    depth: int = 2,
    direction: str = "both",
) -> list[dict]:
    """
    Same as query() but returns plain JSON-serialisable dicts.
    Connections are nested under the 'connections' key recursively.
    """
    def _result_to_dict(r: SymbolResult) -> dict:
        return {
            "name":        r.symbol["name"],
            "type":        r.symbol["type"],
            "parent":      r.symbol["parent"],
            "start_line":  r.symbol["start_line"],
            "end_line":    r.symbol["end_line"],
            "signature":   r.symbol["signature"],
            "docstring":   r.symbol["docstring"],
            "decorators":  r.symbol["decorators"],
            "callees":     sorted(r.symbol.get("callees", set())),
            "callers":     sorted(r.symbol.get("callers", set())),
            "score":       r.score,
            "code":        r.code,
            "file":        r.file,
            "connections": [_result_to_dict(c) for c in r.connections],
        }

    results = query(file_path, question, top_k=top_k, min_score=min_score,
                    depth=depth, direction=direction)
    return [_result_to_dict(r) for r in results]


def map_symbols(file_path: str) -> list[dict]:
    """
    Return the full symbol map for a file with call graph resolved.
    No query filtering — every symbol with its callees and callers.

    Useful for building an index or understanding a file's structure.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()

    lines   = source.splitlines()
    symbols = _extract_symbols(file_path, source, lines)
    symbols = _build_call_graph(symbols)

    return [
        {
            "name":       s["name"],
            "type":       s["type"],
            "parent":     s["parent"],
            "start_line": s["start_line"],
            "end_line":   s["end_line"],
            "signature":  s["signature"],
            "docstring":  s["docstring"],
            "decorators": s["decorators"],
            "callees":    sorted(s["callees"]),
            "callers":    sorted(s["callers"]),
            "code":       s["code"],
        }
        for s in sorted(symbols.values(), key=lambda x: x["start_line"])
    ]


# ---------------------------------------------------------------------------
# 7. Codebase-Wide Multi-Language Graph Extraction & Data Flow Engine
# ---------------------------------------------------------------------------

import json
from pathlib import Path
from collections import defaultdict, deque

GRAPH_PATH = os.environ.get(
    "GRAPHIFY_GRAPH",
    os.path.join("graphify-out", "graph.json"),
)


def _get_clean_code_snippet(file_path: str, start_line: int, end_line: int) -> str:
    """Read snippet from file safely."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        s = max(0, start_line - 1)
        e = min(len(lines), end_line)
        return "".join(lines[s:e])
    except Exception:
        return ""


def extract_codebase_graph(root: str = ".", out_dir: str = "graphify-out",
                           quiet: bool = False) -> dict:
    """Build or refresh the codebase graph via the standalone `graphify` CLI.

    The actual extraction (AST parsing across 25+ languages, confidence
    tagging, community detection) is done by the installed `graphifyy`
    package. This function shells out to it and returns the resulting
    graph.json as a dict.

    Kept as a stable entry point so existing callers
    (find_nearest_nodes.py, relations.py) don't need to change.

    Requires: `graphify` on PATH (pip install graphifyy).
    """
    import subprocess as _sp
    target = os.path.abspath(root)
    try:
        r = _sp.run(
            ["graphify", "extract", target, "--code-only"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if not quiet:
            out = (r.stdout or "") + (r.stderr or "")
            for line in out.splitlines():
                if line.strip():
                    print(line)
        if r.returncode != 0:
            if not quiet:
                print("graphify extract exited with code %d" % r.returncode)
    except FileNotFoundError:
        if not quiet:
            print("graphify CLI not found on PATH -- install with: pip install graphifyy")
    except _sp.TimeoutExpired:
        if not quiet:
            print("graphify extract timed out after 300s")

    return load_codebase_graph(root)



def load_codebase_graph(root: str = ".") -> dict:
    """Load pre-built graph or extract automatically if missing."""
    abs_root = os.path.abspath(root)
    gpath = os.path.join(abs_root, "graphify-out", "graph.json")
    if not os.path.isfile(gpath):
        return extract_codebase_graph(abs_root, quiet=True)
    try:
        with open(gpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if len(data.get("nodes", [])) < 5:
            return extract_codebase_graph(abs_root, quiet=True)
        return data
    except Exception:
        return extract_codebase_graph(abs_root, quiet=True)


def query_codebase_graph(query_str: str, root: str = ".", top_k: int = 5, depth: int = 2) -> list[dict]:
    """
    Search functions and classes across the entire codebase graph by name or description.
    Incorporates:
      1. Domain synonym & vocabulary expansion (synonyms.py & graphify vocab)
      2. Token & exact AST symbol matching
      3. BM25 comments & docstrings index snapping (text_index.py)
      4. RapidFuzz Nearest Nodes fallback when exact terms aren't found
    """
    abs_root = os.path.abspath(root)
    graph = load_codebase_graph(abs_root)
    nodes = graph.get("nodes", [])
    func_nodes = [n for n in nodes if n.get("type") != "file"]

    if not func_nodes:
        return []

    # 1. Expand query with static synonyms & domain vocabulary
    expanded_terms = [query_str]
    try:
        from synonyms import _static_terms
        terms = _static_terms(query_str)
        if terms:
            expanded_terms.extend(terms)
    except Exception:
        pass

    try:
        from find_nearest_nodes import load_graphify_vocab, expand_query_with_vocab
        g_vocab = load_graphify_vocab()
        v_exp = expand_query_with_vocab(query_str, g_vocab)
        if v_exp != query_str:
            expanded_terms.extend(v_exp.split())
    except Exception:
        pass

    combined_query = " ".join(set(expanded_terms))
    q_tokens = _tokenize(combined_query)
    expanded_q = [w for t in q_tokens for w in _expand_identifier(t)]
    all_q_tokens = list(set(q_tokens + expanded_q))

    scored = []
    seen_ids = set()

    for node in func_nodes:
        name_tokens = _expand_identifier(node.get("label", ""))
        doc_tokens = _tokenize(node.get("docstring", ""))
        sig_tokens = _tokenize(node.get("signature", ""))
        file_tokens = _tokenize(node.get("source_file", ""))

        score = 0.0
        # Exact name match
        lbl_lower = node.get("label", "").lower()
        q_lower = query_str.lower().strip()
        if q_lower == lbl_lower:
            score += 40.0
        elif q_lower in lbl_lower or lbl_lower in q_lower:
            score += 15.0

        for qt in all_q_tokens:
            if qt in name_tokens:
                score += 8.0
            elif any(qt in nt or nt in qt for nt in name_tokens):
                score += 3.0
            if qt in doc_tokens:
                score += 4.0
            if qt in sig_tokens:
                score += 3.0
            if qt in file_tokens:
                score += 2.0

        if score > 0:
            scored.append((score, node, "AST Symbol Match"))
            seen_ids.add(node["id"])

    # 2. RapidFuzz Nearest Nodes Fallback (when exact term isn't found or results are thin)
    if len(scored) == 0:
        try:
            from rapidfuzz import process, fuzz
            labels = [n.get("label", "") for n in func_nodes]
            fuzzy_hits = process.extract(
                query_str,
                labels,
                scorer=fuzz.WRatio,
                limit=top_k * 2,
            )
            for match_label, fuzz_score, idx in fuzzy_hits:
                if fuzz_score >= 50.0:
                    node = func_nodes[idx]
                    if node["id"] not in seen_ids:
                        scaled_score = (fuzz_score / 10.0)
                        scored.append((scaled_score, node, f"Nearest Node Fuzzy Match ({fuzz_score:.1f}%)"))
                        seen_ids.add(node["id"])
        except Exception:
            pass

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for item in scored[:top_k]:
        score, node = item[0], item[1]
        match_reason = item[2] if len(item) > 2 else "AST Match"
        fpath = os.path.join(abs_root, node.get("source_file", ""))
        
        start_line = node.get("start_line")
        if start_line is None:
            loc = node.get("source_location", "")
            if loc.startswith("L") and loc[1:].isdigit():
                start_line = int(loc[1:])
            else:
                start_line = 1
        end_line = node.get("end_line", start_line + 20)

        code = _get_clean_code_snippet(fpath, start_line, end_line)

        callees = [c.split("::")[-1] for c in node.get("callees", [])]
        callers = [c.split("::")[-1] for c in node.get("callers", [])]

        results.append({
            "name": node.get("label", ""),
            "type": node.get("type", node.get("file_type", "symbol")),
            "source_file": node.get("source_file", ""),
            "source_location": node.get("source_location", f"L{start_line}"),
            "start_line": start_line,
            "end_line": end_line,
            "signature": node.get("signature", ""),
            "docstring": node.get("docstring", ""),
            "score": round(score, 2),
            "match_reason": match_reason,
            "callees": callees,
            "callers": callers,
            "code": code,
        })

    return results


def trace_data_flow(source_term: str, target_term: str, root: str = ".", max_depth: int = 6) -> dict:
    """
    Trace how data and function calls flow between source_term and target_term
    using BFS shortest-path over the AST call-graph.
    """
    abs_root = os.path.abspath(root)
    graph = load_codebase_graph(abs_root)
    nodes = graph.get("nodes", [])
    links = graph.get("links", [])

    # Index nodes by id, label, norm_label, and source_file
    idx = defaultdict(list)
    for n in nodes:
        nid = n.get("id", "").lower()
        lbl = n.get("label", "").lower()
        sf = n.get("source_file", "").lower()
        if nid: idx[nid].append(n)
        if lbl: idx[lbl].append(n)
        if sf: idx[sf].append(n)

    # Match source and target
    s_hits = idx.get(source_term.lower(), [])
    t_hits = idx.get(target_term.lower(), [])

    if not s_hits:
        for k, v in idx.items():
            if source_term.lower() in k:
                s_hits.extend(v)
                if len(s_hits) > 5:
                    break
    if not t_hits:
        for k, v in idx.items():
            if target_term.lower() in k:
                t_hits.extend(v)
                if len(t_hits) > 5:
                    break

    if not s_hits or not t_hits:
        return {
            "source": source_term,
            "target": target_term,
            "found": False,
            "message": f"Could not find matching graph nodes for '{source_term}' or '{target_term}'.",
            "steps": [],
        }

    s_ids = {n["id"] for n in s_hits}
    t_ids = {n["id"] for n in t_hits}

    # Direct connection check
    if s_ids & t_ids:
        return {
            "source": source_term,
            "target": target_term,
            "found": True,
            "hops": 0,
            "steps": [{
                "step": 1,
                "from": list(s_ids)[0],
                "from_name": list(s_ids)[0].split("::")[-1],
                "to": list(s_ids)[0],
                "to_name": list(s_ids)[0].split("::")[-1],
                "file": "",
                "loc": "Same Component",
            }],
        }

    # Build adjacency (bidirectional so we can find any call relation path)
    adj = defaultdict(list)
    for l in links:
        s, t = l.get("source"), l.get("target")
        if s and t:
            adj[s].append((t, l, "calls"))
            adj[t].append((s, l, "called_by"))

    # BFS for shortest path
    q = deque((s, []) for s in s_ids)
    visited = set(s_ids)
    path = None

    while q:
        curr, p = q.popleft()
        if len(p) >= max_depth:
            continue
        for nxt, edge, dir_type in adj.get(curr, []):
            if nxt in t_ids:
                path = p + [(curr, nxt, edge, dir_type)]
                break
            if nxt not in visited:
                visited.add(nxt)
                q.append((nxt, p + [(curr, nxt, edge, dir_type)]))
        if path:
            break

    if not path:
        return {
            "source": source_term,
            "target": target_term,
            "found": False,
            "message": f"No call flow path found within {max_depth} hops between '{source_term}' and '{target_term}'.",
            "steps": [],
        }

    steps = []
    for i, (frm, to, edge, dir_type) in enumerate(path):
        frm_clean = frm.split("::")[-1]
        to_clean = to.split("::")[-1]
        arrow = "calls" if dir_type == "calls" else "is called by"
        steps.append({
            "step": i + 1,
            "from": frm,
            "from_name": frm_clean,
            "to": to,
            "to_name": to_clean,
            "direction": arrow,
            "file": edge.get("source_file", ""),
            "loc": edge.get("source_location", ""),
        })

    return {
        "source": source_term,
        "target": target_term,
        "found": True,
        "hops": len(path),
        "steps": steps,
    }


def _build_id_index(nodes):
    """Map node id -> node dict for quick lookup."""
    idx = {}
    for n in nodes:
        nid = n.get("id")
        if nid:
            idx[nid] = n
    return idx


def _edges_of(graph, node_id, direction):
    """Return list of (other_node_id, source_file, source_location) for calls edges."""
    out = []
    for l in graph.get("links", []) or []:
        if l.get("relation") != "calls":
            continue
        if direction == "in" and l.get("target") == node_id:
            out.append((l.get("source"), l.get("source_file"), l.get("source_location")))
        elif direction == "out" and l.get("source") == node_id:
            out.append((l.get("target"), l.get("source_file"), l.get("source_location")))
    return out


def _label_for(node_id, id_index):
    n = id_index.get(node_id)
    if n:
        return n.get("label") or node_id
    return node_id


def _sanitize_mermaid(name):
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)



def show_callers(symbol_name: str, root: str = "."):
    """Display all functions that call symbol_name."""
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    RST = "\033[0m"

    graph = load_codebase_graph(root)
    nodes = graph.get("nodes", [])
    idx = _build_id_index(nodes)

    matched = [n for n in nodes if (n.get("label") or "").lower() == symbol_name.lower()]
    if not matched:
        matched = [n for n in nodes if symbol_name.lower() in (n.get("label") or "").lower()]
    if not matched:
        print("\n%sNo symbol matching '%s' found in call graph.%s\n" % (DIM, symbol_name, RST))
        return

    print("\n%s%s== Callers of '%s' (Who calls this function) ==%s" % (BOLD, CYAN, symbol_name, RST))
    for node in matched:
        nid = node.get("id")
        print("\n%s[%s] %s%s %s(%s:%s)%s" % (
            BOLD, node.get("type", "func"), node.get("label"),
            RST, DIM, node.get("source_file"), node.get("source_location"), RST))
        incoming = _edges_of(graph, nid, "in")
        if not incoming:
            print("  %sNo callers found in call graph.%s" % (DIM, RST))
        else:
            print("  Called by (%d):" % len(incoming))
            for other_id, sfile, sloc in incoming:
                label = _label_for(other_id, idx)
                print("    %s<-%s %s%s  %s(from %s:%s)%s" % (
                    GREEN, RST, BOLD, label, DIM, sfile, sloc, RST))
    print()




def show_callees(symbol_name: str, root: str = "."):
    """Display all functions called by symbol_name."""
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    RST = "\033[0m"

    graph = load_codebase_graph(root)
    nodes = graph.get("nodes", [])
    idx = _build_id_index(nodes)

    matched = [n for n in nodes if (n.get("label") or "").lower() == symbol_name.lower()]
    if not matched:
        matched = [n for n in nodes if symbol_name.lower() in (n.get("label") or "").lower()]
    if not matched:
        print("\n%sNo symbol matching '%s' found in call graph.%s\n" % (DIM, symbol_name, RST))
        return

    print("\n%s%s== Callees of '%s' (What this function calls) ==%s" % (BOLD, CYAN, symbol_name, RST))
    for node in matched:
        nid = node.get("id")
        print("\n%s[%s] %s%s %s(%s:%s)%s" % (
            BOLD, node.get("type", "func"), node.get("label"),
            RST, DIM, node.get("source_file"), node.get("source_location"), RST))
        outgoing = _edges_of(graph, nid, "out")
        if not outgoing:
            print("  %sNo downstream function calls recorded.%s" % (DIM, RST))
        else:
            print("  Calls (%d):" % len(outgoing))
            for other_id, sfile, sloc in outgoing:
                label = _label_for(other_id, idx)
                print("    %s->%s %s%s  %s(at %s:%s)%s" % (
                    GREEN, RST, BOLD, label, DIM, sfile, sloc, RST))
    print()




def show_graph(symbol_name: str, root: str = ".", fmt: str = "ascii",
               depth: int = 1, direction: str = "both"):
    """Render callers and callees of a symbol as a tree (ascii) or flowchart (mermaid)."""
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    RST = "\033[0m"

    graph = load_codebase_graph(root)
    nodes = graph.get("nodes", [])
    idx = _build_id_index(nodes)

    matched = [n for n in nodes if (n.get("label") or "").lower() == symbol_name.lower()]
    if not matched:
        matched = [n for n in nodes if symbol_name.lower() in (n.get("label") or "").lower()]
    if not matched:
        print("No symbol matching '%s' found in call graph." % symbol_name)
        return

    node = matched[0]
    nid = node.get("id")
    root_label = node.get("label") or symbol_name

    def expand(current_id, kind, level, seen):
        if level > depth:
            return []
        out = []
        for other_id, _, _ in _edges_of(graph, current_id, kind):
            if other_id in seen:
                continue
            seen.add(other_id)
            out.append((_label_for(other_id, idx), level))
            out.extend(expand(other_id, kind, level + 1, seen))
        return out

    if fmt == "mermaid":
        print("```mermaid")
        print("flowchart LR")
        emitted = 0
        if direction in ("in", "both"):
            for other_id, _, _ in _edges_of(graph, nid, "in"):
                print("  %s --> %s" % (_sanitize_mermaid(_label_for(other_id, idx)),
                                       _sanitize_mermaid(root_label)))
                emitted += 1
        if direction in ("out", "both"):
            for other_id, _, _ in _edges_of(graph, nid, "out"):
                print("  %s --> %s" % (_sanitize_mermaid(root_label),
                                       _sanitize_mermaid(_label_for(other_id, idx))))
                emitted += 1
        if emitted == 0:
            print("  %s" % _sanitize_mermaid(root_label))
        print("```")
        return

    print("")
    print("%s%s%s" % (BOLD, CYAN, root_label))
    print("%s(%s:%s)%s" % (DIM, node.get("source_file", "?"),
                          node.get("source_location", "?"), RST))
    print("")

    if direction in ("in", "both"):
        incoming = _edges_of(graph, nid, "in")
        print("%sIncoming (callers)%s: %d" % (BOLD, RST, len(incoming)))
        if not incoming:
            print("  (none)")
        else:
            for other_id, _, _ in incoming:
                lbl = _label_for(other_id, idx)
                print("  <- %s" % lbl)
                for deeper, lvl in expand(other_id, "in", 2, {other_id, nid}):
                    print("  %s<- %s" % ("  " * lvl, deeper))
        print("")

    if direction in ("out", "both"):
        outgoing = _edges_of(graph, nid, "out")
        print("%sOutgoing (callees)%s: %d" % (BOLD, RST, len(outgoing)))
        if not outgoing:
            print("  (none)")
        else:
            for other_id, _, _ in outgoing:
                lbl = _label_for(other_id, idx)
                print("  -> %s" % lbl)
                for deeper, lvl in expand(other_id, "out", 2, {other_id, nid}):
                    print("  %s-> %s" % ("  " * lvl, deeper))
        print()



def show_stats(root: str = "."):
    """Print overall graph statistics (reads graphifyy's links array)."""
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    DIM = "\033[2m"
    RST = "\033[0m"

    graph = load_codebase_graph(root)
    nodes = graph.get("nodes", [])
    links = graph.get("links", []) or []
    func_nodes = [n for n in nodes if n.get("file_type") != "file" and n.get("_callable")]

    call_links = [l for l in links if l.get("relation") == "calls"]

    # In-degree map for God-Nodes: count incoming call edges per target.
    in_degree = {}
    for l in call_links:
        t = l.get("target")
        if t:
            in_degree[t] = in_degree.get(t, 0) + 1

    by_id = {n.get("id"): n for n in nodes if n.get("id")}

    print("")
    print("%s%s+==============================================================+%s" % (BOLD, CYAN, RST))
    print("%s%s|              PySlick AST Call-Graph Statistics               |%s" % (BOLD, CYAN, RST))
    print("%s%s+==============================================================+%s" % (BOLD, CYAN, RST))
    print("  * Source Files Indexed:       %s%d%s" % (BOLD, graph.get("total_files", len(set(n.get("source_file") for n in nodes if n.get("source_file")))), RST))
    print("  * Functions / Classes Mapped: %s%d%s" % (BOLD, len(func_nodes), RST))
    print("  * Call Connections Resolved:  %s%d%s" % (BOLD, len(call_links), RST))
    print("  * Total Nodes / Edges:        %s%d / %d%s" % (BOLD, len(nodes), len(links), RST))

    # Top 10 by in-degree.
    ranked = sorted(in_degree.items(), key=lambda kv: kv[1], reverse=True)[:10]
    print("")
    print("%sTop Centrality Functions (Most Called God-Nodes):%s" % (BOLD, RST))
    shown = 0
    for nid, cnt in ranked:
        if cnt <= 0:
            continue
        n = by_id.get(nid)
        label = (n.get("label") if n else None) or nid
        sfile = (n.get("source_file") if n else None) or "?"
        print("  %s*%s %s%s%s %s(%s)%s -- %d callers" % (GREEN, RST, BOLD, label, RST, DIM, sfile, RST, cnt))
        shown += 1
    if shown == 0:
        print("  %s(none -- no calls edges in graph)%s" % (DIM, RST))
    print("")


def cli_main(args: list[str] | None = None):
    """
    Main CLI router for `pyslick graphify`.
    Supports extract, query, flow, callers, callees, stats, and file-level queries.
    """
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    DIM = "\033[2m"
    RST = "\033[0m"

    args = args if args is not None else sys.argv[1:]

    if not args or args[0] in ("--help", "-h", "help"):
        print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════════════════════════════╗
║                   pyslick graphify — Function-to-Function Flow Mapper                   ║
║    Comprehend data flows, function hierarchies & caller/callee relations     ║
╚══════════════════════════════════════════════════════════════════════════════╝{RST}

{BOLD}{YELLOW}⚠ CRITICAL RULE FOR AI AGENTS & DEVELOPERS:{RST}
  {BOLD}Never ever scan the entire codebase line-by-line!{RST}
  Use {CYAN}pyslick graphify{RST} to instantly know how data flows and which function calls
  which function across the codebase with exact caller/callee links and zero hallucinations.

{BOLD}Usage:{RST}
  {GREEN}pyslick graphify extract [path]{RST}
      Index/refresh the AST call graph of all source files into graphify-out/graph.json.
      
  {GREEN}pyslick graphify query "<symbol_or_question>"{RST}  (or: {GREEN}pyslick graphify "<query>"{RST})
      Search for functions across the entire repository. Displays signature, source code,
      who calls it (callers), and what it calls (callees).

  {GREEN}pyslick graphify <file> [query]{RST}
      Inspect functions and caller/callee connections inside a specific file (.py, .ts, .tsx, etc.).

  {GREEN}pyslick graphify flow <from_func> <to_func>{RST}
      Trace the step-by-step data flow and function call chain connecting two components.

  {GREEN}pyslick graphify callers <func_name>{RST}
      List all functions and files in the codebase that call the given function.

  {GREEN}pyslick graphify callees <func_name>{RST}
      List all functions called by the given function.

  {GREEN}pyslick graphify stats{RST}
      Show total indexed functions, call links, and top connected God-Node functions.
""")
        return

    sub = args[0]

    if sub in ("extract", "build", "index"):
        target_dir = args[1] if len(args) > 1 and not args[1].startswith("-") else "."
        extract_codebase_graph(target_dir, quiet=False)
        return

    elif sub in ("stats", "summary", "overview"):
        show_stats(".")
        return

    elif sub == "graph":
        # pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]
        rest = args[1:]
        if not rest:
            print("Usage: pyslick graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]")
            return
        fmt = "ascii"
        depth = 1
        direction = "both"
        positional = []
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--format" and i + 1 < len(rest):
                fmt = rest[i + 1]; i += 2
            elif a.startswith("--format="):
                fmt = a.split("=", 1)[1]; i += 1
            elif a == "--depth" and i + 1 < len(rest):
                try: depth = int(rest[i + 1])
                except ValueError: pass
                i += 2
            elif a.startswith("--depth="):
                try: depth = int(a.split("=", 1)[1])
                except ValueError: pass
                i += 1
            elif a == "--direction" and i + 1 < len(rest):
                direction = rest[i + 1]; i += 2
            elif a.startswith("--direction="):
                direction = a.split("=", 1)[1]; i += 1
            else:
                positional.append(a); i += 1
        if not positional:
            print("Usage: pyslick graphify graph <func> [--format=...] [--depth N] [--direction in|out|both]")
            return
        show_graph(positional[0], root=".", fmt=fmt, depth=depth, direction=direction)
        return

    elif sub in ("callers", "who-calls", "incoming"):
        if len(args) < 2:
            print(f"Error: graphify callers requires <function_name>")
            return
        show_callers(args[1], ".")
        return

    elif sub in ("callees", "calls", "outgoing"):
        if len(args) < 2:
            print(f"Error: graphify callees requires <function_name>")
            return
        show_callees(args[1], ".")
        return

    elif sub in ("flow", "trace", "connect", "path"):
        if len(args) < 3:
            # Check if arguments are in quotes e.g. pyslick graphify flow "how does A connect with B"
            joined = " ".join(args[1:])
            from relations import extract_entities
            ents = extract_entities(joined)
            if len(ents) >= 2:
                s_term, t_term = ents[0], ents[1]
            else:
                print(f"Error: graphify flow requires <from_function> <to_function>")
                return
        else:
            s_term, t_term = args[1], args[2]

        print(f"\n{BOLD}{CYAN}━━  Data-Flow & Call-Chain Path: '{s_term}' → '{t_term}'  {RST}")
        flow = trace_data_flow(s_term, t_term, ".")
        if not flow.get("found"):
            print(f"  {DIM}{flow.get('message', 'No path found.')}{RST}\n")
            return

        print(f"  {GREEN}✓ Connected in {flow.get('hops', len(flow.get('steps', [])))} hop(s):{RST}\n")
        for st in flow.get("steps", []):
            print(f"  {BOLD}Step {st['step']}:{RST} {CYAN}{st['from_name']}(){RST} calls {GREEN}{st['to_name']}(){RST} {DIM}in {st['file']}:{st['loc']}{RST}")
        print()
        return

    elif os.path.isfile(sub):
        # File-specific query
        file_path = sub
        q = " ".join(args[1:]) if len(args) > 1 else ""
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".py":
            if q:
                results = query(file_path, q)
                for r in results:
                    print(r)
            else:
                syms = map_symbols(file_path)
                print(f"\n{BOLD}{CYAN}Symbols in {file_path}:{RST}")
                for s in syms:
                    callees_str = f" → calls: {', '.join(s['callees'])}" if s['callees'] else ""
                    print(f"  [{s['type']}] {BOLD}{s['name']}{RST} L{s['start_line']}-L{s['end_line']}{callees_str}")
                print()
        else:
            try:
                from repomap import query_multilang
                if q:
                    results = query_multilang(file_path, q)
                    for r in results:
                        print(r)
                else:
                    from repomap import list_definitions
                    defs = list_definitions(file_path)
                    print(f"\n{BOLD}{CYAN}Definitions in {file_path}:{RST}")
                    for d in defs:
                        print(f"  [{d['kind']}] {BOLD}{d['name']}{RST} L{d['start_line']}-L{d['end_line']}")
                    print()
            except Exception as e:
                print(f"Error reading file symbols: {e}")
        return

    elif sub in ("query", "search", "find"):
        q_str = " ".join(args[1:])
        if not q_str:
            print("Error: graphify query requires a symbol name or question.")
            return
        _run_codebase_query_and_print(q_str)
        return

    else:
        # Direct string query (e.g. pyslick graphify "createBrowserSupabase" or pyslick graphify "how does cache work")
        q_str = " ".join(args)
        _run_codebase_query_and_print(q_str)


def _run_codebase_query_and_print(query_str: str):
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    DIM = "\033[2m"
    RST = "\033[0m"

    # 1. Codebase Overview Interception ("what does this codebase do")
    try:
        from repomap import is_overview_query, get_codebase_overview
        if is_overview_query(query_str):
            ov = get_codebase_overview(".")
            print(f"\n{BOLD}{CYAN}━━  Codebase Call-Graph & Architecture Overview  {RST}\n")
            print(ov.get("overview_text", ""))
            return
    except Exception:
        pass

    # 2. Relationship Query Interception ("how does X connect to Y")
    try:
        from relations import is_relation_query, resolve_relation
        if is_relation_query(query_str):
            rel = resolve_relation(query_str)
            if rel.get("paths"):
                print(f"\n{BOLD}{CYAN}━━  Call-Graph Relation Path: {rel['directive']}  {RST}")
                print(f"  {GREEN}✓ {rel.get('note', 'Path resolved')}{RST}\n")
                for ev in rel.get("evidence", []):
                    f_name = ev['from'].split('::')[-1]
                    t_name = ev['to'].split('::')[-1]
                    print(f"  {BOLD}Step {ev['step']}:{RST} {CYAN}{f_name}(){RST} calls {GREEN}{t_name}(){RST} {DIM}in {ev.get('file', '')}:{ev.get('loc', '')}{RST}")
                print()
                return
    except Exception:
        pass

    # 3. Compound Query Decomposition ("where is supabase client and how is cache registered")
    subqueries = [query_str]
    try:
        from decompose import decompose
        dec_clauses = decompose(query_str)
        if len(dec_clauses) > 1:
            subqueries = dec_clauses
            print(f"\n{DIM}[graphify] Decomposed into {len(subqueries)} subqueries: {subqueries}{RST}")
    except Exception:
        pass

    all_results = []
    seen_ids = set()

    for sq in subqueries:
        res = query_codebase_graph(sq, top_k=4)
        for r in res:
            rid = f"{r['source_file']}::{r['name']}"
            if rid not in seen_ids:
                seen_ids.add(rid)
                all_results.append((sq, r))

    if not all_results:
        print(f"\n{DIM}No functions/symbols matching '{query_str}' found in call graph.{RST}\n")
        return

    print(f"\n{BOLD}{CYAN}━━  Call-Graph Search Results ({len(all_results)} match(es))  {RST}\n")
    for sq, r in all_results:
        reason_badge = f" {YELLOW}[{r.get('match_reason', 'AST Match')}]{RST}" if r.get('match_reason') else ""
        header = f"[{r['type']}] {r['name']} ({r['source_file']}:{r['source_location']})  score={r['score']}{reason_badge}"
        bar = "═" * min(len(header), 80)
        print(f"{BOLD}{header}{RST}")
        if len(subqueries) > 1:
            print(f"  {DIM}Subquery: \"{sq}\"{RST}")
        if r.get("signature"):
            print(f"  {DIM}{r['signature']}{RST}")
        if r.get("docstring"):
            print(f"  {DIM}\"{r['docstring'][:140]}\"{RST}")
        if r.get("callers"):
            print(f"  {GREEN}called by ←{RST} {', '.join(r['callers'][:6])}")
        if r.get("callees"):
            print(f"  {CYAN}calls     →{RST} {', '.join(r['callees'][:6])}")
        print(f"{bar}")
        print(f"{r['code']}\n")


if __name__ == "__main__":
    cli_main(sys.argv[1:])
