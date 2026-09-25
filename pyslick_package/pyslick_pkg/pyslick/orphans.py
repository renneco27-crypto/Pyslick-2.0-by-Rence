"""
Dead-code detection: find functions/classes whose name appears only at its
own definition â€” no calls, imports, or references anywhere in the project.
"""
from collections import Counter, defaultdict
from pathlib import Path
import ast
import re
import sys

SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".next",
    ".venv", "venv", "graphify-out", ".pyslick", ".pyslick_context",
    "coverage", ".cache", ".turbo", "models",
}
PY_EXTS = {".py"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}


def collect_files(root="."):
    out = []
    for p in Path(root).rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in PY_EXTS | JS_EXTS:
            out.append(p)
    return out


def _read(p):
    try:
        return p.read_text(encoding="utf-8-sig", errors="strict")
    except Exception:
        return ""


def _strip_for_counting(text):
    t = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
    t = re.sub(r"'''.*?'''", "", t, flags=re.DOTALL)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.DOTALL)
    t = re.sub(r"#.*", "", t)
    t = re.sub(r"//.*", "", t)
    t = re.sub(r'"(?:[^"\\]|\\.)*"', '""', t)
    t = re.sub(r"'(?:[^'\\]|\\.)*'", "''", t)
    return t


def extract_python_defs(path, text):
    try:
        tree = ast.parse(text, filename=str(path))
    except Exception:
        return []
    defs = []
    exported = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__":
                    try:
                        exported |= set(ast.literal_eval(node.value))
                    except Exception:
                        pass

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                defs.append((child.name, child.lineno,
                             child.name in exported))
            elif isinstance(child, ast.ClassDef):
                walk(child)

    walk(tree)
    return defs


def extract_js_defs(text):
    patterns = [
        (r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", True),
        (r"^\s*export\s+(?:default\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)", True),
        (r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", False),
        (r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", False),
    ]
    defs = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat, is_export in patterns:
            m = re.match(pat, line)
            if m:
                defs.append((m.group(1), i, is_export))
                break
    return defs


def _graph_pass(root="."):
    """Read graphify-out/graph.json and return what the call graph knows.

    Returns dict with:
      - known_labels: set of every symbol label present in the graph
      - has_incoming: set of labels with at least one incoming edge of
        relation calls/imports/imports_from/references
      - has_outgoing: set of labels with at least one outgoing edge
        (a function that calls something is at least doing work)
    """
    import json
    from pathlib import Path
    graph_path = Path(root) / "graphify-out" / "graph.json"
    if not graph_path.exists():
        return {"known_labels": set(), "has_incoming": set(), "has_outgoing": set()}

    try:
        data = json.loads(graph_path.read_text(encoding="utf-8-sig", errors="strict"))
    except Exception:
        return {"known_labels": set(), "has_incoming": set(), "has_outgoing": set()}

    nodes = data.get("nodes", [])
    links = data.get("links", []) or data.get("edges", [])

    # Build a name-set per node id: every identifier form the node is
    # known by, so links can be matched against AST names without
    # relying on graphify's label format.
    def names_of(node):
        names = set()
        for raw in (node.get("label"), node.get("name"), node.get("id")):
            if not isinstance(raw, str) or not raw.strip():
                continue
            s = raw.strip()
            if s.endswith("()"):
                s = s[:-2]
            # split on separators to get the tail tokens
            for sep in ("::", ".", ":", "/", "\\"):
                if sep in s:
                    s = s.rsplit(sep, 1)[-1]
            if not s:
                continue
            names.add(s)
            names.add(s.lower())
            names.add(s + "()")
            # also every suffix after each underscore boundary
            parts = s.split("_")
            for i in range(len(parts)):
                tail = "_".join(parts[i:])
                if tail:
                    names.add(tail)
                    names.add(tail.lower())
                    names.add(tail + "()")
        return names

    names_by_id = {n.get("id"): names_of(n) for n in nodes if n.get("id")}
    known_labels = set()
    for s in names_by_id.values():
        known_labels |= s

    incoming_relations = {"calls", "imports", "imports_from", "references", "inherits"}
    has_incoming = set()
    has_outgoing = set()

    for link in links:
        rel = link.get("relation")
        src_set = names_by_id.get(link.get("source"), set())
        tgt_set = names_by_id.get(link.get("target"), set())
        if rel in incoming_relations:
            has_incoming |= tgt_set
        if rel in ("calls", "imports", "imports_from"):
            has_outgoing |= src_set

    return {
        "known_labels": known_labels,
        "has_incoming": has_incoming,
        "has_outgoing": has_outgoing,
    }

def _graph_has(container, name):
    """True if name appears in container, trying both bare and name() forms."""
    if not name:
        return False
    return (name in container) or ((name + "()") in container)


def find_orphans(root="."):
    """Find functions/classes defined but never referenced.

    Three signals combined:
      1. stripped-pass count -- comments/docstrings/strings removed
      2. raw-pass count -- nothing removed (catches dynamic-dispatch strings)
      3. graph pass -- graphify-out/graph.json incoming + outgoing edges

    Result buckets (o['flagged'] value):
      None                          -- true orphan: text says once, graph
                                       has no incoming edge for this name.
      'possibly_dynamic_dispatch'   -- text says once but raw mentions exist.
      'graph_orphan'                -- text says once, raw once, graph has
                                       no edge at all (no incoming, no
                                       outgoing). Strongest dead-code candidate.
      'graph_confirms_used'         -- text says once but graph has incoming
                                       calls/imports edges for this name.
                                       Framework / dispatch. Do not delete.
    """
    files = collect_files(root)

    counts_stripped = defaultdict(Counter)
    counts_raw = defaultdict(Counter)
    for f in files:
        raw = _read(f)
        stripped = _strip_for_counting(raw)
        for name in re.findall(r"[A-Za-z_$][\w$]*", stripped):
            counts_stripped[name][f] += 1
        for name in re.findall(r"[A-Za-z_$][\w$]*", raw):
            counts_raw[name][f] += 1

    graph = _graph_pass(root)

    orphans = []
    for f in files:
        text = _read(f)
        defs = (extract_python_defs(f, text) if f.suffix == ".py"
                else extract_js_defs(text))
        for name, lineno, exported in defs:
            stripped_total = sum(counts_stripped[name].values())
            raw_total = sum(counts_raw[name].values())

            # If both counts say "only the definition exists" -- candidate
            if stripped_total <= 1 and raw_total <= 1:
                # Graph check: does the graph know about this name at all?
                if _graph_has(graph["has_incoming"], name):
                    # Graph says it's called/imported. Trust the graph.
                    flagged = "graph_confirms_used"
                elif _graph_has(graph["known_labels"], name) and not _graph_has(graph["has_outgoing"], name):
                    # In graph, no incoming AND no outgoing -- strongest signal
                    flagged = "graph_orphan"
                else:
                    # Not in graph at all (or has outgoing only). Text says orphan.
                    flagged = None
                orphans.append({
                    "file": str(f), "name": name, "line": lineno,
                    "exported": exported,
                    "private": name.startswith("_") or name.startswith("#"),
                    "flagged": flagged,
                })
            elif stripped_total <= 1 and raw_total > 1:
                # Mentioned in strings/dicts -- possible dynamic dispatch
                orphans.append({
                    "file": str(f), "name": name, "line": lineno,
                    "exported": exported,
                    "private": name.startswith("_") or name.startswith("#"),
                    "flagged": "possibly_dynamic_dispatch",
                })

    DUNDER = re.compile(r"^__.*__$")
    orphans = [o for o in orphans
               if not DUNDER.match(o["name"])
               and not o["name"].startswith("test_")]
    orphans.sort(key=lambda o: (o["file"], o["line"]))
    return orphans

def _print_orphan_group(items, heading, subtitle):
    """Print one group of orphans with a heading and per-file grouping."""
    print("=" * 72)
    print(f"{heading}: {len(items)}")
    print(f"({subtitle})")
    print("=" * 72)
    cur = None
    for o in items:
        if o["file"] != cur:
            cur = o["file"]
            print(f"\n-- {cur} --")
        flags = []
        if o["exported"]:
            flags.append("EXPORTED")
        if o["private"]:
            flags.append("private")
        tag = f"   [{', '.join(flags)}]" if flags else ""
        print(f"  L{o['line']:<5} {o['name']}{tag}")


def format_report(orphans, json_out=False):
    """Print orphan report, split by confidence.

    Buckets, highest confidence first:
      - GRAPH ORPHANS: no incoming edge, no outgoing edge, appears once.
        Strongest dead-code candidate. Safe to delete after a grep.
      - ORPHANED: name appears once in raw and stripped, not in graph.
      - POSSIBLY DYNAMIC: raw mentions exist (strings/dicts).
      - GRAPH CONFIRMS USED: text says once but graph has incoming calls.
        Framework / dynamic dispatch. Do not delete.
    """
    import json as _json
    if json_out:
        print(_json.dumps(orphans, indent=2))
        return

    buckets = {
        "graph_orphan": ("GRAPH ORPHANS",
            "no incoming and no outgoing edges -- strongest dead-code signal."),
        None: ("ORPHANED FUNCTIONS",
            "name appears once in raw and stripped, not in graph."),
        "possibly_dynamic_dispatch": ("POSSIBLY DYNAMIC DISPATCH",
            "referenced in strings/dicts -- check getattr / handlers maps."),
        "graph_confirms_used": ("GRAPH CONFIRMS USED",
            "graph has incoming edges for this name -- framework or dispatch. Do not delete."),
    }

    for flag, (heading, subtitle) in buckets.items():
        items = [o for o in orphans if o.get("flagged") == flag]
        if not items:
            continue
        print("=" * 72)
        print(f"{heading}: {len(items)}")
        print(f"({subtitle})")
        print("=" * 72)
        cur = None
        for o in items:
            if o["file"] != cur:
                cur = o["file"]
                print(f"\n-- {cur} --")
            tags = []
            if o["exported"]:
                tags.append("EXPORTED")
            if o["private"]:
                tags.append("private")
            tag_str = f"   [{', '.join(tags)}]" if tags else ""
            print(f"  L{o['line']:<5} {o['name']}{tag_str}")
        print()

    if not orphans:
        print("No orphans found.")
