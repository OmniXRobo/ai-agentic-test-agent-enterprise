<#
  SkyGuard QA - final consolidated upgrade patch 23.4.0, for 23.3.35.

  Finds the installation, stops it, and runs install_patch.py with the application's own Python:
  verify the patch files (SHA-256), check disk space and permissions, back up the files it replaces, the database
  and settings.json, copy the files, migrate the database (new tables and one column only), verify, and restore
  everything automatically if any step fails. Then starts the application and checks /api/health.

  Usage (PowerShell, in the extracted patch folder):
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -InstallDir "D:\Apps\AIAgenticTestAgent"
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -VerifyOnly
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -Rollback [-RestoreDatabase]
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [switch]$NoStart,
    [switch]$VerifyOnly,
    [switch]$Rollback,
    [switch]$RestoreDatabase
)
$ErrorActionPreference = "Stop"
$PatchVersion = "23.4.0"
$Here = $PSScriptRoot
function Say([string]$msg) { Write-Host $msg }
function Step([string]$msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host ""; Write-Host "PATCH FAILED: $msg" -ForegroundColor Red; exit 1 }

Say "SkyGuard QA final consolidated patch $PatchVersion"
$roots = @()
if ($InstallDir) { $roots += $InstallDir }
$roots += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent")
$App = $null
foreach ($r in $roots) {
    if (Test-Path (Join-Path $r "app\qa_platform\__init__.py")) { $App = (Resolve-Path (Join-Path $r "app")).Path; break }
    if (Test-Path (Join-Path $r "qa_platform\__init__.py")) { $App = (Resolve-Path $r).Path; break }
}
if (-not $App) { Fail "No installation found in: $($roots -join '; '). Run again with -InstallDir ""<folder that holds app or qa_platform>""." }
if (-not (Test-Path (Join-Path $Here "install_patch.py")) -or -not (Test-Path (Join-Path $Here "PATCH_MANIFEST.json"))) { Fail "The patch folder is incomplete. Extract the whole zip again." }
$Installed = Test-Path (Join-Path $App "INSTALLED.json")
if ($Installed) { $Root = Split-Path $App -Parent; $Data = Join-Path $Root "data" } else { $Root = $App; $Data = Join-Path $App "data" }
if ($env:QA_HOME) { $Data = $env:QA_HOME }
$Py = Join-Path $App "runtime\python\python.exe"
if (-not (Test-Path $Py)) { $Py = Join-Path $App ".venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { Fail "The application's Python was not found in $App\runtime\python or $App\.venv." }

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "patch-$PatchVersion-$Stamp.txt"
try { Start-Transcript -Path $Log -Append | Out-Null } catch { }

$Installer = Join-Path $Here "install_patch.py"
if ($VerifyOnly) {
    & $Py -E -s $Installer verify --app $App --data $Data --python $Py
    $rc = $LASTEXITCODE
    try { Stop-Transcript | Out-Null } catch { }
    exit $rc
}

Step "Stopping the application"
$Port = 8765
$settingsFile = Join-Path $Data "settings.json"
if (Test-Path $settingsFile) { try { $p = (Get-Content $settingsFile -Raw | ConvertFrom-Json).server.port; if ($p) { $Port = [int]$p } } catch { } }
$pids = @()
try { $pids += (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess } catch { }
$pids += (Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) }).Id
$pids = $pids | Where-Object { $_ -and $_ -ne 0 } | Sort-Object -Unique
foreach ($id in $pids) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) { Say "Stopping $($proc.ProcessName) (PID $id)"; Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
if ($pids) { Start-Sleep -Seconds 3 } else { Say "It was not running." }

$ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 turns a native command's stderr into an error
if ($Rollback) {
    $rbArgs = @("rollback", "--app", $App, "--data", $Data)
    if ($RestoreDatabase) { $rbArgs += "--restore-database" }
    & $Py -E -s $Installer @rbArgs
} else {
    & $Py -E -s $Installer apply --app $App --data $Data --python $Py
}
$rc = $LASTEXITCODE
if ($rc -ne 0) { try { Stop-Transcript | Out-Null } catch { }; Fail "See the messages above. Log: $Log" }

$Health = $null
if (-not $NoStart) {
    Step "Starting SkyGuard QA"
    $launcher = Join-Path $Root "START_QA_PLATFORM.cmd"
    if (-not (Test-Path $launcher)) { $launcher = Join-Path $App "START_QA_PLATFORM.cmd" }
    if (Test-Path $launcher) {
        Start-Process -FilePath $launcher -WorkingDirectory (Split-Path $launcher -Parent)
        for ($i = 0; $i -lt 45 -and -not $Health; $i++) {
            Start-Sleep -Seconds 2
            try { $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 } catch { }
        }
    }
}
Step "Result"
if ($Health) { Say "Running: http://127.0.0.1:$Port/  version $($Health.version)" }
elseif ($NoStart) { Say "Done. Start it with START_QA_PLATFORM.cmd." }
else { Say "Done, but the health check did not answer within 90 seconds. Look at the application window." }
Say "Press Ctrl+F5 once in the browser. Log: $Log"
try { Stop-Transcript | Out-Null } catch { }
exit 0
