<#
  SkyGuard Adaptive AI Browser Extension: per-user installation of the local companion (no administrator rights).

  What it does (all inside %LOCALAPPDATA%\SkyGuardAgent, nothing machine-wide):
    1. checks Windows, PowerShell and the package (SHA256_CHECKSUM.txt),
    2. backs up an earlier installation's program files (your data folder is never deleted),
    3. unpacks the bundled Python 3.13.17 and llama-server b10549 (CPU), installs Faster-Whisper from the bundled,
       hash-checked wheels (offline, no download),
    4. copies the companion and the unpacked extension, writes the default configuration,
    5. adds a per-user sign-in entry (HKCU\...\Run) and Start menu shortcuts,
    6. starts the companion, checks it answers on 127.0.0.1, runs the prerequisite check and shows the pairing code.
  It does not change browser policies and does not install the extension into the browser for you: Chrome and Edge
  only allow that through Load unpacked (Developer mode), the Chrome Web Store / Edge Add-ons, or your IT policy.

  powershell -ExecutionPolicy Bypass -File .\scripts\Install-SkyGuardAgent.ps1 [-Port 8790] [-NoStart] [-NoAutostart]
#>
[CmdletBinding()]
param([int]$Port = 8790, [switch]$NoStart, [switch]$NoAutostart)
$ErrorActionPreference = "Stop"
$Pkg = Split-Path $PSScriptRoot -Parent
$Target = Join-Path $env:LOCALAPPDATA "SkyGuardAgent"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
function Step([string]$m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say([string]$m) { Write-Host $m }
function Fail([string]$m) { Write-Host ""; Write-Host "INSTALLATION FAILED: $m" -ForegroundColor Red; exit 1 }

Step "SkyGuard Adaptive AI Browser Extension: companion installation (current user only)"
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$build = [int]([Environment]::OSVersion.Version.Build)
Say "Windows: $($os.Caption) build $build; PowerShell $($PSVersionTable.PSVersion)"
if ($build -lt 22000) { Write-Warning "Windows 11 (build 22000 or later) is the supported system. Continuing on build $build." }
if ([Environment]::Is64BitOperatingSystem -eq $false) { Fail "A 64-bit Windows is required." }

Step "Checking the package (SHA-256)"
$sums = Join-Path $Pkg "SHA256_CHECKSUM.txt"
if (-not (Test-Path $sums)) { Fail "SHA256_CHECKSUM.txt is missing. Extract the whole zip again." }
$bad = @()
foreach ($line in Get-Content $sums) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { continue }
    $want = $Matches[1]; $rel = $Matches[2] -replace '/', '\'
    $f = Join-Path $Pkg $rel
    if (-not (Test-Path -LiteralPath $f)) { $bad += "$rel (missing)"; continue }
    $have = (Get-FileHash -Algorithm SHA256 -LiteralPath $f).Hash.ToLower()
    if ($have -ne $want) { $bad += $rel }
}
if ($bad.Count) { Fail ("The package is damaged or was changed: " + (($bad | Select-Object -First 8) -join ", ") + ". Extract the zip again.") }
Say "All files match."

Step "Stopping a running companion"
try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 | Out-Null; $running = $true } catch { $running = $false }
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Target, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Say "Stopping PID $($_.Id)"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
if ($running) { Start-Sleep -Seconds 2 }

Step "Backing up an earlier installation"
New-Item -ItemType Directory -Force -Path $Target | Out-Null
$probe = Join-Path $Target ".write-test"
try { Set-Content -Path $probe -Value "x"; Remove-Item $probe } catch { Fail "Cannot write to $Target." }
$backup = Join-Path $Target "backups\install-$Stamp"
$had = $false
foreach ($d in @("app", "extension")) {
    $src = Join-Path $Target $d
    if (Test-Path $src) { New-Item -ItemType Directory -Force -Path $backup | Out-Null; Copy-Item -Recurse -Force $src (Join-Path $backup $d); $had = $true }
}
if ($had) { Say "Previous program files saved in $backup (data\ is kept in place)." } else { Say "New installation." }

try {
    Step "Unpacking the bundled Python 3.13.17"
    $rt = Join-Path $Target "runtime"
    $py = Join-Path $rt "python"
    if (Test-Path $py) { Remove-Item -Recurse -Force $py }
    $tmp = Join-Path $env:TEMP "sg-agent-$Stamp"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    Copy-Item (Join-Path $Pkg "offline\python.3.13.17.nupkg") (Join-Path $tmp "python.zip")
    Expand-Archive -Path (Join-Path $tmp "python.zip") -DestinationPath (Join-Path $tmp "python") -Force
    Move-Item (Join-Path $tmp "python\tools") $py
    $PyExe = Join-Path $py "python.exe"; $PywExe = Join-Path $py "pythonw.exe"
    & $PyExe -E -s -c "import sys, sqlite3, ctypes; print(sys.version.split()[0], sqlite3.sqlite_version)"
    if ($LASTEXITCODE -ne 0) { throw "The bundled Python does not start." }

    Step "Unpacking llama-server b10549 (CPU)"
    $llama = Join-Path $rt "llama"
    if (Test-Path $llama) { Remove-Item -Recurse -Force $llama }
    Expand-Archive -Path (Join-Path $Pkg "offline\llama-b10549-bin-win-cpu-x64.zip") -DestinationPath $llama -Force

    Step "Installing Faster-Whisper from the bundled wheels (offline, hash-checked)"
    $fw = Join-Path $rt "faster-whisper\lib"
    if (Test-Path $fw) { Remove-Item -Recurse -Force $fw }
    $ErrorActionPreference = "Continue"
    & $PyExe -E -s -m pip install --no-index --find-links (Join-Path $Pkg "offline\wheels") --require-hashes --only-binary=:all: --no-deps --no-compile --disable-pip-version-check --target $fw -r (Join-Path $Pkg "offline\fw-requirements.txt")
    $rc = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($rc -ne 0) { throw "Faster-Whisper could not be installed from the bundled wheels." }

    Step "Copying the companion and the extension"
    foreach ($d in @("app", "extension")) { $p = Join-Path $Target $d; if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
    Copy-Item -Recurse (Join-Path $Pkg "companion") (Join-Path $Target "app")
    Copy-Item -Recurse (Join-Path $Pkg "extension") (Join-Path $Target "extension")
    Copy-Item (Join-Path $Pkg "scripts\SkyGuardAgent.cmd") (Join-Path $Target "SkyGuardAgent.cmd") -Force
    foreach ($s in @("Install-Models.ps1", "Check-Prerequisites.ps1", "Uninstall-SkyGuardAgent.ps1")) { Copy-Item (Join-Path $Pkg "scripts\$s") (Join-Path $Target $s) -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $Target "data"), (Join-Path $Target "models") | Out-Null
    $cfg = Join-Path $Target "data\config.json"
    if (-not (Test-Path $cfg)) { Set-Content -Path $cfg -Encoding UTF8 -Value ("{`"port`": $Port}") }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
} catch {
    Write-Host "Restoring the previous program files..." -ForegroundColor Yellow
    if ($had) { foreach ($d in @("app", "extension")) { $b = Join-Path $backup $d; if (Test-Path $b) { $p = Join-Path $Target $d; if (Test-Path $p) { Remove-Item -Recurse -Force $p }; Copy-Item -Recurse $b $p } } }
    Fail "$($_.Exception.Message) Your data folder was not touched."
}

Step "Sign-in start and Start menu shortcuts (this user only)"
$main = Join-Path $Target "app\companion_main.py"
$cmdline = "`"$PywExe`" -E -s `"$main`" serve"
if (-not $NoAutostart) {
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "SkyGuardAgent" -Value $cmdline -PropertyType String -Force | Out-Null
    Say "The companion starts when you sign in to Windows (HKCU Run: SkyGuardAgent)."
}
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "SkyGuard Agent"
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$shell = New-Object -ComObject WScript.Shell
function Shortcut([string]$name, [string]$exe, [string]$arguments, [string]$icon) {
    $lnk = $shell.CreateShortcut((Join-Path $menu "$name.lnk"))
    $lnk.TargetPath = $exe; $lnk.Arguments = $arguments; $lnk.WorkingDirectory = $Target
    if ($icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
}
Shortcut "SkyGuard Agent (start companion)" $PywExe "-E -s `"$main`" serve" ""
Shortcut "SkyGuard Agent - Pairing code" (Join-Path $Target "SkyGuardAgent.cmd") "pair" ""
Shortcut "SkyGuard Agent - Check prerequisites" "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Target 'Check-Prerequisites.ps1')`"" ""
Shortcut "SkyGuard Agent - Uninstall" "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Target 'Uninstall-SkyGuardAgent.ps1')`"" ""

$health = $null
if (-not $NoStart) {
    Step "Starting the companion"
    Start-Process -FilePath $PywExe -ArgumentList @("-E", "-s", "`"$main`"", "serve") -WorkingDirectory $Target -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not $health; $i++) { Start-Sleep -Seconds 1; try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 } catch { } }
    if ($health) { Say "Companion $($health.version) answers on http://127.0.0.1:$Port (this PC only)." } else { Write-Warning "The companion did not answer yet; see $Target\data\logs\companion.log" }
}

Step "Prerequisite check"
& $PyExe -E -s $main check

Step "Browser policy (read only)"
foreach ($b in @(@{n="Chrome"; k="Software\Policies\Google\Chrome"}, @{n="Edge"; k="Software\Policies\Microsoft\Edge"})) {
    foreach ($hive in @("HKLM", "HKCU")) {
        $key = "${hive}:\$($b.k)"
        if (Test-Path $key) {
            $p = Get-ItemProperty $key -ErrorAction SilentlyContinue
            if ($null -ne $p.DeveloperToolsAvailability -and $p.DeveloperToolsAvailability -eq 2) { Write-Warning "$($b.n): developer tools are disabled by policy ($key); Load unpacked is not available. Ask IT to deploy the extension (docs\ENTERPRISE_DEPLOYMENT.md)." }
            if (Test-Path "$key\ExtensionInstallBlocklist") { Write-Warning "$($b.n): an extension block list is set by policy ($key\ExtensionInstallBlocklist). If it blocks '*', ask IT to allow the extension ID fmiiaecpdgbhhhbbkkfhnjfkbmllladb." }
        }
    }
}

Step "Pairing code"
& $PyExe -E -s $main pair

Step "Next steps"
Say "1. Optional: install the AI and speech models:  powershell -ExecutionPolicy Bypass -File `"$Target\Install-Models.ps1`""
Say "2. Chrome: open chrome://extensions  |  Edge: open edge://extensions"
Say "   Turn on Developer mode, press 'Load unpacked' and choose:  $Target\extension"
Say "3. Open the extension's Options, type the pairing code above, create your companion user and sign in."
Say "Details: docs\INSTALL_CHROME.md, docs\INSTALL_EDGE.md, docs\COMPANION_SETUP.md"
exit 0
