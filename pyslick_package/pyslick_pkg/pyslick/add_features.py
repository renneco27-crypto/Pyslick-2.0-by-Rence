from pathlib import Path
import shutil, subprocess, sys
from datetime import datetime

ROOT = Path.cwd()
BK = ROOT / ".pyslick" / "backups" / ("features-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
BK.mkdir(parents=True, exist_ok=True)

def read(p):
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", errors="strict")

def write(p, t):
    p.write_bytes(t.encode("utf-8"))

def compile_ok(p):
    r = subprocess.run([sys.executable, "-m", "py_compile", str(p)],
                       capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout)

def safe_edit(p, old, new, label, count=1):
    t = read(p)
    if new in t and old not in t:
        print("  SKIPPED  %s" % label)
        return True
    if old not in t:
        print("  FAILED   %s (anchor not found)" % label)
        return False
    shutil.copy2(p, BK / p.name)
    write(p, t.replace(old, new, count))
    ok, err = compile_ok(p)
    if not ok:
        shutil.copy2(BK / p.name, p)
        print("  ROLLBACK %s: %s" % (label, err.splitlines()[0] if err else ""))
        return False
    print("  APPLIED  %s" % label)
    return True

# ===========================================================================
# FEATURE 1: graphify graph <func> [--format=ascii|mermaid] [--depth N] [--direction in|out|both]
# ===========================================================================

print("\n=== Feature 1: graphify graph ===")

gf = ROOT / "graphify.py"

# 1a. Insert the new show_graph function just before show_stats.
show_graph_fn = '''def show_graph(symbol_name: str, root: str = ".", fmt: str = "ascii",
               depth: int = 1, direction: str = "both"):
    """Render callers and callees of a symbol as a tree (ascii) or flowchart (mermaid).

    direction: "in" = only callers, "out" = only callees, "both" = both.
    depth: how many levels to expand. depth=1 shows immediate neighbours only.
    """
    BOLD = "\\033[1m"
    CYAN = "\\033[96m"
    GREEN = "\\033[92m"
    DIM = "\\033[2m"
    RST = "\\033[0m"

    graph = load_codebase_graph(root)
    nodes = graph.get("nodes", [])

    matched = [n for n in nodes if n.get("label", "").lower() == symbol_name.lower()]
    if not matched:
        matched = [n for n in nodes if symbol_name.lower() in n.get("label", "").lower()]
    if not matched:
        print("No symbol matching '%s' found in call graph." % symbol_name)
        return

    # Build a lookup from call-target label -> node for recursive expansion.
    by_label = {}
    for n in nodes:
        lbl = (n.get("label") or "").lower()
        if lbl:
            by_label[lbl] = n

    def _targets(node, key):
        raw = node.get(key, []) or []
        out = []
        for entry in raw:
            name = entry.split("::")[-1] if "::" in entry else entry
            out.append(name)
        return out

    def _expand(label, kind, level, seen):
        if level > depth:
            return
        node = by_label.get(label.lower())
        if node is None:
            return
        kids = _targets(node, "callers" if kind == "in" else "callees")
        for k in kids:
            if k.lower() in seen:
                continue
            seen.add(k.lower())
            marker = "←" if kind == "in" else "→"
            print("  " * level + marker + " " + k)
            _expand(k, kind, level + 1, seen)

    node = matched[0]
    root_label = node.get("label") or symbol_name

    if fmt == "mermaid":
        print("```mermaid")
        print("flowchart LR")
        edges = []
        if direction in ("in", "both"):
            for c in _targets(node, "callers"):
                edges.append('  %s --> %s' % (c.replace(" ", "_"), root_label.replace(" ", "_")))
        if direction in ("out", "both"):
            for c in _targets(node, "callees"):
                edges.append('  %s --> %s' % (root_label.replace(" ", "_"), c.replace(" ", "_")))
        if not edges:
            print("  %s" % root_label.replace(" ", "_"))
        else:
            for e in edges:
                print(e)
        print("```")
        return

    # ascii
    print("")
    print("%s%s%s" % (BOLD, CYAN, root_label))
    print("%s(%s:%s)%s" % (DIM, node.get("source_file", "?"),
                          node.get("source_location", "?"), RST))
    print("")

    if direction in ("in", "both"):
        callers = _targets(node, "callers")
        print("%sIncoming (callers)%s: %d" % (BOLD, RST, len(callers)))
        if not callers:
            print("  (none)")
        else:
            for c in callers:
                print("  ← %s" % c)
                _expand(c, "in", 2, {c.lower(), root_label.lower()})
        print("")

    if direction in ("out", "both"):
        callees = _targets(node, "callees")
        print("%sOutgoing (callees)%s: %d" % (BOLD, RST, len(callees)))
        if not callees:
            print("  (none)")
        else:
            for c in callees:
                print("  → %s" % c)
                _expand(c, "out", 2, {c.lower(), root_label.lower()})
        print("")


'''

show_stats_anchor = "def show_stats(root: str = \".\"):"
if show_graph_fn in read(gf):
    print("  SKIPPED  show_graph already present")
else:
    safe_edit(gf, show_stats_anchor, show_graph_fn + show_stats_anchor, "insert show_graph function")

# 1b. Wire the subcommand in cli_main. Need to find how subcommands are dispatched.
# Print the relevant part of cli_main first so we can patch precisely.
cli_text = read(gf)
idx = cli_text.find("def cli_main")
print("\n  --- cli_main (first 60 lines after def) ---")
for i, ln in enumerate(cli_text[idx:idx + 3000].split("\n")[:60]):
    print("    %s" % ln)
