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
