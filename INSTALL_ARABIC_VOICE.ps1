<#
  23.13.0: installs the offline Arabic voice for spoken replies (Piper + an Arabic voice model) on this PC.

  1. On a PC with Internet:   powershell -ExecutionPolicy Bypass -File .\GET_ARABIC_VOICE.ps1
     It makes skyguard-arabic-voice.zip (every file checked against a pinned or published SHA-256).
  2. Copy the zip to this PC, for example into Downloads.
  3. Double-click INSTALL_ARABIC_VOICE.cmd in the SkyGuard QA folder. It finds the zip in Downloads, the Desktop or
     next to this script, or takes the path you give it:  INSTALL_ARABIC_VOICE.cmd "D:\usb\skyguard-arabic-voice.zip"

  It goes to <SkyGuard QA>\runtime\piper. Per user, no administrator rights, nothing is downloaded. Arabic replies
  use it at once (no restart needed).

  Alternative: if IT installs the Windows "Arabic" language pack with its speech voice (Naayf, Hoda), SkyGuard QA
  uses that Windows voice for Arabic replies instead; nothing else is needed.
#>
[CmdletBinding()]
param(
    [string]$Source = "",
    [string]$AppDir = ""
)
$ErrorActionPreference = "Stop"

function Find-Piper([string]$Dir) {
    $exe = Get-ChildItem -LiteralPath $Dir -Filter "piper.exe" -File -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($exe) { return $exe.Directory.FullName }
    return $null
}
function Find-Voices([string]$Dir) {
    @(Get-ChildItem -LiteralPath $Dir -Filter "ar_*.onnx" -File -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath ($_.FullName + ".json") })
}

if (-not $AppDir) { $AppDir = $PSScriptRoot }
$dest = Join-Path $AppDir "runtime\piper"

if (-not $Source) {
    $places = @($PSScriptRoot, (Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))
    $found = foreach ($p in $places) {
        if (Test-Path -LiteralPath $p) {
            Get-ChildItem -LiteralPath $p -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^skyguard-arabic-voice(\.zip)?$' }
        }
    }
    $Source = ($found | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    if (-not $Source) {
        Write-Host "No skyguard-arabic-voice.zip was found in Downloads, the Desktop or $PSScriptRoot." -ForegroundColor Red
        Write-Host "Make it on a PC with Internet: GET_ARABIC_VOICE.ps1, then copy it here."
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $Source)) { Write-Host "Not found: $Source" -ForegroundColor Red; exit 1 }
$Source = (Resolve-Path -LiteralPath $Source).Path

$tmp = $null
try {
    $root = $Source
    if (-not (Get-Item -LiteralPath $Source).PSIsContainer) {
        $tmp = Join-Path $env:TEMP ("sg-voice-" + [guid]::NewGuid().ToString("N"))
        Write-Host "Unpacking $Source ..."
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Source, $tmp)
        $root = $tmp
    }
    $piperDir = Find-Piper $root
    $voices = Find-Voices $root
    if (-not $piperDir -or $voices.Count -eq 0) {
        Write-Host "$Source does not hold Piper (piper.exe) and an Arabic voice (ar_*.onnx + .onnx.json)." -ForegroundColor Red
        exit 1
    }

    $staging = "$dest.new"
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Join-Path $staging "voices") | Out-Null
    Copy-Item -Path (Join-Path $piperDir "*") -Destination $staging -Recurse -Force
    foreach ($v in $voices) {
        Copy-Item -LiteralPath $v.FullName -Destination (Join-Path $staging "voices") -Force
        Copy-Item -LiteralPath ($v.FullName + ".json") -Destination (Join-Path $staging "voices") -Force
    }
    if (-not (Test-Path -LiteralPath (Join-Path $staging "piper.exe"))) { throw "The copied voice is incomplete." }

    # A quick test: Piper speaks one word into a WAV file.
    $wav = Join-Path $staging "test.wav"
    $model = (Get-ChildItem -LiteralPath (Join-Path $staging "voices") -Filter "*.onnx" | Select-Object -First 1).FullName
    $word = -join [char[]](0x0645, 0x0631, 0x062D, 0x0628, 0x0627)   # "marhaba" (the script itself stays ASCII)
    $OutputEncoding = New-Object System.Text.UTF8Encoding $false     # Piper reads UTF-8 on stdin
    Push-Location $staging
    $ErrorActionPreference = "Continue"   # Piper logs to stderr; Windows PowerShell would stop on the first line
    try { $word | & (Join-Path $staging "piper.exe") --model $model --output_file $wav 2>&1 | Out-Null } catch { }
    finally { $ErrorActionPreference = "Stop"; Pop-Location }
    if ((Test-Path -LiteralPath $wav) -and (Get-Item -LiteralPath $wav).Length -gt 1000) {
        Write-Host "Test: the Arabic voice spoke a test word." -ForegroundColor Green
        Remove-Item -LiteralPath $wav -Force
    } else {
        Write-Host "Test: Piper did not produce audio here (it may need the Microsoft Visual C++ runtime). It is installed anyway." -ForegroundColor Yellow
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
    if (Test-Path -LiteralPath $dest) {
        $old = "$dest.old"
        if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
        Rename-Item -LiteralPath $dest -NewName (Split-Path $old -Leaf)
    }
    Rename-Item -LiteralPath $staging -NewName (Split-Path $dest -Leaf)
    if (Test-Path -LiteralPath "$dest.old") { Remove-Item -LiteralPath "$dest.old" -Recurse -Force }
    Write-Host ("Installed the Arabic voice ({0}) in {1}" -f (($voices | ForEach-Object { $_.BaseName }) -join ", "), $dest) -ForegroundColor Green
    Write-Host "Arabic replies are now spoken by it. Test it in Voice settings > Arabic voice."
} finally {
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
