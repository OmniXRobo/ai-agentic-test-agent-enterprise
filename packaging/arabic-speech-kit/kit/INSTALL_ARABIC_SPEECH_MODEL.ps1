<#
  SkyGuard QA Arabic speech kit, step 2: lets SkyGuard QA UNDERSTAND spoken Arabic (UAE and other) as well as English.
  Offline, per user, no administrator rights. Works on SkyGuard QA 23.13.1 and later (23.14.1 included).

  1. Close SkyGuard QA.
  2. Double-click INSTALL_ARABIC_SPEECH_MODEL.cmd. It
     - finds faster-whisper-small.zip (or -medium, or the folder) next to this script, in Downloads or on the Desktop,
     - refuses an English-only model (small.en ...) and checks every file against the SHA-256 recorded when it was
       downloaded (SOURCE.json, made by GET_ARABIC_SPEECH_MODEL.cmd),
     - finds the SkyGuard QA folder (%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app, a side-by-side copy in
       %LOCALAPPDATA%\SkyGuardQA\app-*, or the folder this script is in),
     - installs the model in <SkyGuard QA>\runtime\faster-whisper\models\<small|medium> (with the app's own
       INSTALL_SPEECH_MODEL.ps1; the English model small.en is kept),
     - sets Voice settings: speech recognition Faster-Whisper on this PC, model <small|medium>, Language
       "English and Arabic (detected)" (a copy of settings.json is kept first).
  3. Start SkyGuard QA and say "Captain, open Test Plans" or the same in Arabic.

  Options:
    INSTALL_ARABIC_SPEECH_MODEL.cmd "D:\usb\faster-whisper-small.zip"
    INSTALL_ARABIC_SPEECH_MODEL.cmd -AppDir "C:\SkyGuard\app"           another SkyGuard QA folder
    INSTALL_ARABIC_SPEECH_MODEL.cmd -Language ar                        Arabic only (default: auto = English and Arabic)
#>
[CmdletBinding()]
param(
    [string]$Source = "",
    [string]$AppDir = "",
    [ValidateSet("auto", "ar")][string]$Language = "auto",
    [switch]$SkipSettings
)
$ErrorActionPreference = "Stop"
$Kit = $PSScriptRoot

function Say([string]$Text, [string]$Color = "") {
    if ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
}

# ---- 1. the SkyGuard QA folder ------------------------------------------------------------------------------------
function Test-App([string]$Dir) { $Dir -and (Test-Path -LiteralPath (Join-Path $Dir "qa_platform\__init__.py")) }
function Get-AppVersion([string]$Dir) {
    $m = Select-String -LiteralPath (Join-Path $Dir "qa_platform\__init__.py") -Pattern '__version__ = "([^"]+)"' | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return "?"
}
if ($AppDir) {
    if (-not (Test-App $AppDir)) { Say "$AppDir is not a SkyGuard QA folder (no qa_platform inside)." Red; exit 1 }
} else {
    $cands = @()
    if (Test-App $Kit) { $cands += $Kit }
    if ($env:LOCALAPPDATA) {
        $cands += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent\app")
        $sbs = Join-Path $env:LOCALAPPDATA "SkyGuardQA"
        if (Test-Path -LiteralPath $sbs) {
            $cands += @(Get-ChildItem -LiteralPath $sbs -Directory -Filter "app-*" -ErrorAction SilentlyContinue |
                        Sort-Object LastWriteTime -Descending | ForEach-Object { $_.FullName })
        }
    }
    $found = @($cands | Where-Object { Test-App $_ } | Select-Object -Unique)
    if (-not $found) {
        Say "The SkyGuard QA folder was not found. Run it with the folder, for example:" Red
        Say '  INSTALL_ARABIC_SPEECH_MODEL.cmd -AppDir "%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app"'
        Say "  (right-click the SkyGuard QA shortcut > Open file location shows the folder)"
        exit 1
    }
    $AppDir = $found[0]
    if ($found.Count -gt 1) {
        Say "More than one SkyGuard QA folder was found; using the first. For another, run with -AppDir ""<folder>"":" Yellow
        $found | ForEach-Object { Say "  $_ (version $(Get-AppVersion $_))" }
    }
}
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$ver = Get-AppVersion $AppDir
Say "SkyGuard QA $ver in $AppDir" Cyan

# ---- 2. the model zip or folder -----------------------------------------------------------------------------------
if (-not $Source) {
    $places = @($Kit, (Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))
    $items = foreach ($p in $places) {
        if ($p -and (Test-Path -LiteralPath $p)) {
            Get-ChildItem -LiteralPath $p -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^faster-whisper-[A-Za-z0-9._-]+(\.zip)?$' -and $_.Name -notmatch '\.en(\.zip)?$' }
        }
    }
    # medium before small when both are there (it is the one that was fetched on purpose), the newest first
    $pick = $items | Sort-Object @{ e = { if ($_.Name -match 'medium') { 0 } else { 1 } } }, @{ e = { $_.LastWriteTime }; Descending = $true } |
            Select-Object -First 1
    if (-not $pick) {
        $en = foreach ($p in $places) { if ($p -and (Test-Path -LiteralPath $p)) { Get-ChildItem -LiteralPath $p -Filter "faster-whisper-*.en*" -ErrorAction SilentlyContinue } }
        if ($en) { Say "Only an English-only model was found ($(@($en)[0].Name)). It cannot understand Arabic." Red }
        Say "No faster-whisper-small.zip was found next to this script, in Downloads or on the Desktop." Red
        Say "Make it on a PC with Internet: double-click GET_ARABIC_SPEECH_MODEL.cmd, then copy the zip here."
        exit 1
    }
    $Source = $pick.FullName
}
if (-not (Test-Path -LiteralPath $Source)) { Say "Not found: $Source" Red; exit 1 }
$Source = (Resolve-Path -LiteralPath $Source).Path
$isDir = (Get-Item -LiteralPath $Source).PSIsContainer
Say "Model: $Source"

# Read SOURCE.json and check each file's SHA-256 without unpacking (zip) or in place (folder).
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-StreamHash($Stream) {
    try { return (($sha.ComputeHash($Stream) | ForEach-Object { $_.ToString("x2") }) -join "") } finally { $Stream.Dispose() }
}
$record = $null
$hashes = @{}
if ($isDir) {
    $srcFile = Get-ChildItem -LiteralPath $Source -Filter "SOURCE.json" -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($srcFile) {
        $record = Get-Content -LiteralPath $srcFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $record.files.PSObject.Properties) {
            $f = Join-Path $srcFile.DirectoryName $p.Name
            if (Test-Path -LiteralPath $f) { $hashes[$p.Name] = @((Get-Item -LiteralPath $f).Length, (Get-StreamHash ([IO.File]::OpenRead($f)))) }
        }
    }
} else {
    try { $zip = [System.IO.Compression.ZipFile]::OpenRead($Source) } catch { Say "$Source is not a readable zip: $($_.Exception.Message)" Red; exit 1 }
    try {
        $entry = $zip.Entries | Where-Object { $_.FullName -replace '\\', '/' -match '(^|/)SOURCE\.json$' } | Select-Object -First 1
        if ($entry) {
            $reader = New-Object IO.StreamReader($entry.Open(), [Text.Encoding]::UTF8)
            try { $record = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
            $prefix = ($entry.FullName -replace '\\', '/') -replace 'SOURCE\.json$', ''
            foreach ($p in $record.files.PSObject.Properties) {
                $e = $zip.Entries | Where-Object { ($_.FullName -replace '\\', '/') -eq "$prefix$($p.Name)" } | Select-Object -First 1
                if ($e) { Say "  checking $($p.Name)..."; $hashes[$p.Name] = @($e.Length, (Get-StreamHash $e.Open())) }
            }
        }
    } finally { $zip.Dispose() }
}

$name = ""
if ($record) {
    $name = "$($record.model)"
    if ("$($record.repo)" -match '\.en$' -or $name -match '\.en$') {
        Say "This is the English-only model $name ($($record.repo)). It cannot understand Arabic." Red
        Say "Make the multilingual one on a PC with Internet: GET_ARABIC_SPEECH_MODEL.cmd (it makes faster-whisper-small.zip)."
        exit 1
    }
    $bad = @()
    foreach ($p in $record.files.PSObject.Properties) {
        $got = $hashes[$p.Name]
        if (-not $got) { $bad += "$($p.Name) is missing"; continue }
        if ([int64]$got[0] -ne [int64]$p.Value.size) { $bad += "$($p.Name): $($got[0]) bytes, $($p.Value.size) expected"; continue }
        if ($got[1] -ne "$($p.Value.sha256)".ToLower()) { $bad += "$($p.Name): SHA-256 does not match" }
    }
    if ($bad) {
        Say "The model files are damaged or incomplete (copy the zip again, or run GET_ARABIC_SPEECH_MODEL.cmd again):" Red
        $bad | ForEach-Object { Say "  $_" Red }
        exit 1
    }
    Say "  All $(@($record.files.PSObject.Properties).Count) files match the SHA-256 recorded at download ($($record.repo), revision $("$($record.revision)".Substring(0, [Math]::Min(12, "$($record.revision)".Length))))." Green
} else {
    Say "  No SOURCE.json with the model (it was not made by GET_ARABIC_SPEECH_MODEL.cmd): the files cannot be checked." Yellow
}
if (-not $name) {
    $leaf = if ($isDir) { Split-Path $Source -Leaf } else { [IO.Path]::GetFileNameWithoutExtension($Source) }
    $name = $leaf -replace '^faster-whisper-', ''
}
if ($name -notmatch '^[A-Za-z0-9._-]+$') { $name = "small" }
if ($name -match '\.en$') { Say "'$name' is English only. It cannot understand Arabic." Red; exit 1 }

# ---- 3. install the model (the app's INSTALL_SPEECH_MODEL.ps1; the kit's copy for an older app) --------------------
$installer = Join-Path $AppDir "INSTALL_SPEECH_MODEL.ps1"
if (-not (Test-Path -LiteralPath $installer)) { $installer = Join-Path $Kit "INSTALL_SPEECH_MODEL.ps1" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Source $Source -Name $name -AppDir $AppDir
if ($LASTEXITCODE -ne 0) { Say "The model was not installed (exit code $LASTEXITCODE). Nothing in Voice settings was changed." Red; exit 1 }
$dest = @((Join-Path $AppDir "runtime\faster-whisper\models\$name"), (Join-Path (Split-Path $AppDir -Parent) "runtime\faster-whisper\models\$name")) |
        Where-Object { Test-Path -LiteralPath (Join-Path $_ "model.bin") } | Select-Object -First 1
if (-not $dest) { Say "The model is not in runtime\faster-whisper\models\$name after the install." Red; exit 1 }
if ($record) {
    $m = Join-Path $dest "model.bin"
    if ((Get-Item -LiteralPath $m).Length -ne [int64]$record.files."model.bin".size) { Say "model.bin was not copied completely to $dest." Red; exit 1 }
}

# ---- 4. Voice settings ---------------------------------------------------------------------------------------------
$languageText = if ($Language -eq "ar") { "Arabic (UAE and other)" } else { "English and Arabic (detected)" }
$manual = "In SkyGuard QA open Voice settings: Speech recognition = Faster-Whisper on this PC, Faster-Whisper model = $name, " +
          "Language = $languageText, then Save and Restart speech."
if ($SkipSettings) { Say "Voice settings were not changed (-SkipSettings). $manual" Yellow; exit 0 }
$py = @((Join-Path $AppDir "runtime\python\python.exe"), (Join-Path $AppDir ".venv\Scripts\python.exe")) |
      Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $py) {
    $cmd = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) { Say "The model is installed. SkyGuard QA's Python was not found, so: $manual" Yellow; exit 0 }
& $py -I (Join-Path $Kit "set_speech_language.py") --app $AppDir --model $name --language $Language
$rc = $LASTEXITCODE
if ($rc -eq 3) { Say "The model is installed. Close SkyGuard QA and run INSTALL_ARABIC_SPEECH_MODEL.cmd again, or: $manual" Yellow; exit 0 }
if ($rc -ne 0) { Say "The model is installed, but Voice settings could not be changed (exit code $rc). $manual" Yellow; exit 0 }
Say ""
Say "Done. Start SkyGuard QA: it now understands spoken Arabic and English ($name model)." Green
Say "Check it in Voice settings > Test Arabic transcription."
exit 0
