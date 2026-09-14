#!/usr/bin/env python3
"""
router.py — LLM-based directive router for pyslick.

Loads a local Qwen GGUF via llama_cpp and returns one of the valid
pyslick intents. Falls back to rule-based routing if the model is
unavailable. Never raises.

Usage:
    python router.py "where is the mic button"
    from router import route
    route("where is the mic button")  # -> "recon"
"""

import os
import sys
import re

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)


VALID_INTENTS = [
    "query",
    "recon",          # universal recon pack
    "recon_full",     # comprehensive recon + git snapshot
    "agent",
    "recon_pack",
    "graph",
    "git_status",
    "git_log",
    "git_rollback",
    "git_checkpoint",
    "git_stash",
    "ls",
    "lines",
    "grep",
    "help",
    "comment_scan",
]


MODEL_PATHS = [
    os.path.join(THIS_DIR, "models", "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"),
    os.path.join(os.path.expanduser("~"), ".pyslick", "models",
                 "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"),
]


_SYSTEM_PROMPT = """You are pyslick's router. Given a user directive, output ONE intent name.

Valid intents (pick exactly one, lowercase, no explanation):

query          - fast file/line lookup: "show me X", "where is X defined"
recon          - understand or explore: "what does this do", "explain X"
recon_full     - comprehensive read-only recon: "comprehensive recon of X", "describe the codebase", "overview of everything", "full recon"
agent          - edit or change code: "make X bigger", "add Y", "fix Z"
recon_pack     - gather context: "pack X", "gather files for X"
graph          - ONLY when the directive asks who CALLS or USES something: "who calls X", "what calls X", "what uses X", "call graph of X", "end to end of X". If the directive asks WHERE or WHAT something IS, use query or recon instead.
git_status     - "git status", "what changed"
git_log        - "git log", "recent commits", "show history"
git_rollback   - "undo my changes", "revert", "rollback to X"
git_checkpoint - "checkpoint", "save progress"
git_stash      - "stash", "stash my changes", "park changes"
ls             - "list files", "what files are here"
lines          - "show lines 10-20 of X"
grep           - "grep for X", "search for X in files"
help           - "help", "what can you do"
comment_scan   - "scan comments", "find TODOs"

Rules:
- If the directive asks a question (what/where/why/how/explain), prefer query or recon.
- If the directive says "make", "add", "remove", "change", "fix", "update", prefer agent.
- Never output anything except the intent name.

Output ONLY the intent name. Nothing else."""


_model = None
_model_tried = False


def _load_model():
    global _model, _model_tried
    if _model is not None:
        return _model
    if _model_tried:
        return None
    _model_tried = True

    try:
        from llama_cpp import Llama
    except ImportError:
        return None

    for path in MODEL_PATHS:
        if os.path.isfile(path):
            try:
                _model = Llama(
                    model_path=path,
                    n_ctx=1024,
                    n_threads=4,
                    verbose=False,
                )
                return _model
            except Exception:
                continue
    return None


def _rule_route(directive: str) -> str:
    """Cheap rule-based fallback when no model is available."""
    d = (directive or "").lower().strip()
    if not d:
        return "recon"

    # Full recon keywords (checked first — most specific)
    if any(k in d for k in (
        "comprehensive recon", "full recon", "describe the codebase",
        "overview of everything", "list all files and describe",
        "scan everything", "recon everything",
    )):
        return "recon_full"

    # Graph (who calls / what calls)
    if re.search(r"\b(who|what)\s+(calls|uses|invokes|depends on)\b", d):
        return "graph"
    if "call graph" in d or "end to end" in d:
        return "graph"

    # Git
    if d.startswith("git ") or d in ("git", "status", "log"):
        if "log" in d or "history" in d or "commits" in d:
            return "git_log"
        if "rollback" in d or "revert" in d or "undo" in d:
            return "git_rollback"
        if "checkpoint" in d or "save" in d:
            return "git_checkpoint"
        if "stash" in d:
            return "git_stash"
        return "git_status"

    # Editing
    if re.search(r"\b(make|add|remove|delete|change|fix|update|edit|patch|rename)\b", d):
        return "agent"

    # Query / lookup
    if re.search(r"\b(where|show|find|locate)\b", d):
        return "query"
    if re.search(r"\b(what|why|how|explain|understand|describe)\b", d):
        return "recon"

    # Structural commands
    if re.match(r"^ls\b", d) or "list files" in d:
        return "ls"
    if re.search(r"\b(lines?\s+\d+|show lines)\b", d):
        return "lines"
    if "grep" in d or "search for" in d:
        return "grep"
    if "comment" in d and "scan" in d:
        return "comment_scan"
    if d in ("help", "?", "commands"):
        return "help"

    return "recon"


def _apply_guards(directive: str, intent: str) -> str:
    """Post-process the model's choice to fix known failure modes."""
    d = (directive or "").lower()
    if not intent or intent not in VALID_INTENTS:
        return _rule_route(directive)

    # If the model says graph but the directive is really a WHERE/WHAT query,
    # downgrade to recon.
    if intent == "graph":
        if re.search(r"\b(where|what)\s+is\b", d) or re.search(r"\bwhere\b", d):
            if not re.search(r"\b(calls|uses|invokes|depends)\b", d):
                return "recon"

    # If the model says query but the directive asks for comprehensive recon,
    # upgrade to recon_full.
    if intent in ("query", "recon") and any(k in d for k in (
        "comprehensive recon", "full recon", "describe the codebase",
        "overview of everything",
    )):
        return "recon_full"

    return intent


def route(directive: str) -> str:
    """
    Return the best pyslick intent for a directive.

    Always returns a valid intent string. Never raises.
    """
    if not directive or not directive.strip():
        return "recon"

    _full_d = (directive or "").lower()
    if any(k in _full_d for k in (
        "comprehensive recon", "full recon", "describe the codebase",
        "overview of everything", "list all files and describe",
        "scan everything", "recon everything",
    )):
        return "recon_full"

    model = _load_model()
    if model is None:
        return _apply_guards(directive, _rule_route(directive))

    prompt = (f"<|im_start|>system\n{_SYSTEM_PROMPT}<|im_end|>\n"
              f"<|im_start|>user\nDirective: {directive}<|im_end|>\n"
              f"<|im_start|>assistant\n")

    try:
        result = model(
            prompt,
            max_tokens=8,
            temperature=0.0,
            stop=["\n", "<|im_end|>"],
        )
        text = result["choices"][0]["text"].strip().lower()

        for intent in VALID_INTENTS:
            if intent in text:
                return _apply_guards(directive, intent)

        first_word = text.split()[0] if text.split() else ""
        if first_word in VALID_INTENTS:
            return _apply_guards(directive, first_word)
    except Exception:
        pass

    return _apply_guards(directive, _rule_route(directive))


def main():
    if len(sys.argv) < 2:
        print("usage: python router.py \"<directive>\"")
        sys.exit(1)
    arg = " ".join(sys.argv[1:])
    print(f"{arg!r} -> {route(arg)}")


if __name__ == "__main__":
    main()