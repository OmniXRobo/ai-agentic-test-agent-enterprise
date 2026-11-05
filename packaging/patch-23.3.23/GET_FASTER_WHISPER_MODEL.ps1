<#
  Downloads a Faster-Whisper speech model from Hugging Face (Systran/faster-whisper-<model>) and checks every file
  against the SHA-256 Hugging Face publishes for it. Run it on any Windows PC with Internet; no Python is needed.

  It makes faster-whisper-<model>.zip. Copy that zip to the offline PC (into Downloads, or next to the patch zip)
  and run APPLY_PATCH.ps1 there: the patch finds it by itself (or pass -ModelZip <path>).

  Usage (PowerShell, in the extracted patch folder):
    powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1                 # small.en (about 465 MB)
    powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1 -Model base.en  # faster, less accurate
    powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1 -OutDir D:\usb

  Models (English): tiny.en, base.en, small.en (recommended), medium.en. Multilingual: tiny, base, small, medium,
  large-v3. The model files are published by SYSTRAN under the MIT licence (converted from OpenAI Whisper).
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Model = "small.en",
    [string]$OutDir = "",
    [switch]$NoZip,          # keep only the folder (used by APPLY_PATCH.ps1 when the PC itself has Internet)
    [string]$Repo = ""       # another Hugging Face repository with a CTranslate2 Whisper model
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times faster without the progress bar
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
try { [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials } catch { }

if (-not $Repo) { $Repo = "Systran/faster-whisper-$Model" }
if (-not $OutDir) { $OutDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path } }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Folder = Join-Path $OutDir "faster-whisper-$Model"
$Zip = Join-Path $OutDir "faster-whisper-$Model.zip"

$Curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1

function Get-File([string]$Uri, [string]$OutFile) {
    # curl.exe (in Windows 10 and 11) shows progress, resumes a partial file, and gives up on a stalled connection
    # (under 10 KB/s for 60 s) so it can retry; Invoke-WebRequest in Windows PowerShell 5.1 can hang without a sign.
    if ($Curl) {
        for ($i = 1; $i -le 6; $i++) {
            & $Curl.Source -L --fail --ssl-no-revoke -C - --speed-limit 10240 --speed-time 60 --connect-timeout 30 -o $OutFile $Uri
            if ($LASTEXITCODE -eq 0) { return }
            if ($LASTEXITCODE -eq 33 -and (Test-Path $OutFile)) { Remove-Item $OutFile -Force }  # server cannot resume
            if ($i -eq 6) { throw "Download failed: $Uri (curl exit code $LASTEXITCODE)" }
            Write-Host "  connection stalled or dropped (curl $LASTEXITCODE); resuming in 5 s (try $($i + 1) of 6)..."
            Start-Sleep -Seconds 5
        }
    }
    try {
        Start-BitsTransfer -Source $Uri -Destination $OutFile -DisplayName "Faster-Whisper model" -ErrorAction Stop
        return
    } catch { Write-Host "  BITS could not download it ($($_.Exception.Message)); trying Invoke-WebRequest..." }
    if (Test-Path $OutFile) { Remove-Item $OutFile -Force }
    Get-WithRetry $Uri $OutFile
}

function Get-WithRetry([string]$Uri, [string]$OutFile) {
    for ($i = 1; $i -le 4; $i++) {
        try {
            if ($OutFile) { Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 600 | Out-Null; return }
            return Invoke-RestMethod -Uri $Uri -UseBasicParsing -TimeoutSec 60
        } catch {
            if ($i -eq 4) { throw "Download failed: $Uri ($($_.Exception.Message))" }
            Write-Host "  retrying in $([int][math]::Pow(2, $i)) s ($($_.Exception.Message))"
            Start-Sleep -Seconds ([int][math]::Pow(2, $i))
        }
    }
}

Write-Host "== Faster-Whisper model $Model from https://huggingface.co/$Repo (download script r3)" -ForegroundColor Cyan
$info = Get-WithRetry "https://huggingface.co/api/models/$Repo"
$rev = $info.sha
if (-not $rev) { throw "Hugging Face did not return a revision for $Repo." }
# Windows PowerShell 5.1 hands back a JSON array as ONE object (an array inside an array): flatten it.
$tree = @(foreach ($item in @(Get-WithRetry "https://huggingface.co/api/models/$Repo/tree/$rev")) { $item })
$files = @($tree | ForEach-Object { $_ } | Where-Object { "$($_.type)" -eq "file" -and "$($_.path)" -notin @(".gitattributes", "README.md") -and "$($_.path)" -notlike "*/*" })
if (-not ($files | Where-Object { $_.path -eq "model.bin" })) { throw "$Repo has no model.bin: it is not a Faster-Whisper (CTranslate2) model." }

New-Item -ItemType Directory -Force -Path $Folder | Out-Null
$record = [ordered]@{}
foreach ($f in $files) {
    $dest = Join-Path $Folder ([string]$f.path)
    $size = if ($f.lfs) { [int64]$f.lfs.size } else { [int64]$f.size }
    $want = if ($f.lfs) { "$($f.lfs.oid)".ToLower() } else { "" }
    $have = if (Test-Path $dest) { (Get-Item $dest).Length } else { -1 }
    if ($have -eq $size -and $want -and ((Get-FileHash $dest -Algorithm SHA256).Hash.ToLower() -eq $want)) {
        Write-Host "  $($f.path): already downloaded"
    } else {
        Write-Host ("  {0} ({1:N0} MB)..." -f $f.path, ($size / 1MB))
        if ($have -gt $size) { Remove-Item $dest -Force }
        Get-File "https://huggingface.co/$Repo/resolve/$rev/$($f.path)" $dest
    }
    $got = (Get-Item $dest).Length
    if ($got -ne $size) { throw "$($f.path): $got bytes received, $size expected. Run the script again." }
    $hash = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($want -and $hash -ne $want) { throw "$($f.path): SHA-256 $hash does not match the published $want. Run the script again." }
    $record[$f.path] = [ordered]@{ sha256 = $hash; size = $got }
}
$source = [ordered]@{ model = $Model; repo = $Repo; revision = $rev; url = "https://huggingface.co/$Repo"
                      license = "$($info.cardData.license)"; downloaded_at = (Get-Date).ToString("s"); files = $record }
($source | ConvertTo-Json -Depth 5) | Set-Content -Path (Join-Path $Folder "SOURCE.json") -Encoding UTF8
Write-Host "  All files match the SHA-256 published by Hugging Face (revision $($rev.Substring(0, 12)))."

if (-not $NoZip) {
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Folder, $Zip, [System.IO.Compression.CompressionLevel]::Fastest, $true)
    Write-Host ("Model zip: {0} ({1:N0} MB). Copy it to the offline PC (Downloads) and run APPLY_PATCH.ps1 there." -f $Zip, ((Get-Item $Zip).Length / 1MB)) -ForegroundColor Green
} else {
    Write-Host "Model folder: $Folder" -ForegroundColor Green
}
Write-Output "MODEL_DIR=$Folder"
