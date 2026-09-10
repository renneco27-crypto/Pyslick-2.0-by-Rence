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
    ("git staus", "git"),
    ("show me the coments", "comments"),
    ("closest functon to line 20", "nearest"),
    ("how autoloop conects to patchit", "connect"),
    ("whats the use of test_file", "file_info"),
]

print("=== Testing Confidence-Scored Intent Classification ===")
for q, expected in queries:
    classified = agent._classify_intent(q)
    status = "[PASS]" if classified == expected else f"[FAIL: got {classified}]"
    print(f"{status:18s} | \"{q}\" -> {classified}")
