<# Read-only prerequisite check for the SkyGuard companion (also run by the installer). #>
$Target = Join-Path $env:LOCALAPPDATA "SkyGuardAgent"
$Py = Join-Path $Target "runtime\python\python.exe"
$Main = Join-Path $Target "app\companion_main.py"
if (-not (Test-Path $Py)) { $Py = Join-Path (Split-Path $PSScriptRoot -Parent) "runtime\python\python.exe" }
if (-not (Test-Path $Py) -or -not (Test-Path $Main)) { Write-Host "The companion is not installed yet. Run INSTALL.cmd from the package." -ForegroundColor Yellow; exit 1 }
& $Py -E -s $Main check
$rc = $LASTEXITCODE
foreach ($b in @("chrome", "msedge")) {
    $exe = Get-Command "$b.exe" -ErrorAction SilentlyContinue
    $paths = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    foreach ($p in $paths) { if ((Test-Path $p) -and $p -like "*$b*") { $v = (Get-Item $p).VersionInfo.ProductVersion; Write-Host "$b $v ($p)"; if ([int]($v.Split(".")[0]) -lt 116) { Write-Warning "$b 116 or later is required (side panel)." } } }
}
Write-Host ""; Read-Host "Press Enter to close" | Out-Null
exit $rc
