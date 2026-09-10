import json
import os
import sys
from rapidfuzz import process
from rapidfuzz.fuzz import WRatio

GRAPH_PATH = os.path.join("graphify-out", "graph.json")
SKILL_PATH = os.path.join("graphify", "skill.md")
TOP_K = 5


def load_graph_nodes():
    if not os.path.exists(GRAPH_PATH):
        print(f"Error: Could not find {GRAPH_PATH}")
        print("Run 'graphify .' first to generate the graph output.")
        return []

    with open(GRAPH_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes = []
    for node in data.get("nodes", []):
        node_id = node.get("id", "")
        label = node.get("label", node_id)
        nodes.append({
            "id": node_id,
            "label": label,
            "type": node.get("type", "unknown"),
        })

    return nodes


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
        return

    for match, score, index in results:
        node = nodes[index]
        print(f"  [{score:5.1f}%]  {node['label']}  ({node['type']})  ->  {node['id']}")


def main():
    nodes = load_graph_nodes()
    if not nodes:
        sys.exit(1)

    graph_vocab = load_graphify_vocab()
    print(f"Loaded {len(nodes)} graph nodes from {GRAPH_PATH}")

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
        search_query = expand_query_with_vocab(query, graph_vocab)
        if search_query != query:
            print(f"  [Graphify Vocab Expanded]: {search_query}")
        find_closest_graph_nodes(search_query, nodes)
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
        find_closest_graph_nodes(search_query, nodes)


if __name__ == "__main__":
    main()
