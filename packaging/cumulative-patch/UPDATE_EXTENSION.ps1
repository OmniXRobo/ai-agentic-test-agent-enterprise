<#
  SkyGuard Adaptive AI Browser Extension - update to 1.3.1 (missing information answered field by field), part of the SkyGuard QA
  23.11.2 release. For a companion already installed with Install-SkyGuardAgent.ps1. No administrator rights; works offline.

  What it does (all inside %LOCALAPPDATA%\SkyGuardAgent, nothing machine-wide):
    1. checks the update files against SHA256_CHECKSUM.txt,
    2. stops the running companion,
    3. backs up the current app\ and extension\ folders to backups\update-1.3.1-<time> (data\ is never touched),
    4. replaces app\ (companion and agent core) and extension\ with the 1.3.1 files,
    5. starts the companion and checks it answers on 127.0.0.1; on any failure puts the backup back.
  Afterwards open chrome://extensions (or edge://extensions) and press the reload button on the SkyGuard card.

  powershell -ExecutionPolicy Bypass -File .\UPDATE_EXTENSION.ps1 [-Port 8790] [-NoStart] [-Rollback]
#>
[CmdletBinding()]
param([int]$Port = 8790, [switch]$NoStart, [switch]$Rollback)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Src = Join-Path $Here "browser-extension-update"
$Target = Join-Path $env:LOCALAPPDATA "SkyGuardAgent"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
function Step([string]$m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say([string]$m) { Write-Host $m }
function Fail([string]$m) { Write-Host ""; Write-Host "UPDATE FAILED: $m" -ForegroundColor Red; exit 1 }

Step "SkyGuard browser extension and companion: update to 1.3.1 (current user only)"
if (-not (Test-Path (Join-Path $Target "app\companion_main.py"))) { Fail "No companion installation in $Target. Install the browser extension package first (INSTALL.cmd)." }
$PyExe = Join-Path $Target "runtime\python\python.exe"
$PywExe = Join-Path $Target "runtime\python\pythonw.exe"
if (-not (Test-Path $PyExe)) { Fail "The companion's Python was not found in $Target\runtime\python." }

function Stop-Companion {
    Get-Process -Name pythonw, python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Say "Stopping PID $($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}
function Start-Companion {
    if ($NoStart) { return $null }
    $main = Join-Path $Target "app\companion_main.py"
    Start-Process -FilePath $PywExe -ArgumentList @("-E", "-s", "`"$main`"", "serve") -WorkingDirectory $Target -WindowStyle Hidden
    $h = $null
    for ($i = 0; $i -lt 20 -and -not $h; $i++) { Start-Sleep -Seconds 1; try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 } catch { } }
    return $h
}
function Restore-From([string]$b) {
    foreach ($d in @("app", "extension")) {
        $bb = Join-Path $b $d
        if (Test-Path $bb) { $p = Join-Path $Target $d; if (Test-Path $p) { Remove-Item -Recurse -Force $p }; Copy-Item -Recurse $bb $p }
    }
}

if ($Rollback) {
    $last = Get-ChildItem (Join-Path $Target "backups") -Directory -Filter "update-1.3.1-*" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    if (-not $last) { Fail "No 1.3.1 update backup found in $Target\backups." }
    Step "Putting back $($last.FullName)"
    Stop-Companion
    Restore-From $last.FullName
    $h = Start-Companion
    if ($h) { Say "Companion $($h.version) answers on http://127.0.0.1:$Port." }
    Say "Reload the extension in chrome://extensions or edge://extensions."
    exit 0
}

Step "Checking the update files (SHA-256)"
$sums = Join-Path $Here "SHA256_CHECKSUM.txt"
if (-not (Test-Path $sums) -or -not (Test-Path $Src)) { Fail "The patch folder is incomplete. Extract the whole zip again." }
$bad = @(); $n = 0
foreach ($line in Get-Content $sums) {
    if ($line -notmatch '^([0-9a-f]{64})  (browser-extension-update/.+)$') { continue }
    $want = $Matches[1]; $rel = $Matches[2] -replace '/', '\'; $n++
    $f = Join-Path $Here $rel
    if (-not (Test-Path -LiteralPath $f)) { $bad += "$rel (missing)"; continue }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower() -ne $want) { $bad += $rel }
}
if ($bad.Count -or $n -eq 0) { Fail ("The update files are damaged or were changed: " + (($bad | Select-Object -First 8) -join ", ") + ". Extract the zip again.") }
Say "$n files match."

Step "Stopping the companion"
Stop-Companion

Step "Backing up the current application files"
$backup = Join-Path $Target "backups\update-1.3.1-$Stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
foreach ($d in @("app", "extension")) { $p = Join-Path $Target $d; if (Test-Path $p) { Copy-Item -Recurse -Force $p (Join-Path $backup $d) } }
Say "Saved in $backup (data\ is kept in place)."

try {
    Step "Copying the 1.3.1 companion, agent core and extension"
    foreach ($d in @("app", "extension")) {
        $p = Join-Path $Target $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        Copy-Item -Recurse (Join-Path $Src $d) $p
    }
    $v = & $PyExe -E -s -c "import sys; sys.path.insert(0, r'$Target\app'); import skyguard_companion, skyguard_agent.understanding; print(skyguard_companion.VERSION)"
    if ($LASTEXITCODE -ne 0 -or "$v".Trim() -ne "1.3.1") { throw "The new companion does not load (got '$v')." }
    Say "Companion and agent core 1.3.1 load."
} catch {
    Write-Host "Putting back the previous application files..." -ForegroundColor Yellow
    Restore-From $backup
    Start-Companion | Out-Null
    Fail "$($_.Exception.Message) Your data folder was not touched."
}

Step "Starting the companion"
$h = Start-Companion
if ($h) { Say "Companion $($h.version) answers on http://127.0.0.1:$Port (this PC only)." } elseif (-not $NoStart) { Write-Warning "The companion did not answer yet; see $Target\data\logs\companion.log" }

Step "Next"
Say "1. Open chrome://extensions (or edge://extensions) and press the reload button on the SkyGuard card."
Say "2. Open the side panel, tab Learn, choose the application and press Start learning; use the application, then press Stop & save learning."
Say "3. Stop & save tests that every learned task and page can be found again by a command, and shows the result."
Say "4. New in 1.3.1: answers to 'Missing information' are read field by field and kept; only what is still missing is asked."
Say "To undo: UPDATE_EXTENSION.cmd -Rollback"
exit 0
