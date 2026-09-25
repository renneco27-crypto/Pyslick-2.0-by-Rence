param(
    [Parameter(Mandatory=$true)][string]$File,
    [Parameter(Mandatory=$true)][string]$Old,
    [Parameter(Mandatory=$true)][string]$New,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($File)) { Write-Host "FAIL: -File empty." -ForegroundColor Red; exit 1 }
if ([string]::IsNullOrEmpty($Old))       { Write-Host "FAIL: -Old empty."  -ForegroundColor Red; exit 1 }
if ([string]::IsNullOrEmpty($New))       { Write-Host "FAIL: -New empty."  -ForegroundColor Red; exit 1 }

function ReadUtf8Strict([string]$path) {
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $path).Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $bytes = $bytes[3..($bytes.Length - 1)]
    }
    $enc = New-Object System.Text.UTF8Encoding($false, $true)
    return $enc.GetString($bytes)
}

function WriteUtf8NoBom([string]$path, [string]$text) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Resolve-Path -LiteralPath $path).Path, $text, $enc)
}

function Fail([string]$msg) { Write-Host ""; Write-Host "FAIL: $msg" -ForegroundColor Red; exit 1 }
function Say([string]$msg) { Write-Host $msg }

if (-not (Test-Path -LiteralPath $File)) { Fail "file not found: $File" }
$resolved = (Resolve-Path -LiteralPath $File).Path
Say "Target: $resolved"

$original = ReadUtf8Strict $resolved
if ($original.Length -eq 0) { Fail "empty or unreadable" }

if ($original.Contains($New)) {
    Write-Host "Already applied - no change." -ForegroundColor Cyan
    exit 0
}

$occurrences = ([regex]::Matches($original, [regex]::Escape($Old))).Count
if ($occurrences -eq 0) {
    Write-Host "Anchor NOT FOUND. Nothing written." -ForegroundColor Yellow
    exit 1
}
if ($occurrences -gt 20) {
    Fail "anchor matches $occurrences places - too generic"
}
if ($occurrences -gt 1) {
    Write-Host "Anchor AMBIGUOUS ($occurrences matches). Nothing written." -ForegroundColor Yellow
    exit 1
}

$modified = $original.Replace($Old, $New)

Say ""
Say "-- DIFF --"
$origLines = $original -split "`n"
$modLines  = $modified -split "`n"
$maxLines  = [Math]::Max($origLines.Count, $modLines.Count)
$diffLines = @()
for ($i = 0; $i -lt $maxLines; $i++) {
    $a = if ($i -lt $origLines.Count) { $origLines[$i] } else { $null }
    $b = if ($i -lt $modLines.Count)  { $modLines[$i] }  else { $null }
    if ($a -ne $b) {
        if ($null -ne $a) { $diffLines += "  - L$($i+1): $a" }
        if ($null -ne $b) { $diffLines += "  + L$($i+1): $b" }
    }
}
if ($diffLines.Count -eq 0) { Say "  (no diff)"; exit 0 }
$diffLines | ForEach-Object { Write-Host $_ }
Say ""

if ($DryRun) { Write-Host "DryRun - nothing written." -ForegroundColor Cyan; exit 0 }

$backupDir = Join-Path (Get-Location) ".pyslick\backups"
if (-not (Test-Path -LiteralPath $backupDir)) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
}
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$safeName  = ($File -replace '[\\/:*?"<>|]', '_')
$backupPath = Join-Path $backupDir "$safeName.$timestamp.bak"
Copy-Item -LiteralPath $resolved -Destination $backupPath -Force
Say "Backup: $backupPath"

WriteUtf8NoBom $resolved $modified
Say "Written."

$verify = ReadUtf8Strict $resolved
if (-not $verify.Contains($New)) {
    Copy-Item -LiteralPath $backupPath -Destination $resolved -Force
    Fail "verification failed - rolled back"
}
$bytes = [System.IO.File]::ReadAllBytes($resolved)
if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    Copy-Item -LiteralPath $backupPath -Destination $resolved -Force
    Fail "BOM written - rolled back"
}
Say ""
Say "VERIFIED: replacement landed, no BOM, no mojibake." -ForegroundColor Green
exit 0
