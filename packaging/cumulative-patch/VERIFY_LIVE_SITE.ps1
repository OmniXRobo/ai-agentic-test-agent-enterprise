<#
  SkyGuard QA 23.7.2 - live flight-search check (run after APPLY_PATCH).

  A visible browser (the application's own Python, Playwright and Edge) searches the site by itself with the same
  component handlers Play uses: From and To (typed and chosen from the suggestions by airport code), departure and
  return dates in the calendar, Adults, Search, then checks that a results page names both airports. It runs once per
  set of values (two different sets by default). It stops at the results: nothing is booked or paid. The report
  (REPORT.md, screenshots, and what the engine saw at any failure) is saved as one ZIP on your Desktop.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\VERIFY_LIVE_SITE.ps1
    powershell -ExecutionPolicy Bypass -File .\VERIFY_LIVE_SITE.ps1 -Set "Dubai|DXB>New Delhi|DEL@+9,+13#2","London|LHR>Manila|MNL@+20,+27#1"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$Url = "https://www.trip.com/flights/",
    [string[]]$Set = @(),
    [ValidateSet("msedge", "chrome", "chromium")][string]$Browser = "msedge"
)
$ErrorActionPreference = "Stop"
function Fail([string]$msg) { Write-Host ""; Write-Host "CHECK FAILED TO START: $msg" -ForegroundColor Red; exit 1 }
$roots = @()
if ($InstallDir) { $roots += $InstallDir }
$roots += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent")
$App = $null
foreach ($r in $roots) {
    if (Test-Path (Join-Path $r "app\qa_platform\__init__.py")) { $App = (Resolve-Path (Join-Path $r "app")).Path; break }
    if (Test-Path (Join-Path $r "qa_platform\__init__.py")) { $App = (Resolve-Path $r).Path; break }
}
if (-not $App) { Fail "No installation found in: $($roots -join '; '). Run again with -InstallDir ""<folder that holds app or qa_platform>""." }
if (-not (Test-Path (Join-Path $App "qa_platform\automation\live_check.py"))) { Fail "Apply this patch first (APPLY_PATCH.cmd in this folder)." }
$Py = Join-Path $App "runtime\python\python.exe"
if (-not (Test-Path $Py)) { $Py = Join-Path $App ".venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { Fail "The application's Python was not found in $App\runtime\python or $App\.venv." }
$argsList = @("-E", "-s", "-m", "qa_platform.automation.live_check", "--url", $Url, "--browser", $Browser)
foreach ($s in $Set) { $argsList += @("--set", $s) }
$ErrorActionPreference = "Continue"
Push-Location $App
try { & $Py @argsList }
finally { Pop-Location }
exit $LASTEXITCODE
