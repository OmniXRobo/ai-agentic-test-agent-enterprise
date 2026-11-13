<#
  Removes the SkyGuard companion for the current user: stops it, removes the sign-in entry, the Start menu folder and
  the program files. Your knowledge database, audit trail and saved credentials (data\ and the DPAPI vault files) are
  kept unless you add -RemoveData. The browser extension itself is removed in chrome://extensions or edge://extensions.

  powershell -ExecutionPolicy Bypass -File Uninstall-SkyGuardAgent.ps1 [-RemoveData]
#>
[CmdletBinding()]
param([switch]$RemoveData)
$Target = Join-Path $env:LOCALAPPDATA "SkyGuardAgent"
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "SkyGuardAgent" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path ([Environment]::GetFolderPath("Programs")) "SkyGuard Agent") -ErrorAction SilentlyContinue
foreach ($d in @("app", "runtime", "extension", "backups")) { Remove-Item -Recurse -Force (Join-Path $Target $d) -ErrorAction SilentlyContinue }
foreach ($f in @("SkyGuardAgent.cmd", "Install-Models.ps1", "Check-Prerequisites.ps1")) { Remove-Item -Force (Join-Path $Target $f) -ErrorAction SilentlyContinue }
if ($RemoveData) {
    Remove-Item -Recurse -Force (Join-Path $Target "data"), (Join-Path $Target "models") -ErrorAction SilentlyContinue
    Write-Host "Program files, models and data removed (saved credentials are deleted with the vault folder)."
} else {
    Write-Host "Program files removed. Kept: $Target\data (knowledge, audit, vault) and $Target\models. Run again with -RemoveData to delete them."
}
Write-Host "Remove the extension in chrome://extensions or edge://extensions (Remove)."
$self = $MyInvocation.MyCommand.Path
if ($self -and $self.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -Force $self -ErrorAction SilentlyContinue }
