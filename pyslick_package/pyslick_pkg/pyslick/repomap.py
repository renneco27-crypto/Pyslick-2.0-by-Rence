"""
repomap.py — one unified, multi-language, syntax-aware map of a codebase.

Replaces the split logic of graphify.py (Python-only AST) and
graphify_sitter.py (TypeScript-only, CLI-shelling-out) with a single
tree-sitter-language-pack backed extractor that works across ~20 common
languages, plus a PageRank-ranked file graph so pyslick can answer
"what are the most connected files in this project" for real instead of
falling back to filename fuzzy-matching.

Usage:
    from repomap import build_repo_map, query_multilang, rank_top_files

    # "top 5 connected files" — the feature query.py's docstring
    # said should exist but never did for non-Python files
    ranked = rank_top_files(".", top_n=5)

    # Ask a natural-language question against ANY supported file,
    # not just .py — this is graphify.query()'s multi-language sibling
    results = query_multilang("src/app/layout.tsx", "how does the header render")

Design notes:
  - Definitions and calls are extracted generically via tree-sitter's
    `child_by_field_name("name")` / `child_by_field_name("function")`
    convention, which holds across nearly every tree-sitter grammar
    (verified against python, javascript, typescript/tsx, java, go,
    rust, c, cpp, c_sharp, ruby, php, kotlin, swift). This trades a
    little precision for zero per-language query files to maintain —
    consistent with pyslick's "no LLM, best-effort, all local" ethos.
  - The file-level graph (which file references a name defined in
    which other file) is what gets PageRanked — this is the same
    core idea as Aider's repomap.py, adapted to reuse pyslick's
    existing SKIP_DIRS/CODE_EXTS conventions instead of a separate
    config surface.
  - graphify.py (the Python AST path) is left untouched — it's more
    precise for .py specifically (real scope resolution, docstrings,
    decorators) and patchit.py / recon.py / autoloop.py already
    depend on its exact return shape. repomap.py is the fallback/
    multi-language layer that graphify_sitter.py used to gesture at
    but never actually implemented (it only shelled out to a
    `graphify` CLI that doesn't parse anything itself).
"""

from __future__ import annotations

import os
import re
import math
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

try:
    from tree_sitter_language_pack import get_parser
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False

try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    _HAS_NETWORKX = False


# ---------------------------------------------------------------------------
# Config — reuses pyslick's existing skip/ext conventions (see toolbox.py,
# query.py, agent.py) so this module doesn't introduce a second source of
# truth for "what counts as a source file".
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".turbo", ".cache", "coverage", "out", "graphify-out",
    ".gradle", "target", "bin", "obj", ".pnpm", ".gemini", ".agents",
    ".system_generated", "android_capacitor_backup", "outputs", ".idea",
    ".vscode", "captures", "intermediates", "generated",
}

# Substrings in directory paths that always indicate build artifacts
SKIP_DIR_PARTS = {"/build/", "\\build\\", "/out/", "\\out\\", "/dist/", "\\dist\\",
                  "/intermediates/", "\\intermediates\\", "/generated/", "\\generated\\",
                  "/outputs/", "\\outputs\\", "/.next/", "\\.next\\", "/node_modules/", "\\node_modules\\"}

# Files that are generated bundles, sourcemaps, minified, or lockfiles
SKIP_FILE_EXTS = {".map", ".min.js", ".min.css", ".bundle.js", ".chunk.js", ".wasm", ".lock", ".d.ts.map"}
SKIP_FILE_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "sw.js", "workbox-*.js"}

def is_generated_or_minified_file(filepath: str) -> bool:
    """Return True if filepath is a generated artifact, bundle, sourcemap, or minified."""
    norm = filepath.replace("\\", "/").lower()
    base = os.path.basename(filepath).lower()

    # Direct filename / extension skips
    for ext in SKIP_FILE_EXTS:
        if base.endswith(ext):
            return True
    if base in ("sw.js", "workbox-window.prod.mjs"):
        # sw.js in public or root is a compiled bundle
        return True
    if ".min." in base or ".bundle." in base or ".chunk." in base:
        return True

    # Path substring check
    for part in SKIP_DIR_PARTS:
        if part.replace("\\", "/").lower() in norm:
            return True

    # Check file size: ignore files > 1.2 MB for code AST / semantic search
    try:
        size = os.path.getsize(filepath)
        if size > 1_200_000:
            return True
    except OSError:
        return True

    # Content inspection on first 16KB: check for minification or binary
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(16384)
        if b"\0" in chunk:
            return True
        # Check line lengths: minified files have few lines with extreme length
        lines = chunk.split(b"\n")
        if lines:
            max_line = max(len(l) for l in lines)
            if max_line > 1500:
                return True
            if len(lines) > 1:
                avg_line = sum(len(l) for l in lines) / len(lines)
                if avg_line > 400:
                    return True
    except Exception:
        return True

    return False

# extension -> tree-sitter-language-pack language name
EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift",
}

DEF_NODE_TYPES = {
    "function_definition", "function_declaration", "function_item",
    "method_definition", "method_declaration",
    "class_definition", "class_declaration",
    "struct_item", "impl_item", "trait_item",
    "interface_declaration",
}
CALL_NODE_TYPES = {
    "call", "call_expression", "method_invocation", "function_call_expression",
}

_parser_cache: dict[str, object] = {}


def _get_parser_for(ext: str):
    lang = EXT_TO_LANG.get(ext)
    if lang is None or not _HAS_TREE_SITTER:
        return None
    if lang not in _parser_cache:
        try:
            _parser_cache[lang] = get_parser(lang)
        except Exception:
            _parser_cache[lang] = None
    return _parser_cache[lang]


# ---------------------------------------------------------------------------
# 1. File discovery (mirrors query.py's build_file_index, extended to every
#    extension repomap knows how to parse rather than just .py/.ts/.tsx/.js)
# ---------------------------------------------------------------------------

def collect_files(root: str = ".") -> list[str]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and not d.endswith(".tmp")
        ]
        norm_dir = dirpath.replace("\\", "/").lower()
        if any(part in norm_dir for part in ("build", "dist", "out", "node_modules", ".next", "intermediates", "outputs")):
            continue

        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext in EXT_TO_LANG:
                fpath = os.path.normpath(os.path.join(dirpath, fn))
                if not is_generated_or_minified_file(fpath):
                    files.append(fpath)
    return files


# ---------------------------------------------------------------------------
# 2. Tag extraction — definitions + calls, generic across languages
# ---------------------------------------------------------------------------

@dataclass
class Tag:
    name: str
    kind: str          # node.type, e.g. "function_definition"
    start_line: int
    end_line: int
    is_def: bool        # True = definition, False = reference/call


def extract_tags(file_path: str) -> list[Tag] | None:
    """Parse a single file and return its definitions + call references.
    Returns None if the file's language isn't supported or parsing fails."""
    ext = Path(file_path).suffix
    parser = _get_parser_for(ext)
    if parser is None:
        return None

    try:
        with open(file_path, "rb") as f:
            code = f.read()
    except OSError:
        return None

    try:
        tree = parser.parse(code)
    except Exception:
        return None

    tags: list[Tag] = []

    def walk(node):
        if node.type in DEF_NODE_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                name = code[name_node.start_byte:name_node.end_byte].decode("utf-8", "ignore")
                tags.append(Tag(
                    name=name, kind=node.type,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    is_def=True,
                ))
        elif node.type in CALL_NODE_TYPES:
            fn_node = node.child_by_field_name("function") or node.child_by_field_name("method")
            if fn_node is not None:
                text = code[fn_node.start_byte:fn_node.end_byte].decode("utf-8", "ignore")
                # strip qualifiers: "self.foo" / "pkg::foo" / "obj.foo" -> "foo"
                name = re.split(r"[.:]", text)[-1].strip()
                if name:
                    tags.append(Tag(
                        name=name, kind=node.type,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        is_def=False,
                    ))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return tags


# ---------------------------------------------------------------------------
# 3. Repo-wide graph + PageRank — "top N most connected files"
# ---------------------------------------------------------------------------

@dataclass
class RepoMap:
    file_tags: dict[str, list[Tag]] = field(default_factory=dict)
    ranked_files: list[tuple[str, float]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # files with no parser available


def build_repo_map(root: str = ".", focus_files: list[str] | None = None) -> RepoMap:
    """Parse every supported file under root, build a file-level reference
    graph (file A -> file B if A calls a name defined in B), and PageRank it.

    If focus_files is given (e.g. files already touched in this session),
    PageRank is personalized toward them — mirrors Aider's "mentioned files
    get a relevance boost" behaviour.
    """
    rm = RepoMap()
    if not _HAS_TREE_SITTER:
        return rm

    files = collect_files(root)
    name_to_defining_files: dict[str, set[str]] = defaultdict(set)

    for fp in files:
        tags = extract_tags(fp)
        if tags is None:
            rm.skipped.append(fp)
            continue
        rm.file_tags[fp] = tags
        for t in tags:
            if t.is_def:
                name_to_defining_files[t.name].add(fp)

    if not _HAS_NETWORKX or not rm.file_tags:
        # No graph library, or nothing parsed — every file ranks equally
        rm.ranked_files = [(fp, 1.0) for fp in rm.file_tags]
        return rm

    G = nx.MultiDiGraph()
    for fp in rm.file_tags:
        G.add_node(fp)

    for fp, tags in rm.file_tags.items():
        for t in tags:
            if t.is_def:
                continue
            for target in name_to_defining_files.get(t.name, ()):
                if target != fp:
                    G.add_edge(fp, target)

    if G.number_of_edges() == 0:
        rm.ranked_files = [(fp, 1.0) for fp in rm.file_tags]
        return rm

    personalization = None
    if focus_files:
        focus_set = {os.path.normpath(f) for f in focus_files}
        hits = {n: 1.0 for n in G.nodes if n in focus_set}
        if hits:
            personalization = hits

    try:
        pr = nx.pagerank(G, personalization=personalization)
    except Exception:
        # PageRank can fail to converge on pathological graphs — fall back
        # to plain in-degree so this never hard-crashes a CLI query.
        pr = {n: float(d) for n, d in G.in_degree()}

    rm.ranked_files = sorted(pr.items(), key=lambda kv: kv[1], reverse=True)
    return rm


def rank_top_files(root: str = ".", top_n: int = 5, focus_files: list[str] | None = None) -> list[tuple[str, float]]:
    """The "top N most connected files" answer. Returns [(path, score), ...]."""
    rm = build_repo_map(root, focus_files=focus_files)
    return rm.ranked_files[:top_n]


# ---------------------------------------------------------------------------
# 4. Single-file natural-language query — the multi-language sibling of
#    graphify.query(), used by query.py for any non-.py file
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


@dataclass
class MultiLangResult:
    name: str
    kind: str
    start_line: int
    end_line: int
    score: float
    code: str
    file: str
    calls: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        header = f"[{self.kind}] {self.name}  L{self.start_line}-{self.end_line}  score={self.score:.2f}"
        bar = "═" * min(len(header), 80)
        calls_str = f"\n  calls → {', '.join(self.calls)}" if self.calls else ""
        return f"{header}{calls_str}\n{bar}\n{self.code}\n"


def query_multilang(file_path: str, question: str, top_k: int = 5, min_score: float = 0.0) -> list[MultiLangResult]:
    """Natural-language query against a single file in any supported language.
    Same intent as graphify.query() but backed by tree-sitter tags instead of
    Python's ast module, so it works on .ts/.tsx/.go/.rs/.java/etc as well."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    tags = extract_tags(file_path)
    if tags is None:
        return []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    defs = [t for t in tags if t.is_def]
    calls = [t.name for t in tags if not t.is_def]

    raw_tokens = _tokenize(question)
    query_tokens = list(set(raw_tokens + [w for t in raw_tokens for w in _expand_identifier(t)]))

    scored: list[tuple[float, Tag]] = []
    for t in defs:
        name_tokens = _expand_identifier(t.name)
        score = 0.0
        for qt in query_tokens:
            if qt in name_tokens:
                score += 5.0
            elif any(qt in nt or nt in qt for nt in name_tokens):
                score += 1.5
        if score > 0:
            scored.append((score, t))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, t in scored[:top_k]:
        if score < min_score:
            continue
        code = "\n".join(lines[t.start_line - 1:t.end_line])
        # calls made specifically inside this def's line range
        inner_calls = sorted({
            c.name for c in tags
            if not c.is_def and t.start_line <= c.start_line <= t.end_line and c.name != t.name
        })
        results.append(MultiLangResult(
            name=t.name, kind=t.kind, start_line=t.start_line, end_line=t.end_line,
            score=round(score, 4), code=code, file=file_path, calls=inner_calls,
        ))
    return results


def is_supported(file_path: str) -> bool:
    return Path(file_path).suffix in EXT_TO_LANG


def list_definitions(file_path: str) -> list[dict]:
    """Return every definition in a file, unfiltered by any relevance score —
    used as the candidate pool for semantic (embedding) search in llm.py,
    which needs to compare *all* symbols against the directive rather than
    only the ones token-matching already surfaced."""
    tags = extract_tags(file_path)
    if tags is None:
        return []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().splitlines()

    defs = [t for t in tags if t.is_def]
    out = []
    for t in defs:
        code = "\n".join(lines[t.start_line - 1:t.end_line])
        out.append({
            "name": t.name, "kind": t.kind,
            "start_line": t.start_line, "end_line": t.end_line,
            "code": code,
        })
    return out


def dependency_status() -> dict:
    """Report which optional deps are present, for CLI diagnostics / help text."""
    return {
        "tree_sitter_language_pack": _HAS_TREE_SITTER,
        "networkx": _HAS_NETWORKX,
    }


# ---------------------------------------------------------------------------
# 5. Codebase Overview Engine (answers "what does this codebase do")
# ---------------------------------------------------------------------------

_OVERVIEW_PATTERNS = [
    r"\bwhat\s+(?:does\s+)?(?:this\s+)?(?:codebase|repo|repository|project|app)\s+do\b",
    r"\bexplain\s+(?:this\s+)?(?:codebase|repo|repository|project|app|architecture)\b",
    r"\b(?:codebase|repo|project|app)\s+overview\b",
    r"\bdescribe\s+(?:this\s+)?(?:codebase|repo|project|app)\b",
    r"\barchitecture\s+overview\b",
    r"\bhow\s+(?:is\s+)?(?:this\s+)?(?:project|repo|app|codebase)\s+structured\b",
    r"\bwhat\s+is\s+this\s+(?:project|repo|app|codebase)\b",
    r"^overview$",
    r"^architecture$",
]


def is_overview_query(directive: str) -> bool:
    """Detect if a query is asking for a global codebase overview / architecture explanation."""
    d = (directive or "").strip().lower()
    return any(re.search(p, d) for p in _OVERVIEW_PATTERNS)


def get_codebase_overview(root: str = ".") -> dict:
    """Extract a rich, factual overview of the codebase using PageRank, project manifests,
    and opening docstrings / comments. 100% deterministic and hallucination-free.
    """
    import json as _json
    abs_root = os.path.abspath(root)

    # 1. Project metadata
    project_meta = {}
    pkg_json = os.path.join(abs_root, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8", errors="ignore") as fh:
                pj = _json.load(fh)
            project_meta["name"] = pj.get("name", "")
            project_meta["description"] = pj.get("description", "")
            project_meta["scripts"] = list(pj.get("scripts", {}).keys())
            project_meta["dependencies"] = list(pj.get("dependencies", {}).keys())[:15]
            project_meta["type"] = "Node / TypeScript / JavaScript"
        except Exception:
            pass

    pyproj = os.path.join(abs_root, "pyproject.toml")
    if os.path.isfile(pyproj):
        try:
            with open(pyproj, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            name_m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', txt)
            desc_m = re.search(r'description\s*=\s*["\']([^"\']+)["\']', txt)
            if name_m: project_meta["name"] = name_m.group(1)
            if desc_m: project_meta["description"] = desc_m.group(1)
            project_meta["type"] = "Python"
        except Exception:
            pass

    # 2. Top connected files via PageRank
    top_files_ranked = rank_top_files(abs_root, top_n=8)
    top_files = []
    for fpath, score in top_files_ranked:
        rel = os.path.relpath(fpath, abs_root)
        # Read first comments / docstrings
        first_comment = ""
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                head_lines = [fh.readline() for _ in range(25)]
            for line in head_lines:
                cl = line.strip()
                if cl.startswith(("#", "//", "/*", "*", '"""', "'''")):
                    clean = re.sub(r'^[#/*\s─\-=•"\']+', "", cl).strip()
                    if len(clean) > 15 and not clean.startswith(("import ", "from ", "export ", "<")):
                        first_comment = clean
                        break
        except Exception:
            pass
        top_files.append({
            "path": rel,
            "pagerank": round(score, 4),
            "summary": first_comment or f"Core connected module ({rel})",
        })

    # 3. Detect key entry points
    entry_candidates = [
        "src/app/page.tsx", "src/app/layout.tsx", "src/pages/index.tsx",
        "src/index.ts", "src/index.js", "src/main.ts", "src/main.py",
        "app.py", "main.py", "__init__.py", "src/sw.ts", "public/sw.js",
    ]
    entry_points = []
    for cand in entry_candidates:
        full_p = os.path.join(abs_root, cand)
        if os.path.isfile(full_p):
            entry_points.append(cand)

    # 4. Format structured markdown
    md_lines = ["# Codebase Architecture Overview\n"]
    if project_meta.get("name"):
        md_lines.append(f"**Project**: {project_meta['name']}  ({project_meta.get('type', 'Codebase')})")
    if project_meta.get("description"):
        md_lines.append(f"**Description**: {project_meta['description']}\n")

    if entry_points:
        md_lines.append("## Key Entry Points")
        for ep in entry_points:
            md_lines.append(f"- `{ep}`")
        md_lines.append("")

    if top_files:
        md_lines.append("## Most Connected Core Files (PageRank)")
        for tf in top_files:
            md_lines.append(f"- **`{tf['path']}`** (score: {tf['pagerank']}): {tf['summary']}")
        md_lines.append("")

    return {
        "metadata": project_meta,
        "entry_points": entry_points,
        "top_files": top_files,
        "overview_text": "\n".join(md_lines),
    }


# ---------------------------------------------------------------------------
# 6. Universal Dependency & Import Scanner
# ---------------------------------------------------------------------------

_JS_IMPORT_RE = re.compile(r"""(?:import\s+(?:(?:[\w*\s{},$]+)\s+from\s+)?|import\s*\(|require\s*\()\s*['"]([^'"]+)['"]""", re.MULTILINE)
_PY_IMPORT_RE = re.compile(r"""^\s*(?:from\s+([a-zA-Z0-9_\.]+)\s+import|import\s+([a-zA-Z0-9_\.,\s]+))""", re.MULTILINE)
_JAVA_IMPORT_RE = re.compile(r"""^\s*import\s+(?:static\s+)?([a-zA-Z0-9_\.]+);""", re.MULTILINE)
_GO_IMPORT_RE = re.compile(r"""^\s*(?:import\s+['"]([^'"]+)['"]|['"]([^'"]+)['"])""", re.MULTILINE)
_RS_USE_RE = re.compile(r"""^\s*use\s+([a-zA-Z0-9_:]+)""", re.MULTILINE)


def scan_imports_and_dependencies(root: str = ".", target: str | None = None) -> dict:
    """Scan all project manifests and source files for imported libraries and dependencies."""
    abs_root = os.path.abspath(root)
    manifest_deps = {}

    # 1. Read manifests
    pkg_json = os.path.join(abs_root, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8", errors="ignore") as fh:
                pj = json.load(fh)
            for k in ("dependencies", "devDependencies", "peerDependencies"):
                for dep, ver in pj.get(k, {}).items():
                    manifest_deps[dep] = {"version": ver, "type": k, "manifest": "package.json"}
        except Exception:
            pass

    pyproj = os.path.join(abs_root, "pyproject.toml")
    if os.path.isfile(pyproj):
        try:
            with open(pyproj, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            for m in re.finditer(r'["\']([a-zA-Z0-9_\-]+)(?:[><=~^!][^"\']*)?["\']', txt):
                name = m.group(1).lower()
                if name not in ("build-system", "setuptools", "project", "pyslick"):
                    manifest_deps[name] = {"version": "*", "type": "pyproject", "manifest": "pyproject.toml"}
        except Exception:
            pass

    # 2. Walk source files and extract imports
    imports_by_pkg: dict[str, list[dict]] = defaultdict(list)
    total_scanned = 0

    for fpath in collect_files(abs_root):
        if is_generated_or_minified_file(fpath):
            continue
        rel = os.path.relpath(fpath, abs_root)
        ext = os.path.splitext(fpath)[1].lower()
        total_scanned += 1

        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            continue

        for idx, line in enumerate(lines):
            lineno = idx + 1
            matched_pkgs = []

            if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                for m in _JS_IMPORT_RE.finditer(line):
                    raw = m.group(1).strip()
                    # Filter relative imports unless user specifically asked
                    if not raw.startswith("."):
                        pkg = raw.split("/")[0] if not raw.startswith("@") else "/".join(raw.split("/")[:2])
                        matched_pkgs.append((pkg, raw))
                    elif target and target in raw:
                        matched_pkgs.append((raw, raw))

            elif ext == ".py":
                for m in _PY_IMPORT_RE.finditer(line):
                    g1, g2 = m.group(1), m.group(2)
                    raw = (g1 or g2 or "").strip()
                    if raw:
                        pkg = raw.split(".")[0].split()[0].strip(",")
                        matched_pkgs.append((pkg, raw))

            elif ext in (".java", ".kt"):
                for m in _JAVA_IMPORT_RE.finditer(line):
                    raw = m.group(1).strip()
                    parts = raw.split(".")
                    pkg = ".".join(parts[:2]) if len(parts) >= 2 else raw
                    matched_pkgs.append((pkg, raw))

            elif ext == ".rs":
                for m in _RS_USE_RE.finditer(line):
                    raw = m.group(1).strip()
                    pkg = raw.split("::")[0]
                    matched_pkgs.append((pkg, raw))

            for pkg_name, raw_stmt in matched_pkgs:
                if target and target.lower() not in pkg_name.lower() and target.lower() not in raw_stmt.lower():
                    continue
                imports_by_pkg[pkg_name].append({
                    "file": rel,
                    "line": lineno,
                    "statement": line.strip()[:140],
                    "raw": raw_stmt,
                })

    return {
        "root": abs_root,
        "target": target,
        "files_scanned": total_scanned,
        "manifest_dependencies": manifest_deps,
        "imports_by_package": dict(imports_by_pkg),
    }


