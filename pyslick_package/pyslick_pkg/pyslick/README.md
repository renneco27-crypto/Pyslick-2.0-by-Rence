# Pyslick 2.0 — Architecture & Developer Guide

> **Core Philosophy:** High-accuracy local semantic code navigation, call-graph comprehension, and context packing for potato PCs (Ryzen 3 / 8GB RAM CPU). Local operations find and extract exact functions with zero hallucinations, while complex reasoning & editing are packed for web LLMs (DeepSeek, Claude Web) or opened directly in VS Code (`code -g`).

---

## 1. System Architecture

```
                                  [ User Query in PowerShell ]
                                               │
                                               ▼
                              ┌──────────────────────────────────┐
                              │  Universal Startup Auto-Graphify │
                              │  (Ensures graphify-out/graph.json│
                              │   via code-only extraction)      │
                              └────────────────┬─────────────────┘
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │   Stage 1: Fast Candidate Retriever (Top 20) │
                        ├──────────────────────────────────────────────┤
                        │ • Custom BM25 Index (text_index.py)          │
                        │ • AST Symbol Graph & God-Nodes (graphify.py) │
                        │ • RapidFuzz String & Token Matching          │
                        └──────────────────────┬───────────────────────┘
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │   Stage 2: Semantic Cross-Encoder Reranker   │
                        ├──────────────────────────────────────────────┤
                        │ • CPU-optimized ONNX Cross-Encoder           │
                        │ • Scores Natural Language Query vs Symbols   │
                        │ • 100% Deterministic & Hallucination-Free    │
                        └──────────────────────┬───────────────────────┘
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │       Tree-Sitter AST Scope Snapper          │
                        ├──────────────────────────────────────────────┤
                        │ • Expands hit line to full enclosing function│
                        │ • Never outputs truncated snippet windows    │
                        └──────────────────────┬───────────────────────┘
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │                  Outputs                     │
                        ├──────────────────────────────────────────────┤
                        │ 1. Terminal: Ranked function blocks + ranges │
                        │ 2. VS Code: `code -g filepath:line`          │
                        │ 3. Clipboard: Hierarchical DeepSeek/Web LLM  │
                        │    Context Bundle (.pyslick_context/)        │
                        └──────────────────────────────────────────────┘
```

---

## 2. Directory Layout & Module Responsibilities

```
pyslick_package/pyslick_pkg/pyslick/
│
├── __init__.py            ← CLI entry point, argument parsing & dispatch (927 lines)
├── agent.py               ← Agentic workflow & fallback router (6030 lines)
├── recon.py               ← Guided recon & investigation pipeline (631 lines)
├── recon_semantic.py      ← 2-stage hybrid semantic search engine (936 lines)
├── recon_pack.py          ← Context bundler for DeepSeek / Claude Web (458 lines)
├── query.py               ← Fast AST symbol & dependency lookup (387 lines)
├── toolbox.py             ← mode_ls, mode_lines, mode_grep & code launcher (462 lines)
├── text_index.py          ← In-memory BM25 indexer over comments/strings/JSX (502 lines)
├── graphify.py            ← Local Python AST caller/callee graph builder (447 lines)
├── graphify_sitter.py     ← Tree-sitter AST extraction for TS/TSX (159 lines)
├── find_nearest_nodes.py  ← Fuzzy & graph traversal across graphify-out (277 lines)
├── find_stray_symbols.py  ← Unmatched JSX/TSX tag scanner (68 lines)
├── indentation.py         ← Scope & brace balance validator (96 lines)
├── jsx_tag_checker.py     ← Deep JSX/HTML tag hierarchy checker (171 lines)
├── repomap.py             ← Multi-language AST tag extractor via tree-sitter
├── synonyms.py            ← Semantic domain vocabulary & query expansion (133 lines)
├── autoloop.py            ← Command testing & auto-verification loop
├── clipboard.py           ← Instant stdout capture & clipboard synchronization
└── watch.py               ← Project watcher for `# pyslick?` review tags
```

---

## 3. Key Upgrades & Improvements

### A. 2-Stage Hybrid Semantic Search (`pyslick grep "<concept>"`)
- **Stage 1 (Filter)**: In-memory BM25 + AST node labels collect top 20 candidate code regions and comments.
- **Stage 2 (Rerank)**: Fast ONNX Cross-Encoder evaluates query semantics against candidate docstrings, comments, and signatures.
- **AST Enclosure**: Every match automatically snaps to its enclosing function end-to-end via `repomap.py` / `graphify.py`.

### B. Universal Auto-Graphify
- Running any command in a fresh repository automatically verifies if `graphify-out/graph.json` exists.
- If missing, it transparently triggers `graphify extract . --code-only` once with status feedback, ensuring rich graph awareness for all commands.

### C. Deprecation of `patchit` → Direct VS Code & DeepSeek Web Pack
- Manual patching via `patchit` is replaced by:
  1. `code -g <filepath>:<line>` to open and navigate immediately in VS Code.
  2. `pyslick recon-pack` / clipboard capture to export a clean Hierarchical Context bundle (Overview + Call Graph + Complete Functions) ready for 1-click paste into DeepSeek or Claude Web.

### D. Unified File & Directory Scanners
- `pyslick indentation <file>` — checks brace & indentation drift on a single file.
- `pyslick jsx-check <file>` — checks unclosed/mismatched JSX tags on a single file.
- `pyslick find-stray-symbols <file_or_dir>` — supports both individual `.tsx`/`.jsx` files and recursive project directories.

---

## 4. CLI Command Reference

| Command | Description |
|---|---|
| `pyslick "what does this codebase do"` | Universal overview: God nodes, connectivity degree, opening architectural comments |
| `pyslick grep "<natural language query>"` | Semantic hybrid search finding nearest functions end-to-end |
| `pyslick grep <file> <pattern> [--context N]` | Exact pattern/regex search inside a file with AST function expansion |
| `pyslick recon "<directive>"` | Guided code inspection & component isolation pipeline |
| `pyslick recon-pack "<directive>"` | Pack complete context into `.pyslick_context/` and clipboard for DeepSeek Web |
| `pyslick query "<directive>"` | Fast non-LLM symbol & caller/callee lookup |
| `pyslick ls [root]` | Clean grouped directory listing (skipping dependencies & build artifacts) |
| `pyslick lines <file> [range]` | View file with line numbers (e.g. `10-50` or `--head 20`) |
| `pyslick indentation <file>` | Validate scope alignment and `{}` brace balance |
| `pyslick jsx-check <file>` | Comprehensive JSX/HTML tag hierarchy validation |
| `pyslick find-stray-symbols <path>` | Scan a `.tsx`/`.jsx` file or entire folder for stray/unclosed tags |
| `pyslick copy` | Copy last command output to Windows clipboard |
