$ErrorActionPreference = 'Stop'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$editor = Join-Path $here "oneshot-edit.ps1"
if (-not (Test-Path -LiteralPath $editor)) {
    Write-Host "Missing: $editor" -ForegroundColor Red
    exit 1
}
Set-Location $here

$edits = @(
    @{
        Name = "1. __init__.py add playbook + git-* branches after log"
        File = "pyslick_package\pyslick_pkg\pyslick\__init__.py"
        Old  = @'
            elif command == "log":
                git_log()

            elif command == "ls":
'@
        New  = @'
            elif command == "log":
                git_log()

            elif command == "playbook":
                try:
                    from .playbook import print_playbook
                    if "--handoff" in args:
                        print_playbook("handoff")
                    elif "--all" in args:
                        print_playbook("all")
                    else:
                        print_playbook("main")
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "pre-work":
                try:
                    from .pre_work import run_pre_work
                    task = " ".join(a for a in args if not a.startswith("-"))
                    filepath = None
                    if "--file" in args:
                        i = args.index("--file")
                        if i + 1 < len(args):
                            filepath = args[i + 1]
                    run_pre_work(task, filepath)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "git-search":
                try:
                    from .pre_work import search_commits, _extract_tokens
                    tokens = _extract_tokens(" ".join(args))
                    for tok, lines in search_commits(tokens).items():
                        print(f"\n[{tok}]")
                        for l in lines:
                            print(f"  {l}")
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "git-touching":
                try:
                    if not args:
                        print("Error: git-touching requires <file>")
                        sys.exit(1)
                    from .pre_work import _git
                    rc, out, err = _git(".", "log", "--all", "--oneline", "--", args[0])
                    for l in out.splitlines():
                        print(f"  {l}")
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "git-branches":
                try:
                    if not args:
                        print("Error: git-branches requires <file>")
                        sys.exit(1)
                    from .pre_work import search_branches_for_file
                    for b, lines in search_branches_for_file(args[0]).items():
                        print(f"\n{b}:")
                        for l in lines:
                            print(f"  {l}")
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "git-prs":
                try:
                    from .pre_work import check_open_prs
                    result = check_open_prs([])
                    if not result.get("available"):
                        print("gh CLI not installed")
                    else:
                        for p in result.get("prs", []):
                            print(f"  #{p['number']}  {p['title']}  [{p['headRefName']}]")
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "ls":
'@
    },
    @{
        Name = "2. __init__.py add oneshot after grep"
        File = "pyslick_package\pyslick_pkg\pyslick\__init__.py"
        Old  = @'
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command in ("deps", "imports", "dependencies"):
'@
        New  = @'
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "oneshot":
                try:
                    from .oneshot import run_oneshot
                    query = " ".join(a for a in args if not a.startswith("-"))
                    file_scope = None
                    if "--file" in args:
                        i = args.index("--file")
                        if i + 1 < len(args):
                            file_scope = args[i + 1]
                    jobs = None
                    if "--jobs" in args:
                        i = args.index("--jobs")
                        if i + 1 < len(args):
                            try:
                                jobs = int(args[i + 1])
                            except ValueError:
                                pass
                    run_oneshot(
                        query,
                        use_json="--json" in args,
                        do_verify="--verify" in args,
                        jobs=jobs,
                        file_scope=file_scope,
                    )
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command in ("deps", "imports", "dependencies"):
'@
    },
    @{
        Name = "3. __init__.py add orphans after comment-scan"
        File = "pyslick_package\pyslick_pkg\pyslick\__init__.py"
        Old  = @'
                    print_report(nodes)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "recon":
'@
        New  = @'
                    print_report(nodes)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "orphans":
                try:
                    from .orphans import find_orphans, format_report
                    root = args[0] if args and not args[0].startswith("-") else "."
                    result = find_orphans(root)
                    format_report(result, json_out="--json" in args)
                except Exception as e:
                    print(f"Error: {e}")
                    sys.exit(1)

            elif command == "recon":
'@
    },
    @{
        Name = "4. agent.py add playbook + pre_work branches"
        File = "pyslick_package\pyslick_pkg\pyslick\agent.py"
        Old  = @'
    if intent == "help":
        result = tool_pyslick_help()
        print(result)
        return
'@
        New  = @'
    if intent == "help":
        result = tool_pyslick_help()
        print(result)
        return

    if intent == "playbook":
        try:
            from playbook import print_playbook
            print_playbook("main")
        except Exception as e:
            err(f"playbook: {e}")
        return

    if intent == "pre_work":
        try:
            from pre_work import run_pre_work
            m = re.search(r"([\w./\\-]+\.\w+)", active_directive)
            filepath = m.group(1) if m else None
            run_pre_work(active_directive, filepath)
        except Exception as e:
            err(f"pre_work: {e}")
        return
'@
    },
    @{
        Name = "5. agent.py add orphans branch before stray_symbols"
        File = "pyslick_package\pyslick_pkg\pyslick\agent.py"
        Old  = @'
    if intent == "stray_symbols":
'@
        New  = @'
    if intent == "orphans":
        try:
            from orphans import find_orphans, format_report
            results = find_orphans(".")
            m = re.search(r"\b(?:in|for|inside)\s+([\w./\\-]+\.\w+)", active_directive)
            if m:
                target = m.group(1)
                results = [o for o in results if target in o["file"]]
            format_report(results, json_out="--json" in active_directive)
        except Exception as e:
            err(f"orphans: {e}")
        return

    if intent == "stray_symbols":
'@
    },
    @{
        Name = "6. agent.py add oneshot branch before PATCH fallback"
        File = "pyslick_package\pyslick_pkg\pyslick\agent.py"
        Old  = ('    # Uses rapidfuzz graph match ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' LLM extract ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' diff ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' confirm')
        New  = (('    if intent == "oneshot":' + [char]10 +
                '        try:' + [char]10 +
                '            from oneshot import run_oneshot' + [char]10 +
                '            run_oneshot(active_directive)' + [char]10 +
                '        except Exception as e:' + [char]10 +
                '            err(f"oneshot: {e}")' + [char]10 +
                '        return' + [char]10 + [char]10 +
                '    # Uses rapidfuzz graph match ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' LLM extract ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' diff ' + [string]([char]0x00E2) + [char]0x2020 + [char]0x2019 + ' confirm'))
    },
    @{
        Name = "7. intent_vocab.json add four new intents"
        File = "pyslick_package\pyslick_pkg\pyslick\intent_vocab.json"
        Old  = @'
    "smoketest":
'@
        New  = @'
        "playbook": {
            "require_any": [
                "show me the playbook", "debugging playbook", "debug playbook",
                "how do i debug", "debugging rules", "the rules",
                "read the playbook", "debugging handoff", "debug technique"
            ],
            "exclude_if": []
        },
        "pre_work": {
            "require_any": [
                "has anyone fixed", "already fixed", "already been done",
                "check if done", "is this already fixed", "before i start",
                "check branches for", "check main for", "check pr for",
                "pre-work", "preexisting fix", "existing fix"
            ],
            "exclude_if": []
        },
        "orphans": {
            "require_any": [
                "orphan functions", "dead code", "unused functions",
                "functions not called", "find dead functions", "orphaned functions",
                "code that's never called", "what functions are unused",
                "find unused", "dead functions"
            ],
            "exclude_if": []
        },
        "oneshot": {
            "require_any": [
                "oneshot search", "fast search", "quick search", "stream search",
                "fast grep", "search fast"
            ],
            "exclude_if": []
        },
    "smoketest":
'@
    }
)

$passed = @()
$failed = $null

foreach ($edit in $edits) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host "  $($edit.Name)" -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
    try {
        & $editor -File $edit.File -Old $edit.Old -New $edit.New
        if ($LASTEXITCODE -ne 0) { throw "editor exited $LASTEXITCODE" }
        $passed += $edit.Name
    } catch {
        Write-Host ""
        Write-Host "STOPPED at: $($edit.Name)" -ForegroundColor Red
        Write-Host "  $_" -ForegroundColor Red
        $failed = $edit.Name
        break
    }
}

Write-Host ""
Write-Host ("=" * 72) -ForegroundColor Cyan
Write-Host "  SUMMARY" -ForegroundColor Cyan
Write-Host ("=" * 72) -ForegroundColor Cyan
Write-Host "  Passed: $($passed.Count) / $($edits.Count)"
foreach ($p in $passed) { Write-Host "    OK  $p" -ForegroundColor Green }
if ($failed) {
    Write-Host "    FAIL  $failed  (stopped)" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  All applied." -ForegroundColor Green
}
exit 0