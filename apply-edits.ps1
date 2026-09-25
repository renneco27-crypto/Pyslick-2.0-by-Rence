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
        Name = "1. comment_blocks.py mojibake fix"
        File = "pyslick_package\pyslick_pkg\pyslick\comment_blocks.py"
        Old  = 'with open(filepath, "r", encoding="utf-8", errors="replace") as f:'
        New  = 'with open(filepath, "r", encoding="utf-8-sig", errors="strict") as f:'
    },
    @{
        Name = "2. comment_blocks.py marker start regex"
        File = "pyslick_package\pyslick_pkg\pyslick\comment_blocks.py"
        Old  = 'MARKER_START_RE = re.compile(r"pyslick:start\s+(\S+)")'
        New  = 'MARKER_START_RE = re.compile(r"^\s*(?:#|//|--|;)\s*pyslick:start\s+(\S+)")'
    },
    @{
        Name = "3. comment_blocks.py marker end regex"
        File = "pyslick_package\pyslick_pkg\pyslick\comment_blocks.py"
        Old  = 'MARKER_END_RE = re.compile(r"pyslick:end\s+(\S+)")'
        New  = 'MARKER_END_RE = re.compile(r"^\s*(?:#|//|--|;)\s*pyslick:end\s+(\S+)")'
    },
    @{
        Name = "4. agent.py priority list"
        File = "pyslick_package\pyslick_pkg\pyslick\agent.py"
        Old  = @'
    priority = [
        "help", "git", "run_info", "list_files", "comments", "nearest",
        "scan_function", "graph", "connect", "file_info",
        "stray_symbols", "syntax_check", "indentation", "smoketest",
        "patch",
    ]
'@
        New  = @'
    priority = [
        "help", "playbook",
        "pre_work", "git",
        "run_info", "list_files", "comments", "nearest",
        "scan_function", "graph", "connect", "file_info",
        "orphans",
        "stray_symbols", "syntax_check", "indentation", "smoketest",
        "oneshot",
        "patch",
    ]
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
