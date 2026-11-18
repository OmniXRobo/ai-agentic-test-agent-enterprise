<#
  Stops a running SkyGuard QA before the upgrade (used by Setup.exe): the process listening on the application's port
  and any python / pythonw started from the application folder. Current user's processes only; nothing else.
  Windows PowerShell 5.1 and later.
#>
param([Parameter(Mandatory = $true)][string]$App, [int]$Port = 8765)
$ErrorActionPreference = "Continue"
$pids = @()
try { $pids += (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess } catch { }
$pids += (Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) }).Id
$pids = $pids | Where-Object { $_ -and $_ -ne 0 } | Sort-Object -Unique
foreach ($id in $pids) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) { Write-Output "Stopping $($proc.ProcessName) (PID $id)"; Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
if ($pids) { Start-Sleep -Seconds 3 } else { Write-Output "SkyGuard QA was not running." }
for ($i = 0; $i -lt 10; $i++) {
    $still = $null
    try { $still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue } catch { }
    if (-not $still) { exit 0 }
    Start-Sleep -Seconds 1
}
Write-Output "Port $Port still answers: close SkyGuard QA yourself, then run Setup again."
exit 1
