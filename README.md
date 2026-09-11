[README (1).md](https://github.com/user-attachments/files/32055815/README.1.md)
<div align="center">

```
 ██████╗ ██╗   ██╗███████╗██╗     ██╗ ██████╗██╗  ██╗
 ██╔══██╗╚██╗ ██╔╝██╔════╝██║     ██║██╔════╝██║ ██╔╝
 ██████╔╝ ╚████╔╝ ███████╗██║     ██║██║     █████╔╝
 ██╔═══╝   ╚██╔╝  ╚════██║██║     ██║██║     ██╔═██╗
 ██║        ██║   ███████║███████╗██║╚██████╗██║  ██╗
 ╚═╝        ╚═╝   ╚══════╝╚══════╝╚═╝ ╚═════╝╚═╝  ╚═╝
```

**Smart code navigator + patcher for your projects — no LLM required.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.2.0-brightgreen?style=flat-square)]()
[![License](https://img.shields.io/badge/license-MIT-orange?style=flat-square)]()
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square)]()

</div>

---

## What is pyslick?

pyslick is a **local CLI tool** that lets you navigate, inspect, and safely patch codebases using plain English — without sending your code to any AI API.

You ask it a question like:

```powershell
pyslick query "what renders the mic button"
```

And it zeroes in on the right function, its callers, its callees, and the exact file and line range — in under a second, even on a slow machine.

It's built for developers who want the speed of a fuzzy search with the precision of an AST call-graph, wrapped in PowerShell-friendly commands that feel native on Windows.

---

## Features

| Feature | What it does |
|---|---|
| `query` | Fuzzy-matches your question against filenames + symbols, then walks the AST call graph to return the matched function plus its full dependency chain |
| `patchit` | Safe find-and-replace with Python syntax validation, auto-indentation fix, per-file backup, and a git diff after every write |
| `recon` | Guided flow: find → inspect → patch → confirm → checkpoint |
| `ls` | Recursive file listing (`Get-ChildItem` equivalent) |
| `lines` | Numbered file content (`Get-Content` equivalent) |
| `grep` | Multi-term search with context lines |
| `jsx-check` | Validates JSX tag structure before and after edits |
| `graphify-query` | Cross-file dependency linking (HTML ↔ CSS ↔ JS), no API needed |
| `smoke.py` | 6-phase test suite — run after any change to verify nothing broke |
| `debugger.py` | Runtime crash catcher with structured reports and a heuristic fix database |

---

## Installation

### Windows (recommended — double-click)

1. Download and unzip the release
2. Open the unzipped folder in Explorer
3. Navigate into `pyslick_pkg\`
4. Double-click **`install.bat`**

That's it. The installer registers `pyslick` on your PATH and confirms the setup.

### Manual (PowerShell / macOS / Linux)

```powershell
# Navigate to the inner package folder (the one with pyproject.toml)
cd pyslick_pkg\pyslick_pkg

# Install globally (editable mode — changes apply immediately)
pip install -e .
```

**Requirements:** Python 3.10+ with pip. No other setup needed.

To reinstall after pulling updates:

```powershell
pip install -e . --force-reinstall --no-deps
```

### Optional: TSX / tree-sitter support

```powershell
pip install -e ".[tsx]"
```

---

## Quick start

Run all commands from your **project root** (the folder that contains your source files).

```powershell
# Find what's relevant to your question — no full-codebase scan
pyslick query "what handles the login button click"

# List all files recursively
pyslick ls
pyslick ls --full-path

# View a file with line numbers
pyslick lines src/app/page.tsx

# Search across a file for multiple terms
pyslick grep index.html "addEventListener" "electronAPI" --context 2

# Inspect a file before touching it
pyslick patchit src/app/page.tsx -l

# Safe find-and-replace (backs up, validates, diffs)
pyslick patchit src/app/page.tsx -f
# → prompts: Find: / Replace:

# Guided patch session (the interactive flow)
pyslick recon "the mic button needs to be bigger"
```

---

## How `query` works

No LLM. No API calls. No reading your whole repo.

1. **Fuzzy match** your sentence against filenames and paths using `rapidfuzz`
2. **Narrow to 2–3 candidate files** (not the whole project)
3. **AST call-graph walk** on those files — finds the matched function plus its direct callers and callees (configurable depth)
4. **Prints a compact report:** file, function, line range, docstring, what it calls, what calls it

If you've already run `graphify extract .` in your project, `query` uses your existing graph nodes for an even sharper hit.

**Flags:**

```powershell
pyslick query "..." --top-files 3     # how many candidate files to consider
pyslick query "..." --top-symbols 5   # how many matched symbols to report
pyslick query "..." --depth 3         # call-graph walk depth
pyslick query "..." --full            # also dump matched source lines
pyslick query "..." --root ./src      # project root if not running from there
```

---

## How `patchit` works safely

Previous code patchers were dangerous — a misfire could wipe a file. pyslick fixes that:

| Safety layer | What it does |
|---|---|
| Per-file backup | Every write copies the original to `.pyslick_backups/` first |
| Python syntax check | `.py` edits are `ast.parse`-validated before writing — bad syntax is blocked |
| Auto-indentation | Replacement text is re-indented to match the block it replaces |
| Diff after write | Every successful write runs `git diff` (or a diff vs the backup) and prints it |
| Drastic shrinkage guard | If a paste would remove most of a file, you must type `OVERWRITE` in full |

---

## Smoke testing

Run after any change to pyslick itself to make sure nothing broke:

```powershell
cd pyslick_pkg\pyslick_pkg
python smoke.py              # full suite (6 phases)
python smoke.py --only static    # syntax + imports + API surface only
python smoke.py --only unit      # command-level tests against a fake project
python smoke.py --only build     # pnpm build + dev boot + ESLint (Next.js/Vite projects)
python smoke.py --fix            # on failure, asks Claude API for a suggested fix
python smoke.py --watch          # re-runs automatically when any .py file changes
```

---

## Debugging

Wrap any command to get a structured crash report instead of a raw traceback:

```powershell
python debugger.py query "make the mic button bigger"
python debugger.py patchit src/app/page.tsx -l
python debugger.py --wrap-cmd "pnpm build"
python debugger.py --scan-log build.txt
python debugger.py --fix <any of the above>
```

The debugger covers 13 common crashes (missing `rapidfuzz`, missing `graph.json`, ANSI encoding on Windows, `EADDRINUSE`, TypeScript errors, etc.) and gives you the fix without the API for the most common ones.

---

## Project structure

```
pyslick_pkg/
└── pyslick_pkg/
    ├── install.bat              ← Windows one-click installer
    ├── pyproject.toml
    ├── smoke.py                 ← test suite
    ├── debugger.py              ← crash catcher
    └── pyslick/
        ├── __main__.py          ← CLI entry point
        ├── __init__.py
        ├── query.py             ← fuzzy match + AST call-graph
        ├── patchit.py           ← safe find/replace + validation
        ├── recon.py             ← guided patch session
        ├── graphify.py          ← Python AST call-graph extractor
        ├── graphify_sitter.py   ← tree-sitter graph (JS/TS/JSX)
        ├── webdesign.py         ← HTML ↔ CSS ↔ JS cross-file linker
        ├── find_nearest_nodes.py
        ├── find_stray_symbols.py
        ├── indentation.py
        ├── jsx_tag_checker.py
        ├── llm.py               ← optional SmolLM2 hook (drop-in)
        └── toolbox.py
```

---

## Optional: local LLM assist

pyslick works 100% offline with no LLM. But if you want smarter query rewriting, you can drop in a local model:

1. Download [SmolLM2-135M-Instruct Q4 GGUF](https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct-GGUF) (~90MB)
2. Place it in `pyslick/models/smollm2-135m-instruct-q4.gguf`
3. `pip install llama-cpp-python`

The LLM is used only to reword your query before fuzzy matching — everything else stays local and deterministic.

---

## Roadmap

- [ ] `pyslick backups --prune` (age-based backup cleanup)
- [ ] Design-aware query (CSS variable tracing, Tailwind class lookup)
- [ ] User-annotated graph nodes via Markdown (describe your own nodes, pyslick learns them)
- [ ] `pyslick diff` — compare two checkpoints side by side
- [ ] VS Code extension

---

## License

MIT — do what you want, keep the credit.

---

<div align="center">
Built for developers who move fast and want their tools to keep up.
</div>


<img width="1289" height="621" alt="image" src="https://github.com/user-attachments/assets/4fd21de3-41de-48b1-b2bf-994d1a9c18ed" />
<img width="1156" height="517" alt="image" src="https://github.com/user-attachments/assets/b60194c8-6baf-4de4-993b-23d2009196c4" />
<img width="1365" height="655" alt="image" src="https://github.com/user-attachments/assets/7da68aa7-daa1-4668-8b26-549cdcd749c8" />


## 🚀 Recent Features & Capabilities (v0.2.0)

### 🧠 1. App Summary & Architecture Overview
* **Instant Project Summarization**: Ask `"what does this program do"`, `"what does this app do"`, or `"whats this project"` to generate an immediate high-level architecture overview without an LLM.
* **Top 5 God Nodes & First Comments**: Reads graph centrality from `graph.json` or AST analysis to highlight the 5 most connected modules in the codebase alongside their initial purpose comments.
* **Separator Cleaning**: Automatically ignores ASCII banner borders (`===`, `---`, `///`) to pull pure semantic comments.

### 🔍 2. Node-First Encapsulation & Smart Scope Resolution
* **Nearest Node Resolution**: Querying any keyword (e.g. `pyslick "weights"`) searches graph nodes, AST definitions, and comment blocks first.
* **Enclosing Scope Detection**: Automatically discovers the parent function, class, struct, or code block enclosing the matched symbol across Python, JavaScript, TypeScript, Kotlin, Java, C/C++, Rust, and Go.
* **$\le$ 20-Line Smart Compact View**: If the enclosing function exceeds 20 lines, PySlick displays a clean preview (header, docstring, first lines, and closing return) plus an exact PowerShell one-liner (`Get-Content | Select-Object`) to view the rest.
* **End-to-End Caller Tracking**: Displays all functions and line numbers across the entire codebase that call the target function:
  ```text
  Called by (functions & line numbers end-to-end):
    ← def _run_local_agent in agent.py (called at L2449, function scope L2219-L3211)


  pyslick agent -override comments "show me comments in query.py"
pyslick agent -override file_info "some weirdly phrased query"
pyslick agent -override grep "the pattern I'm looking for"
