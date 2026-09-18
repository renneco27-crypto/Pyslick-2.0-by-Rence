---
description: Standard workflow for using Pyslick for codebase navigation and continuous 1-10 quality evaluation and refinement.
globs: ["**/*"]
always_on: true
---

# Pyslick-First Codebase Workflow & Continuous Improvement Protocol

## 1. Pyslick Navigation & Investigation Standard

When exploring, searching, orienting, or investigating codebases:
1. **Repository Overview**: Always run `pyslick "what does this codebase do"` on fresh codebases to extract God-nodes and architectural comments.
2. **Semantic & Concept Grep**: Run `pyslick grep "<natural language query>"` to locate relevant code expanded to full AST function boundaries.
3. **Multi-Question Queries**: Pass compound queries directly (e.g. `pyslick "where is X and what triggers Y?"`) to leverage `decompose.py`.
4. **Code Quality Checks**: Use `pyslick find-stray-symbols <path>`, `pyslick indentation <file>`, and `pyslick jsx-check <file>` for structure validation.
5. **Context Bundling for Web AI**: Use `pyslick recon-pack "<directive>"` to package complete context for DeepSeek Chat or Claude Web.
6. **Direct Editor Navigation**: Open target locations in VS Code via `code -g <filepath>:<line>`. Avoid using deprecated `patchit`.

## 2. Query Quality Evaluation (1-10 Rating)

After running any `pyslick` search or recon command:
- **Rate the output quality on a 1-10 scale**:
  - **9-10**: Exact function/component found with complete AST boundaries and zero irrelevant noise.
  - **8**: Relevant file and function identified with minor extraneous context.
  - **< 8 (Deficient/Garbage)**: Misrouted intent, missed target functions, truncated snippets, or zero matches.

## 3. Continuous Auto-Improvement Gate

Whenever a query scores `< 8`:
1. **Diagnose the Failure Root Cause**:
   - *Tokenization / Lexical mismatch*: Stemming or stopword stripping gap in `text_index.py`.
   - *Ranking failure*: Rapidfuzz / BM25 score thresholding in `recon_semantic.py` or `toolbox.py`.
   - *AST Snapping issue*: Tag extraction or scope boundary detection in `repomap.py` or `graphify.py`.
   - *Graph connectivity gap*: Thin graph or missing link in `graphify-out/graph.json`.
2. **Implement & Test Fix**:
   - Fix the issue in the Pyslick codebase using lightweight, CPU-efficient patterns (ONNX / BM25 / Tree-sitter).
   - Re-run the failing query to confirm the score is elevated to ≥ 8 before proceeding.
