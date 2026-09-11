import sys
import os
import io

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS_DIR, "pyslick"))
import agent

queries = [
    (" pyslick querries", "help"),
    ("help me run pyslick", "help"),
    ("how to run file, repo,directory", "run_info"),
    ("run the file", "run_info"),
    ("what does this app do", "run_info"),
    ("what does this project do", "run_info"),
    ("what does this program do", "run_info"),
    ("whats this", "run_info"),
    ("whats this project", "run_info"),
    ("git staus", "git"),
    ("show me the coments", "comments"),
    ("show me comments lines 171 to 213 llmpy", "comments"),
    ("closest functon to line 20", "nearest"),
    ("how autoloop conects to patchit", "connect"),
    ("graphmd", "graph"),
    ("graph txt", "graph"),
    ("graphify notes", "graph"),
    ("graphify.", "graph"),
    ("show me design code of the entire webpage", "graph"),
    ("show me code design of entire webpage", "graph"),
    ("ui components", "graph"),
    ("visual design", "graph"),
    ("digital layout", "graph"),
    ("user interface design", "graph"),
    ("component styles", "graph"),
    ("webpage design", "graph"),
    ("show me js scripts", "list_files"),
    ("show me python files", "list_files"),
    ("show me tsx files", "list_files"),
    ("show me env files", "list_files"),
    ("show me c++", "list_files"),
    ("show me c files", "list_files"),
    ("show .js", "list_files"),
    ("show .env", "list_files"),
    ("show .kt", "list_files"),
    ("show me all kotlin files", "list_files"),
    ("show me all zig files", "list_files"),
    ("show .tsx", "list_files"),
    ("show me line 24-72 of package.json", "file_info"),
    ("scan package.json line 24-72", "file_info"),
    ("show me gitignore", "file_info"),
    ("show me packagejson", "file_info"),
    ("show me package.json scan", "file_info"),
    ("whats the use of test_file", "file_info"),
    # ── new intents ────────────────────────────────────────────
    ("find stray symbol", "stray_symbols"),
    ("find missing symbol", "stray_symbols"),
    ("find stray symbols", "stray_symbols"),
    ("look for stray symbols", "stray_symbols"),
    ("check syntax", "syntax_check"),
    ("look for syntax error", "syntax_check"),
    ("find syntax errors", "syntax_check"),
    ("check for syntax errors", "syntax_check"),
    ("check indentation", "indentation"),
    ("find indentation issues", "indentation"),
    ("indentation problems", "indentation"),
    # ── general recon & multi-target queries ───────────────
    ("find functions and files card model schema state handlers", "nearest"),
    ("find nearest file names", "nearest"),
    ("find nearest functions", "nearest"),
    ("general recon", "nearest"),
    ("find function handle_click", "scan_function"),
    # ── "where is" file-location queries → file_info ────────
    ("where is graphify.md located", "file_info"),
    ("where is graphify.md", "file_info"),
    ("where's graphify.md", "file_info"),
    ("locate graphify.md", "file_info"),
    ("where is the readme", "file_info"),
]

print("=== Testing Confidence-Scored Intent Classification ===")
failed_count = 0
for q, expected in queries:
    classified = agent._classify_intent(q)
    is_ok = (classified == expected)
    if not is_ok:
        failed_count += 1
    status = "[PASS]" if is_ok else f"[FAIL: got {classified}]"
    print(f"{status:18s} | \"{q}\" -> {classified}")

if failed_count > 0:
    print(f"\n❌ {failed_count} classification tests failed!")
    sys.exit(1)
else:
    print("\n✔ All intent classification tests passed!")
