"""
Pre-work check: before starting a task, ask git whether anyone has already
done it. Searches commit messages, branches, file history, and open PRs.
"""
import re
import subprocess
from shutil import which

try:
    from recon_semantic import _git_run
except ImportError:
    def _git_run(root, *args, timeout=8):
        try:
            r = subprocess.run(["git"] + list(args), cwd=root,
                               capture_output=True, text=True,
                               timeout=timeout)
            return r.returncode, (r.stdout or ""), (r.stderr or "")
        except Exception as e:
            return 1, "", str(e)


def _git(root, *args):
    return _git_run(root, *args)


def _extract_tokens(task):
    stop = {"the", "a", "an", "in", "on", "at", "for", "to", "of", "and",
            "or", "is", "are", "fix", "add", "make", "change", "update"}
    return [w for w in re.findall(r"[a-zA-Z0-9_]{3,}", task.lower())
            if w not in stop]


def search_commits(tokens, max_per_token=5):
    results = {}
    for tok in tokens:
        rc, out, _ = _git(".", "log", "--all", "--oneline",
                          f"--grep={tok}", "-i", f"-{max_per_token}")
        if out.strip():
            results[tok] = out.strip().splitlines()
    return results


def search_branches_for_file(filepath):
    rc, out, _ = _git(".", "branch", "-a", "--contains", "HEAD")
    branches = [b.strip().lstrip("* ") for b in out.splitlines() if b.strip()]
    results = {}
    for b in branches:
        rc2, log, _ = _git(".", "log", "--oneline",
                           f"HEAD..{b}", "--", filepath)
        if log.strip():
            results[b] = log.strip().splitlines()[:5]
    return results


def check_main_has_fix(filepath, tokens):
    if not filepath:
        return []
    hits = []
    for tok in tokens:
        rc, out, _ = _git(".", "log", "--oneline",
                          "HEAD..origin/main",
                          f"--grep={tok}", "-i", "--", filepath)
        if out.strip():
            hits.extend(out.strip().splitlines())
    return list(set(hits))[:10]


def check_open_prs(tokens):
    if not which("gh"):
        return {"available": False}
    rc, out, _ = _git(".", "pr", "list", "--state=open",
                      "--json", "number,title,headRefName")
    if not out.strip():
        return {"available": True, "prs": []}
    import json
    try:
        prs = json.loads(out)
    except Exception:
        return {"available": True, "prs": []}
    matched = [p for p in prs
               if not tokens
               or any(tok in p.get("title", "").lower() for tok in tokens)]
    return {"available": True, "prs": matched}


def run_pre_work(task, filepath=None):
    tokens = _extract_tokens(task)
    print(f"\n== PRE-WORK CHECK: {task!r} ==")
    print(f"tokens: {tokens}\n")

    commits = search_commits(tokens)
    print("-- COMMITS MENTIONING THIS TASK (all branches) --")
    if commits:
        for tok, lines in commits.items():
            print(f"\n  token: {tok}")
            for l in lines:
                print(f"    {l}")
    else:
        print("  (none)")

    if filepath:
        print(f"\n-- BRANCHES WITH UNMERGED CHANGES TO {filepath} --")
        branches = search_branches_for_file(filepath)
        if branches:
            for b, lines in branches.items():
                print(f"\n  {b}:")
                for l in lines:
                    print(f"    {l}")
        else:
            print("  (none)")

        print(f"\n-- MAIN'S COMMITS TO {filepath} NOT IN HEAD --")
        main_hits = check_main_has_fix(filepath, tokens)
        if main_hits:
            for l in main_hits:
                print(f"    {l}")
        else:
            print("  (none)")

    print("\n-- OPEN PRS MENTIONING THIS TASK --")
    prs = check_open_prs(tokens)
    if not prs.get("available"):
        print("  (gh CLI not installed)")
    elif not prs["prs"]:
        print("  (none)")
    else:
        for p in prs["prs"]:
            print(f"    #{p['number']}  {p['title']}  [{p['headRefName']}]")

    print("\n-- ACTION --")
    if commits or prs.get("prs"):
        print("  ! Relevant work may already exist.")
    else:
        print("  OK No existing work found - safe to begin.")