<#
  SkyGuard QA 23.7.1 - site diagnostic (run after APPLY_PATCH).

  Opens a visible browser on the site with the application's own Python, Playwright and browser, asks you to do five
  ordinary steps (show the search box, click the departure field, type a city, open the calendar, open the
  passengers), and after each one saves a screenshot, the accessibility tree, the page HTML and what SkyGuard's
  component engine finds. Adds your newest recorded scripts for the site and their last runs. Saves one ZIP on your
  Desktop. You do every click yourself; nothing is booked. Passwords and cookies are not collected.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\DIAGNOSE_SITE.ps1
    powershell -ExecutionPolicy Bypass -File .\DIAGNOSE_SITE.ps1 -City "Dubai" -Browser chrome -Url "https://www.trip.com/flights/"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$Url = "https://www.trip.com/flights/",
    [string]$City = "Manila",
    [ValidateSet("msedge", "chrome", "chromium")][string]$Browser = "msedge"
)
$ErrorActionPreference = "Stop"
function Fail([string]$msg) { Write-Host ""; Write-Host "DIAGNOSTIC FAILED: $msg" -ForegroundColor Red; exit 1 }
$roots = @()
if ($InstallDir) { $roots += $InstallDir }
$roots += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent")
$App = $null
foreach ($r in $roots) {
    if (Test-Path (Join-Path $r "app\qa_platform\__init__.py")) { $App = (Resolve-Path (Join-Path $r "app")).Path; break }
    if (Test-Path (Join-Path $r "qa_platform\__init__.py")) { $App = (Resolve-Path $r).Path; break }
}
if (-not $App) { Fail "No installation found in: $($roots -join '; '). Run again with -InstallDir ""<folder that holds app or qa_platform>""." }
if (-not (Test-Path (Join-Path $App "qa_platform\automation\site_diagnostic.py"))) { Fail "Apply the 23.7.1 patch first (APPLY_PATCH.cmd in this folder)." }
if (Test-Path (Join-Path $App "INSTALLED.json")) { $Data = Join-Path (Split-Path $App -Parent) "data" } else { $Data = Join-Path $App "data" }
if ($env:QA_HOME) { $Data = $env:QA_HOME }
$Py = Join-Path $App "runtime\python\python.exe"
if (-not (Test-Path $Py)) { $Py = Join-Path $App ".venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { Fail "The application's Python was not found in $App\runtime\python or $App\.venv." }
$ErrorActionPreference = "Continue"
Push-Location $App
try { & $Py -E -s -m qa_platform.automation.site_diagnostic --url $Url --city $City --browser $Browser --data $Data }
finally { Pop-Location }
exit $LASTEXITCODE
