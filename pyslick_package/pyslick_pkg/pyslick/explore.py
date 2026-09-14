"""explore.py — local LLM recon loop.

Read-only. Locked to the repo root. No edits, no writes except the final
findings file. Prompt-encoded tool protocol (see _SYSTEM below):

  <cmd>Get-ChildItem src</cmd>     -> run the command, feed output back
  <done>...your answer...</done>   -> stop, write findings

The model drives; we gate every command against a whitelist and refuse
anything that could mutate the repo or escape it.

Commit 1 scope: load the model and do ONE plain round-trip, no tools.
This is the smoke test that llama-cpp + Qwen actually responds via our
chat wrapper. The tool loop lands in the next commit.
"""

import os
import re
import sys
import time
from pathlib import Path

# ── Model load (reuse llm.py's finder + singleton behaviour) ──────────────
_MODEL_DIR = os.path.join(os.path.expanduser("~"), ".pyslick", "models")


def _find_model() -> str | None:
    import glob
    cands = sorted(glob.glob(os.path.join(_MODEL_DIR, "*.gguf")))
    if not cands:
        return None
    # prefer instruct models if several are present
    instruct = [c for c in cands if "instruct" in os.path.basename(c).lower()]
    return (instruct or cands)[0]


def _load_model():
    path = _find_model()
    if not path:
        return None, None
    try:
        from llama_cpp import Llama
    except ImportError:
        return None, None
    llm = Llama(
        model_path=path,
        n_ctx=2048,           # bigger than llm.py's 512: we feed tool output
        n_threads=max(1, os.cpu_count() or 2),
        verbose=False,
    )
    return llm, path


# ── Prompt scaffolding (commit 1: no tool loop yet, just framing) ────────
_SYSTEM = (
    "You are a shell-command agent. You do NOT answer from memory. You "
    "answer ONLY by running commands and reading their real output.\n\n"
    "OUTPUT FORMAT — every reply must contain EXACTLY ONE of these two "
    "forms, nothing else:\n\n"
    "  <cmd>Select-String -Path src\\lib\\supabaseServer.ts -Pattern admin</cmd>\n\n"
    "or, when you have seen enough real output:\n\n"
    "  <done>\n"
    "  your findings, citing file:line you actually saw\n"
    "  </done>\n\n"
    "RULES:\n"
    "- Never write prose outside the tags. Prose is a failure.\n"
    "- Never invent commands. If unsure of syntax, run "
    "`Get-ChildItem` first to look around.\n"
    "- One <cmd> per reply.\n"
    "- Allowed commands ONLY: Get-ChildItem, Get-Content, Select-String, "
    "rg, ls, cat, find, tree, head, tail, wc.\n"
    "- No pipes, no semicolons, no redirection. Single command, simple args.\n"
    "- You must run at least one command before you may emit <done>.\n"
)


def _format_chat(llm, system: str, user: str) -> str:
    """Ask llama-cpp to apply the model's own chat template, if it knows
    one. Falls back to a plain prompt if the template is missing."""
    try:
        return llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=256,
            temperature=0.1,
        )["choices"][0]["message"]["content"]
    except Exception:
        # fall back to completion interface with a hand-rolled prompt
        prompt = f"{system}\n\nUser: {user}\nAssistant:"
        return llm(prompt, max_tokens=256, temperature=0.1)["choices"][0]["text"]


# ── Tool loop ─────────────────────────────────────────────────────────────
_ALLOWED_CMDS = {
    "get-childitem", "get-content", "select-string", "rg", "ls", "cat",
    "find", "tree", "head", "tail", "wc",
}
_DENY_TOKENS = ("|", ";", "&&", "||", ">", "<", "`", "$(", "&")
_MAX_TURNS = 12
_MAX_OUTPUT_LINES = 60
_MAX_FEEDBACK_CHARS = 6000


def _repo_root() -> str:
    """The repo we let the model explore. Walk up from cwd until we find
    .git or pyproject.toml; fall back to cwd."""
    p = Path(os.getcwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return str(candidate)
    return str(p)


def _is_safe(argv: list[str], root: str) -> tuple[bool, str]:
    if not argv:
        return False, "empty command"
    head = os.path.basename(argv[0]).lower()
    if head not in _ALLOWED_CMDS:
        return False, f"command '{head}' not in whitelist"
    # Block shell metachars anywhere in the argv
    joined = " ".join(argv)
    for bad in _DENY_TOKENS:
        if bad in joined:
            return False, f"shell metachar '{bad}' rejected"
    # Path escape check: any arg with .. or absolute outside root
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        try:
            resolved = (Path(root) / a).resolve() if not os.path.isabs(a) else Path(a).resolve()
        except Exception:
            return False, f"unresolvable path: {a}"
        if not str(resolved).startswith(root):
            return False, f"path escapes repo: {a}"
    return True, ""


def _run_cmd(argv: list[str], root: str) -> str:
    try:
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return "(command timed out after 15s)"
    except FileNotFoundError:
        return f"(command not found: {argv[0]})"
    except Exception as e:
        return f"(command error: {e})"
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    if len(lines) > _MAX_OUTPUT_LINES:
        out = "\n".join(lines[:_MAX_OUTPUT_LINES]) + f"\n... +{len(lines)-_MAX_OUTPUT_LINES} more lines"
    if len(out) > _MAX_FEEDBACK_CHARS:
        out = out[:_MAX_FEEDBACK_CHARS] + "\n...(truncated)"
    return out or "(no output)"


_CMD_RX = re.compile(r"<cmd>\s*(.+?)\s*</cmd>", re.DOTALL | re.IGNORECASE)
_DONE_RX = re.compile(r"<done>(.*?)</done>", re.DOTALL | re.IGNORECASE)


def _shlex_split(s: str) -> list[str]:
    import shlex
    try:
        return shlex.split(s, posix=False)
    except ValueError:
        return []


def main(question: str) -> int:
    llm, path = _load_model()
    if llm is None:
        print("explore: no model found or llama-cpp-python not installed.")
        print(f"         looked in {_MODEL_DIR}")
        return 1

    root = _repo_root()
    print(f"explore: model    = {os.path.basename(path)}")
    print(f"explore: repo     = {root}")
    print(f"explore: question = {question!r}")
    print("─" * 60)

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": question},
    ]

    _protocol_strikes = 0
    for turn in range(1, _MAX_TURNS + 1):
        t0 = time.time()
        try:
            reply = llm.create_chat_completion(
                messages=messages, max_tokens=384, temperature=0.1,
            )["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[turn {turn}] generation error: {e}")
            return 2
        dt = time.time() - t0

        # done?
        done_m = _DONE_RX.search(reply)
        if done_m:
            findings = done_m.group(1).strip()
            print(f"[turn {turn}] DONE ({dt:.1f}s)")
            print("─" * 60)
            print(findings)
            print("─" * 60)
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(root, f"pyslick_findings_{ts}.txt")
            try:
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(f"# {question}\n\n{findings}\n")
                print(f"explore: findings written to {out_path}")
            except Exception as e:
                print(f"explore: could not write findings: {e}")
            return 0

        # command?
        cmd_m = _CMD_RX.search(reply)
        if not cmd_m:
            _protocol_strikes += 1
            print(f"[turn {turn}] protocol violation #{_protocol_strikes} — nudging.")
            if _protocol_strikes >= 3:
                print("explore: model cannot follow the protocol. Aborting.")
                print(reply)
                return 3
            print(f"    model said: {reply[:120]!r}")
            messages.append({"role": "assistant", "content": reply})
            messages.append({
                "role": "user",
                "content": (
                    "FORMAT ERROR. You wrote prose. Reply with EXACTLY ONE "
                    "<cmd>...</cmd> block and nothing else. No explanation, "
                    "no memory-based answer. Start with a simple listing "
                    "command like: <cmd>Get-ChildItem src\\lib</cmd>"
                ),
            })
            continue

        raw_cmd = cmd_m.group(1).strip()
        argv = _shlex_split(raw_cmd)
        ok, why = _is_safe(argv, root)
        if ok:
            print(f"[turn {turn}] $ {raw_cmd}")
            output = _run_cmd(argv, root)
        else:
            print(f"[turn {turn}] REFUSED: {raw_cmd}  ({why})")
            output = f"REFUSED: {why}"

        # show a peek
        peek = output.splitlines()[:6]
        for ln in peek:
            print(f"    {ln}")
        if len(output.splitlines()) > 6:
            print(f"    ... ({len(output.splitlines())-6} more lines fed back)")

        messages.append({"role": "assistant", "content": reply})
        messages.append({
            "role": "user",
            "content": f"Command output:\n{output}\n\nNext action?",
        })

    print("explore: hit max turns without <done>.")
    return 4


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What does this repo do? Explore it."
    sys.exit(main(q))