import os
import sys
import json
import re
import subprocess

try:
    from tree_sitter import Language, Parser
    import tree_sitter_typescript as tsts
    _HAS_TREE_SITTER = True
except ImportError:
    _HAS_TREE_SITTER = False


def get_tree_sitter_parser(file_path):
    lang_func = tsts.language_tsx if file_path.endswith('.tsx') else tsts.language_typescript
    TS_LANGUAGE = Language(lang_func())
    return Parser(TS_LANGUAGE)


def parse_ast_exact_bounds(file_path, symbol_name):
    if not _HAS_TREE_SITTER:
        return None
    if not file_path or not os.path.exists(file_path):
        return None

    parser = get_tree_sitter_parser(file_path)

    with open(file_path, 'r', encoding='utf-8') as f:
        code = f.read()

    tree = parser.parse(bytes(code, "utf-8"))
    root_node = tree.root_node

    clean_symbol = re.sub(r'[()\s]', '', symbol_name).lower()

    def find_symbol_node(node):
        if node.type in ['function_declaration', 'lexical_declaration', 'method_definition', 'export_statement']:
            node_text = code[node.start_byte:node.end_byte]
            if clean_symbol in node_text.lower():
                return node

        for child in node.children:
            result = find_symbol_node(child)
            if result:
                return result
        return None

    target_node = find_symbol_node(root_node)

    if target_node:
        start_line = target_node.start_point[0] + 1
        end_line = target_node.end_point[0] + 1
        snippet = code[target_node.start_byte:target_node.end_byte]
        return {
            "start_line": start_line,
            "end_line": end_line,
            "code_snippet": snippet
        }
    return None


def extract_file_and_symbol(user_query):
    file_match = re.search(r'([\w\-/.]+\.(?:ts|tsx|js|jsx))', user_query)
    explicit_file = file_match.group(1) if file_match else None

    tokens = [t for t in re.sub(r'[^a-zA-Z0-9_]', ' ', user_query).split() if len(t) > 1]

    symbol = tokens[-1] if tokens else "main"
    for token in tokens:
        if token.upper() in ["POST", "GET", "PUT", "DELETE", "PATCH"]:
            symbol = token.upper()
            break
        elif "layout" in token.lower():
            symbol = "RootLayout"

    return explicit_file, symbol


def run_auto_graphify_extract():
    try:
        sys.stderr.write("[graphify_sitter] AST result is null. Auto-running graphify extract . --code-only...\n")
        subprocess.run("graphify extract . --code-only", shell=True, check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        sys.stderr.write(f"[graphify_sitter] Failed to auto-extract: {e}\n")
        return False


def query_graphify_cli(symbol_name):
    try:
        clean_name = re.sub(r'[()\s]', '', symbol_name)
        cmd = f'graphify query "{clean_name}"'
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)

        lines = output.splitlines()
        for line in lines:
            if clean_name.lower() in line.lower() and "src=" in line:
                match = re.search(r'NODE\s+(\S+)\s+\[src=(.*?)\s+loc=L(\d+)', line)
                if match:
                    return {
                        "symbol_name": match.group(1),
                        "file_path": match.group(2),
                        "approx_line": int(match.group(3))
                    }
    except Exception:
        pass
    return None


def main(user_query):
    if not _HAS_TREE_SITTER:
        print("graphify-sitter requires tree-sitter + tree-sitter-typescript. "
              "Install with: pip install tree-sitter tree-sitter-typescript")
        return

    explicit_file, target_symbol = extract_file_and_symbol(user_query)

    graphify_match = query_graphify_cli(target_symbol)

    file_path = explicit_file or (graphify_match["file_path"] if graphify_match else None)
    if graphify_match and not explicit_file:
        target_symbol = graphify_match["symbol_name"]

    ast_res = parse_ast_exact_bounds(file_path, target_symbol)

    if not ast_res:
        if run_auto_graphify_extract():
            graphify_match = query_graphify_cli(target_symbol)
            file_path = explicit_file or (graphify_match["file_path"] if graphify_match else file_path)
            if graphify_match and not explicit_file:
                target_symbol = graphify_match["symbol_name"]

            ast_res = parse_ast_exact_bounds(file_path, target_symbol)

    if not file_path:
        file_path = "src/app/layout.tsx"
        target_symbol = "RootLayout"

    response = {
        "query": user_query,
        "graphify_matched_node": target_symbol,
        "file_path": file_path,
        "start_line": ast_res["start_line"] if ast_res else (graphify_match["approx_line"] if graphify_match else None),
        "end_line": ast_res["end_line"] if ast_res else None,
        "code_snippet": ast_res["code_snippet"] if ast_res else "Snippet unavailable"
    }

    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)

    user_prompt = " ".join(sys.argv[1:])
    main(user_prompt)
