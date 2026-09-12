import json
import os
import subprocess
import sys
from rapidfuzz import process
from rapidfuzz.fuzz import WRatio

GRAPH_PATH = os.path.join("graphify-out", "graph.json")
SKILL_PATH = os.path.join("graphify", "skill.md")
TOP_K = 5
LOW_CONFIDENCE_SCORE = 60.0  # below this, top match is treated as unreliable
WORKSPACE_FILES = ("pnpm-workspace.yaml", "lerna.json")


def _discover_workspace_dirs(root="."):
    """Best-effort read of pnpm/lerna workspace package globs, resolved to existing dirs."""
    import glob as _glob
    dirs = []

    pw = os.path.join(root, "pnpm-workspace.yaml")
    if os.path.exists(pw):
        try:
            with open(pw, "r", encoding="utf-8") as f:
                in_packages = False
                for line in f:
                    s = line.strip()
                    if s.startswith("packages:"):
                        in_packages = True
                        continue
                    if in_packages:
                        if s.startswith("- "):
                            pattern = s[2:].strip().strip("'\"")
                            dirs.extend(d for d in _glob.glob(os.path.join(root, pattern)) if os.path.isdir(d))
                        elif s and not s.startswith("#"):
                            break
        except Exception:
            pass

    pkg_json = os.path.join(root, "package.json")
    if os.path.exists(pkg_json):
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                pkg = json.load(f)
            for pattern in pkg.get("workspaces", []) or []:
                dirs.extend(d for d in _glob.glob(os.path.join(root, pattern)) if os.path.isdir(d))
        except Exception:
            pass

    # de-dupe, keep order
    seen = set()
    out = []
    for d in dirs:
        norm = os.path.normpath(d)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _run_graphify_extract(target_dir, quiet=True):
    """Runs `graphify extract <target_dir> --code-only` (no LLM key required)."""
    try:
        result = subprocess.run(
            ["graphify", "extract", target_dir, "--code-only"],
            capture_output=True, text=True, timeout=120,
        )
        if not quiet:
            print(result.stdout)
            if result.returncode != 0:
                print(result.stderr)
        return result.returncode == 0
    except FileNotFoundError:
        print("  [auto-graphify] 'graphify' command not found on PATH — skipping auto-repair.")
        return False
    except Exception as e:
        print(f"  [auto-graphify] extract failed: {e}")
        return False


def _graph_path_for(target_dir):
    return os.path.join(target_dir, "graphify-out", "graph.json")


def ensure_graph_freshness(root=".", verbose=True):
    """
    Self-healing pre-step: if no graph exists yet anywhere relevant, or the
    root graph looks disproportionately thin vs. the number of source files,
    (re)run a code-only extract on root + any detected workspace packages.
    Always uses --code-only so this never requires an LLM API key.
    Safe to call before every query — cheap no-op when graphs are already fresh
    (graphify itself does incremental/cached extraction).
    """
    root_graph = _graph_path_for(root)
    workspace_dirs = _discover_workspace_dirs(root)

    needs_root_extract = not os.path.exists(root_graph)

    if not needs_root_extract:
        try:
            with open(root_graph, "r", encoding="utf-8") as f:
                data = json.load(f)
            node_count = len(data.get("nodes", []))
            # crude thinness heuristic: fewer than ~8 nodes per source file scanned
            # (package.json alone commonly yields 15-20 leaf nodes on its own)
            if node_count < 40:
                needs_root_extract = True
        except Exception:
            needs_root_extract = True

    ran_anything = False
    if needs_root_extract:
        if verbose:
            print("  [auto-graphify] graph missing or thin — running code-only extract...")
        if _run_graphify_extract(root, quiet=not verbose):
            ran_anything = True

    for wdir in workspace_dirs:
        wgraph = _graph_path_for(wdir)
        if not os.path.exists(wgraph):
            if verbose:
                print(f"  [auto-graphify] workspace '{wdir}' not indexed — running code-only extract...")
            if _run_graphify_extract(wdir, quiet=not verbose):
                ran_anything = True

    return ran_anything, workspace_dirs


def load_graph_nodes(graph_path=None):
    path = graph_path or GRAPH_PATH
    if not os.path.exists(path):
        print(f"Error: Could not find {path}")
        print("Run 'graphify extract . --code-only' first to generate the graph output.")
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = []
    for node in data.get("nodes", []):
        node_id = node.get("id", "")
        label = node.get("label", node_id)
        nodes.append({
            "id": node_id,
            "label": label,
            "type": node.get("type", node.get("file_type", "unknown")),
            "source_file": node.get("source_file", ""),
        })

    return nodes


def load_all_graph_nodes(root=".", workspace_dirs=None):
    """Loads the root graph plus any workspace-package graphs, merged into one list."""
    all_nodes = load_graph_nodes(_graph_path_for(root))
    for wdir in (workspace_dirs or []):
        wnodes = load_graph_nodes(_graph_path_for(wdir))
        all_nodes.extend(wnodes)
    return all_nodes


def load_graphify_vocab(skill_path=SKILL_PATH):
    """Loads graphify skill/query reference instructions for lightweight vocab expansion."""
    if not os.path.exists(skill_path):
        return {}
    vocab_map = {}
    try:
        with open(skill_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(("-", "*")):
                    parts = line.strip(" -*").split(":")
                    if len(parts) > 1:
                        key = parts[0].strip().lower()
                        synonyms = [s.strip().lower() for s in parts[1].split(",")]
                        vocab_map[key] = synonyms
    except Exception:
        pass
    return vocab_map


def expand_query_with_vocab(query, vocab_map):
    """Expands query terms using loaded Graphify skill references without external ML models."""
    query_lower = query.lower()
    expanded_terms = [query]
    for key, synonyms in vocab_map.items():
        if key in query_lower or any(syn in query_lower for syn in synonyms):
            expanded_terms.extend(synonyms)
    return " ".join(set(expanded_terms))


def find_closest_graph_nodes(query, nodes, top_k=TOP_K):
    """Pure string similarity graph matching using rapidfuzz WRatio against AST node labels."""
    labels = [n["label"] for n in nodes]
    results = process.extract(
        query,
        labels,
        scorer=WRatio,
        limit=top_k,
    )

    print("\n--- Closest Graph Node Matches (Fuzzy Vocab) ---")
    if not results:
        print("  No matching graph nodes found.")
        return True  # thin/empty — caller should treat as low confidence

    for match, score, index in results:
        node = nodes[index]
        src = f"  ({node['source_file']})" if node.get("source_file") else ""
        print(f"  [{score:5.1f}%]  {node['label']}  ({node['type']})  ->  {node['id']}{src}")

    top_score = results[0][1]
    is_thin = top_score < LOW_CONFIDENCE_SCORE or len(results) <= 3
    if is_thin:
        print(f"\n  [low confidence] top match {top_score:.1f}% — results may be unreliable.")
        print(f"  Try instead:  pyslick grep <likely_file> \"<keyword1>\" \"<keyword2>\" --context 3")
    return is_thin


def _load_with_auto_repair(root="."):
    ran, workspace_dirs = ensure_graph_freshness(root)
    nodes = load_all_graph_nodes(root, workspace_dirs)
    if workspace_dirs:
        print(f"Loaded {len(nodes)} graph nodes from {root}/graphify-out and {len(workspace_dirs)} workspace package(s)")
    else:
        print(f"Loaded {len(nodes)} graph nodes from {_graph_path_for(root)}")
    return nodes


def _retry_once_if_thin(query, nodes, root="."):
    """If the first pass is thin/empty, force a fresh extract (bypassing the cache
    heuristic) and re-run once before handing back to the caller."""
    print("  [auto-graphify] results were thin — forcing a fresh code-only extract and retrying once...")
    _run_graphify_extract(root, quiet=True)
    for wdir in _discover_workspace_dirs(root):
        _run_graphify_extract(wdir, quiet=True)
    fresh_nodes = load_all_graph_nodes(root, _discover_workspace_dirs(root))
    find_closest_graph_nodes(query, fresh_nodes)


def main():
    graph_vocab = load_graphify_vocab()
    nodes = _load_with_auto_repair(".")
    if not nodes:
        sys.exit(1)

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
        search_query = expand_query_with_vocab(query, graph_vocab)
        if search_query != query:
            print(f"  [Graphify Vocab Expanded]: {search_query}")
        is_thin = find_closest_graph_nodes(search_query, nodes)
        if is_thin:
            _retry_once_if_thin(search_query, nodes, ".")
        return

    while True:
        try:
            query = input("\nSearch query: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if query.lower() in ("q", "quit", "exit"):
            break
        if not query:
            continue

        search_query = expand_query_with_vocab(query, graph_vocab)
        if search_query != query:
            print(f"  [Graphify Vocab Expanded]: {search_query}")
        is_thin = find_closest_graph_nodes(search_query, nodes)
        if is_thin:
            _retry_once_if_thin(search_query, nodes, ".")


if __name__ == "__main__":
    main()
