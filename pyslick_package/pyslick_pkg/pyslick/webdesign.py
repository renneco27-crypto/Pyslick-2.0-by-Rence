#!/usr/bin/env python3
"""
webdesign.py — cross-file "design" linking for HTML/CSS/JS, no LLM needed.

Problem this solves: graphify.py's AST call-graph only understands Python.
A "design" (a button, a card, a panel) usually lives across three files —
the markup (HTML), the style (CSS), and the behavior (JS) — and pyslick had
no way to connect them. This module does that with plain regex extraction
(good enough for locating things, not a full parser) plus a union-find over
shared ids/classes/filenames to group them into "communities".

Communities are exported to a markdown file you edit by hand:

    pyslick communities            # writes pyslick-out/communities.md
    (you open it, fill in the blank "Description:" lines)
    pyslick query "..."            # now matches against your descriptions
    pyslick communities            # re-run any time; your descriptions are
                                    # preserved and merged with new code

This replaces "graphify needs an API" — there's no external service, no
account, no token. It's a flat file you own and edit in your own words.
"""

import os
import re
import glob
from datetime import datetime

COMM_DIR = "pyslick-out"
COMM_MD = os.path.join(COMM_DIR, "communities.md")

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", ".pyslick_backups",
    "pyslick-out",
}

PLACEHOLDER = "_(no description yet — edit this line and re-run `pyslick communities` to save it)_"


# ─────────────────────────────────────────────────────────────────────────
# 1. File discovery
# ─────────────────────────────────────────────────────────────────────────
def _find_files(root: str, exts: set) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if os.path.splitext(fn)[1] in exts:
                out.append(os.path.normpath(os.path.join(dirpath, fn)))
    return out


# ─────────────────────────────────────────────────────────────────────────
# 2. Node = (type, file, name, line). key() is the stable identity string.
# ─────────────────────────────────────────────────────────────────────────
class Node:
    __slots__ = ("type", "file", "name", "line")

    def __init__(self, type_, file, name, line):
        self.type = type_
        self.file = file
        self.name = name
        self.line = line

    def key(self):
        return f"{self.type}:{self.file}:{self.name}"

    def __repr__(self):
        return f"[{self.type}] {self.name}  {self.file}:{self.line}"


# ─────────────────────────────────────────────────────────────────────────
# 3. Extractors — regex-based, best-effort, not a real parser
# ─────────────────────────────────────────────────────────────────────────
def extract_css(file_path: str):
    """Returns list of (Node, set(classes_and_ids_referenced))."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    lines = content.splitlines()

    results = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", content):
        selector_text = m.group(1).strip()
        if not selector_text or selector_text.startswith("@"):
            continue
        line_no = content[: m.start()].count("\n") + 1
        classes = set(re.findall(r"\.([\w-]+)", selector_text))
        ids = set(re.findall(r"#([\w-]+)", selector_text))
        if not classes and not ids:
            continue
        node = Node("css", file_path, selector_text[:60], line_no)
        results.append((node, classes | {f"#{i}" for i in ids}))
    return results


def extract_html(file_path: str):
    """Returns (elements, linked_files) where elements is a list of
    (Node, set(classes_and_ids), set(referenced_js_function_names)) and
    linked_files is a set of relative <script src> / <link href> paths."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    elements = []
    for m in re.finditer(r"<([a-zA-Z][\w-]*)\b([^>]*)>", content):
        tag, attrs = m.group(1), m.group(2)
        if tag.lower() in ("script", "style", "meta", "link", "html", "head", "body"):
            continue
        id_m = re.search(r'\bid=["\']([\w-]+)["\']', attrs)
        class_m = re.search(r'\bclass=["\']([^"\']+)["\']', attrs)
        if not id_m and not class_m:
            continue
        line_no = content[: m.start()].count("\n") + 1
        ids_classes = set()
        name_bits = []
        if id_m:
            ids_classes.add(f"#{id_m.group(1)}")
            name_bits.append(f"#{id_m.group(1)}")
        if class_m:
            classes = class_m.group(1).split()
            ids_classes |= set(classes)
            name_bits.extend(f".{c}" for c in classes[:2])

        js_refs = set()
        for handler_m in re.finditer(r'\bon\w+=["\']([^"\']+)["\']', attrs):
            js_refs |= set(re.findall(r"([A-Za-z_]\w*)\s*\(", handler_m.group(1)))

        node_name = f"<{tag}> {' '.join(name_bits)}".strip()
        elements.append((Node("html", file_path, node_name, line_no), ids_classes, js_refs))

    linked_files = set()
    for m in re.finditer(r'<(?:script[^>]+src|link[^>]+href)=["\']([^"\']+)["\']', content):
        linked_files.add(m.group(1))

    return elements, linked_files


def extract_js(file_path: str):
    """Returns list of (Node, set(dom_ids_classes_referenced), set(js_calls_made))."""
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    lines = content.splitlines()

    # Find function start positions (name, char offset)
    fn_starts = []
    for m in re.finditer(r"function\s+([A-Za-z_]\w*)\s*\(", content):
        fn_starts.append((m.group(1), m.start()))
    for m in re.finditer(r"(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", content):
        fn_starts.append((m.group(1), m.start()))
    fn_starts.sort(key=lambda x: x[1])

    results = []
    for i, (name, start) in enumerate(fn_starts):
        end = fn_starts[i + 1][1] if i + 1 < len(fn_starts) else len(content)
        body = content[start:end]
        line_no = content[:start].count("\n") + 1

        dom_refs = set()
        for dm in re.finditer(r"getElementById\(\s*['\"]([\w-]+)['\"]\s*\)", body):
            dom_refs.add(f"#{dm.group(1)}")
        for dm in re.finditer(r"querySelector(?:All)?\(\s*['\"]([.#][\w-]+)['\"]\s*\)", body):
            sel = dm.group(1)
            dom_refs.add(sel if sel.startswith("#") else sel[1:])
        for dm in re.finditer(r"classList\.(?:add|remove|toggle)\(\s*['\"]([\w-]+)['\"]", body):
            dom_refs.add(dm.group(1))

        calls = set(re.findall(r"([A-Za-z_]\w*)\s*\(", body)) - {name}
        results.append((Node("js", file_path, name, line_no), dom_refs, calls))

    return results


# ─────────────────────────────────────────────────────────────────────────
# 4. Union-Find
# ─────────────────────────────────────────────────────────────────────────
class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


# ─────────────────────────────────────────────────────────────────────────
# 5. Build the whole cross-file graph and cluster it
# ─────────────────────────────────────────────────────────────────────────
def build_communities(root: str = ".", include_singletons: bool = False):
    css_files = _find_files(root, {".css", ".scss"})
    html_files = _find_files(root, {".html", ".htm"})
    js_files = _find_files(root, {".js", ".jsx", ".ts", ".tsx"})

    uf = UnionFind()
    all_nodes = {}          # key -> Node
    selector_index = {}     # ".foo" or "#bar" -> set(css node keys)
    js_name_index = {}      # js function name -> node key (first file wins)

    # CSS
    for cf in css_files:
        for node, refs in extract_css(cf):
            all_nodes[node.key()] = node
            for ref in refs:
                selector_index.setdefault(ref, set()).add(node.key())

    # JS
    js_calls_pending = []  # (node_key, set(call_names))
    for jf in js_files:
        for node, dom_refs, calls in extract_js(jf):
            all_nodes[node.key()] = node
            js_name_index[node.name] = node.key()
            for ref in dom_refs:
                selector_index.setdefault(ref, set()).add(node.key())
            js_calls_pending.append((node.key(), calls))

    # link JS calls to other JS functions (same-project call graph)
    for node_key, calls in js_calls_pending:
        for c in calls:
            target = js_name_index.get(c)
            if target and target != node_key:
                uf.union(node_key, target)

    # HTML
    html_linked_files = {}  # html file -> set(referenced relative paths)
    for hf in html_files:
        elements, linked = extract_html(hf)
        html_linked_files[hf] = linked
        for node, ids_classes, js_refs in elements:
            all_nodes[node.key()] = node
            for ref in ids_classes:
                selector_index.setdefault(ref, set()).add(node.key())
            for fn_name in js_refs:
                target = js_name_index.get(fn_name)
                if target:
                    uf.union(node.key(), target)

    # union everything that shares a selector (id/class)
    for ref, keys in selector_index.items():
        keys = list(keys)
        for k in keys[1:]:
            uf.union(keys[0], k)

    # weaker signal: html <-> its <script src> / <link href> files, by
    # resolving the referenced path relative to the html file
    for hf, linked in html_linked_files.items():
        hf_dir = os.path.dirname(hf)
        for rel in linked:
            resolved = os.path.normpath(os.path.join(hf_dir, rel))
            # union any node whose file matches the resolved path with any
            # node from this html file (best-effort co-location signal)
            same_file_targets = [k for k, n in all_nodes.items() if n.file == resolved]
            same_file_html = [k for k, n in all_nodes.items() if n.file == hf]
            if same_file_targets and same_file_html:
                for t in same_file_targets[:1]:
                    for h in same_file_html[:1]:
                        uf.union(t, h)

    # group into communities
    groups = {}
    for key in all_nodes:
        root_key = uf.find(key)
        groups.setdefault(root_key, []).append(key)

    communities = []
    for root_key, keys in groups.items():
        if len(keys) < 2 and not include_singletons:
            continue
        members = sorted(keys, key=lambda k: (all_nodes[k].type, all_nodes[k].file, all_nodes[k].line))
        communities.append({
            "members": members,
            "nodes": {k: all_nodes[k] for k in members},
        })

    # deterministic ordering across regenerations: by first member's file/line
    communities.sort(key=lambda c: (all_nodes[c["members"][0]].file, all_nodes[c["members"][0]].line))
    return communities, all_nodes


# ─────────────────────────────────────────────────────────────────────────
# 6. Markdown read/write (this IS the persistence layer / "no API" store)
# ─────────────────────────────────────────────────────────────────────────
def parse_existing_md(path: str) -> list[dict]:
    """Returns list of {"description": str, "members": set(member_keys)}."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = re.split(r"\n(?=## Community )", text)
    parsed = []
    for block in blocks:
        if not block.startswith("## Community"):
            continue
        desc_m = re.search(r"^Description:\s*(.*)$", block, re.MULTILINE)
        description = desc_m.group(1).strip() if desc_m else ""
        if description == PLACEHOLDER or not description:
            description = ""
        members = set(re.findall(r"^- `([^`]+)`", block, re.MULTILINE))
        if members:
            parsed.append({"description": description, "members": members})
    return parsed


def _best_match_description(members: set, old_communities: list[dict]) -> str:
    best_score, best_desc = 0.0, ""
    for old in old_communities:
        if not old["description"]:
            continue
        overlap = len(members & old["members"])
        union = len(members | old["members"])
        score = overlap / union if union else 0
        if score > best_score:
            best_score, best_desc = score, old["description"]
    return best_desc if best_score >= 0.4 else ""


def write_communities_md(path: str, communities: list[dict], root: str):
    old = parse_existing_md(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    lines = [
        "# PySlick Design Communities",
        "",
        f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} from `{os.path.abspath(root)}`_",
        "",
        "Each section below is a group of HTML/CSS/JS/Python pieces that pyslick",
        "found linked together (shared ids/classes, shared script/link tags, or",
        "call graphs). **Fill in the `Description:` line in your own words** —",
        "e.g. \"mic button: markup + round-button style + toggle/pulse behavior\".",
        "",
        "Re-run `pyslick communities` any time; your descriptions are kept and",
        "matched to the closest new group automatically. `pyslick query \"...\"`",
        "will then match your directive against these descriptions first, and",
        "hand back every file in the matched group — not just one.",
        "",
        "---",
        "",
    ]

    for i, comm in enumerate(communities, start=1):
        members = set(comm["members"])
        description = _best_match_description(members, old) or PLACEHOLDER
        lines.append(f"## Community {i}")
        lines.append(f"Description: {description}")
        lines.append("")
        for key in comm["members"]:
            node = comm["nodes"][key]
            lines.append(f"- `{key}` — [{node.type}] {node.name}  ({node.file}:{node.line})")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    carried_over = sum(1 for c in communities if _best_match_description(set(c["members"]), old))
    return carried_over


def load_communities_for_query(root: str = ".") -> list[dict]:
    """Used by query.py: returns [{"description":str, "members": [(type,file,name,line), ...]}]
    for communities that actually have a user-written (non-placeholder) description."""
    path = os.path.join(root, COMM_MD)
    parsed = parse_existing_md(path)
    out = []
    for c in parsed:
        if not c["description"]:
            continue
        members_detail = []
        for m in c["members"]:
            parts = m.split(":", 2)
            if len(parts) == 3:
                members_detail.append(parts)  # (type, file, name)
        out.append({"description": c["description"], "members": members_detail})
    return out


# ─────────────────────────────────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────────────────────────────────
def run(root: str = ".", include_singletons: bool = False):
    communities, all_nodes = build_communities(root, include_singletons=include_singletons)
    if not communities:
        print("No cross-file HTML/CSS/JS/Python links found — nothing to group.")
        print("(This needs at least two linked files, e.g. an id in HTML matched by a CSS rule.)")
        return

    carried_over = write_communities_md(COMM_MD, communities, root)
    print(f"Found {len(communities)} design communities across "
          f"{len({n.file for n in all_nodes.values()})} files.")
    if carried_over:
        print(f"Carried over {carried_over} of your existing descriptions.")
    print(f"Written to: {COMM_MD}")
    print("Open it, fill in the 'Description:' lines, then just ask "
          "`pyslick query \"...\"` — it'll read this file automatically.")


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else ".")
