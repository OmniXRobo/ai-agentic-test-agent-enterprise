<#
  Added after 23.13.0: installs a Faster-Whisper speech model on this PC, offline. For Arabic and UAE Arabic use a multilingual
  model ("small", not "small.en").

  1. On a PC with Internet:   powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1 -Model small
     It makes faster-whisper-small.zip (every file checked against the SHA-256 Hugging Face publishes).
  2. Copy the zip (or the faster-whisper-small folder) to this PC, for example into Downloads.
  3. Double-click INSTALL_SPEECH_MODEL.cmd in the SkyGuard QA folder. It finds the zip or folder in Downloads, the
     Desktop or next to this script, or takes the path you give it:
        INSTALL_SPEECH_MODEL.cmd "D:\usb\faster-whisper-small.zip"
        INSTALL_SPEECH_MODEL.cmd "D:\usb\faster-whisper-small" -Name small

  The model goes to <SkyGuard QA>\runtime\faster-whisper\models\<name>. Per user, no administrator rights, nothing is
  downloaded. Restart SkyGuard QA (or press Restart speech in Voice settings) afterwards.
#>
[CmdletBinding()]
param(
    [string]$Source = "",
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Name = "",
    [string]$AppDir = ""
)
$ErrorActionPreference = "Stop"

function Test-Model([string]$Dir) {
    (Test-Path -LiteralPath (Join-Path $Dir "model.bin")) -and (Test-Path -LiteralPath (Join-Path $Dir "config.json")) -and (
        (Test-Path -LiteralPath (Join-Path $Dir "tokenizer.json")) -or (Test-Path -LiteralPath (Join-Path $Dir "vocabulary.txt")) -or
        (Test-Path -LiteralPath (Join-Path $Dir "vocabulary.json")))
}
function Find-ModelDir([string]$Dir) {
    if (Test-Model $Dir) { return $Dir }
    $sub = Get-ChildItem -LiteralPath $Dir -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue | Where-Object { Test-Model $_.FullName } | Select-Object -First 1
    if ($sub) { return $sub.FullName }
    return $null
}

if (-not $AppDir) { $AppDir = $PSScriptRoot }
$homes = @((Join-Path $AppDir "runtime\faster-whisper"), (Join-Path (Split-Path $AppDir -Parent) "runtime\faster-whisper"))
$fwHome = $homes | Where-Object { Test-Path -LiteralPath (Join-Path $_ "lib\faster_whisper") } | Select-Object -First 1
if (-not $fwHome) {
    $fwHome = $homes[0]
    Write-Host "Faster-Whisper itself is not installed in $fwHome yet; the model is put there anyway." -ForegroundColor Yellow
}

if (-not $Source) {
    $places = @($PSScriptRoot, (Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))
    $found = foreach ($p in $places) {
        if (Test-Path -LiteralPath $p) {
            Get-ChildItem -LiteralPath $p -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^faster-whisper-[A-Za-z0-9._-]+(\.zip)?$' }
        }
    }
    # a multilingual model (no ".en") first: it is the one Arabic needs
    $Source = ($found | Sort-Object @{ e = { if ($_.Name -match '\.en(\.zip)?$') { 1 } else { 0 } } }, LastWriteTime -Descending | Select-Object -First 1).FullName
    if (-not $Source) {
        Write-Host "No faster-whisper-<model>.zip or folder was found in Downloads, the Desktop or $PSScriptRoot." -ForegroundColor Red
        Write-Host "Make it on a PC with Internet: GET_FASTER_WHISPER_MODEL.ps1 -Model small, then copy it here."
        exit 1
    }
}
if (-not (Test-Path -LiteralPath $Source)) { Write-Host "Not found: $Source" -ForegroundColor Red; exit 1 }
$Source = (Resolve-Path -LiteralPath $Source).Path
if (-not $Name) {
    $leaf = [IO.Path]::GetFileNameWithoutExtension($Source)
    if ((Get-Item -LiteralPath $Source).PSIsContainer) { $leaf = Split-Path $Source -Leaf }
    $Name = ($leaf -replace '^faster-whisper-', '')
    if ($Name -notmatch '^[A-Za-z0-9._-]+$') { $Name = "small" }
}

$tmp = $null
try {
    if ((Get-Item -LiteralPath $Source).PSIsContainer) {
        $modelDir = Find-ModelDir $Source
    } else {
        $tmp = Join-Path $env:TEMP ("sg-model-" + [guid]::NewGuid().ToString("N"))
        Write-Host "Unpacking $Source ..."
        # .NET directly: Expand-Archive breaks when Windows PowerShell inherits PowerShell 7's module path
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($Source, $tmp)
        $modelDir = Find-ModelDir $tmp
    }
    if (-not $modelDir) { Write-Host "$Source does not hold a Faster-Whisper model (model.bin, config.json, tokenizer.json)." -ForegroundColor Red; exit 1 }

    $dest = Join-Path $fwHome "models\$Name"
    $staging = "$dest.new"
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Copy-Item -Path (Join-Path $modelDir "*") -Destination $staging -Recurse -Force
    if (-not (Test-Model $staging)) { throw "The copied model is incomplete." }
    if (Test-Path -LiteralPath $dest) {
        $old = "$dest.old"
        if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
        Rename-Item -LiteralPath $dest -NewName (Split-Path $old -Leaf)
    }
    Rename-Item -LiteralPath $staging -NewName (Split-Path $dest -Leaf)
    if (Test-Path -LiteralPath "$dest.old") { Remove-Item -LiteralPath "$dest.old" -Recurse -Force }
    Write-Host "Installed the speech model '$Name' in $dest" -ForegroundColor Green
    if ($Name -match '\.en$') {
        Write-Host "Note: '$Name' is English only. Arabic needs a multilingual model such as 'small'." -ForegroundColor Yellow
    } else {
        Write-Host "Arabic and UAE Arabic speech can now be recognised. In Voice settings choose Language Arabic or Auto."
    }
    Write-Host "Restart SkyGuard QA (or press Restart speech in Voice settings) to use it."
} finally {
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
