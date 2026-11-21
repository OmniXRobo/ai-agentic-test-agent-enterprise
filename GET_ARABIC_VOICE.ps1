<#
  23.13.0: downloads the offline Arabic voice for SkyGuard QA's spoken replies. Run it on any Windows PC with
  Internet; no Python is needed.

  It makes skyguard-arabic-voice.zip with
    piper\        Piper text-to-speech 2023.11.14-2 for Windows (github.com/rhasspy/piper, MIT licence),
                  checked against the SHA-256 pinned below
    voices\       the Arabic voice ar_JO-kareem-medium (male, Jordanian Arabic; huggingface.co/rhasspy/piper-voices),
                  checked against the SHA-256 Hugging Face publishes for it

  Male and female: Piper publishes one Arabic voice, Kareem (male). SkyGuard QA speaks female Arabic replies with
  Kareem at a higher pitch, unless a female voice is installed: a Windows Arabic voice such as Hoda (Arabic language
  pack with speech, installed by IT), or a female Piper model added with -Voice when one is published.

  Copy the zip to the offline PC (Downloads or the Desktop) and double-click INSTALL_ARABIC_VOICE.cmd in the
  SkyGuard QA folder there.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\GET_ARABIC_VOICE.ps1
    powershell -ExecutionPolicy Bypass -File .\GET_ARABIC_VOICE.ps1 -OutDir D:\usb
    powershell -ExecutionPolicy Bypass -File .\GET_ARABIC_VOICE.ps1 -Voice ar_JO-kareem-low     # smaller, rougher
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [ValidatePattern('^ar_[A-Z]{2}-[a-z0-9_]+-(x_low|low|medium|high)$')][string[]]$Voice = @("ar_JO-kareem-medium")
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
try { [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials } catch { }

$PiperUrl = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
$PiperSha = "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea"
$Repo = "rhasspy/piper-voices"
$Rev = "v1.0.0"

if (-not $OutDir) { $OutDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path } }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Work = Join-Path $OutDir "skyguard-arabic-voice"
$Zip = Join-Path $OutDir "skyguard-arabic-voice.zip"
$Curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1

function Get-File([string]$Uri, [string]$OutFile) {
    if ($Curl) {
        for ($i = 1; $i -le 6; $i++) {
            & $Curl.Source -L --fail --ssl-no-revoke -C - --speed-limit 10240 --speed-time 60 --connect-timeout 30 -o $OutFile $Uri
            if ($LASTEXITCODE -eq 0) { return }
            if ($LASTEXITCODE -eq 33 -and (Test-Path $OutFile)) { Remove-Item $OutFile -Force }
            if ($i -eq 6) { throw "Download failed: $Uri (curl exit code $LASTEXITCODE)" }
            Write-Host "  connection dropped (curl $LASTEXITCODE); resuming in 5 s (try $($i + 1) of 6)..."
            Start-Sleep -Seconds 5
        }
    }
    for ($i = 1; $i -le 4; $i++) {
        try { Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 600 | Out-Null; return }
        catch {
            if ($i -eq 4) { throw "Download failed: $Uri ($($_.Exception.Message))" }
            Start-Sleep -Seconds ([int][math]::Pow(2, $i))
        }
    }
}
function Get-Sha([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLower() }

Write-Host "== Offline Arabic voice for SkyGuard QA: Piper + $($Voice -join ', ')" -ForegroundColor Cyan
if (Test-Path $Work) { Remove-Item $Work -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $Work "voices") | Out-Null

# 1. Piper for Windows (pinned SHA-256)
$piperZip = Join-Path $OutDir "piper_windows_amd64.zip"
if (-not ((Test-Path $piperZip) -and ((Get-Sha $piperZip) -eq $PiperSha))) {
    Write-Host "  Piper for Windows (about 21 MB)..."
    if (Test-Path $piperZip) { Remove-Item $piperZip -Force }
    Get-File $PiperUrl $piperZip
}
$got = Get-Sha $piperZip
if ($got -ne $PiperSha) { throw "piper_windows_amd64.zip: SHA-256 $got does not match the pinned $PiperSha." }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($piperZip, $Work)   # makes $Work\piper\piper.exe
if (-not (Test-Path (Join-Path $Work "piper\piper.exe"))) { throw "The Piper zip has no piper\piper.exe." }

# 2. The Arabic voice(s) (SHA-256 published by Hugging Face)
foreach ($one in $Voice) {
    $parts = $one -split "-"           # ar_JO, kareem, medium
    $lang = $parts[0]; $family = $lang.Split("_")[0]; $name = $parts[1]; $quality = $parts[2]
    $dir = "$family/$lang/$name/$quality"
    $tree = @(foreach ($item in @(Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo/tree/$Rev/$dir" -UseBasicParsing -TimeoutSec 60)) { $item })
    foreach ($file in @("$one.onnx", "$one.onnx.json")) {
        $entry = $tree | ForEach-Object { $_ } | Where-Object { "$($_.path)" -eq "$dir/$file" } | Select-Object -First 1
        if (-not $entry) { throw "$file is not published in $Repo ($dir)." }
        $dest = Join-Path $Work "voices\$file"
        Write-Host ("  {0} ({1:N0} MB)..." -f $file, ([int64]$(if ($entry.lfs) { $entry.lfs.size } else { $entry.size }) / 1MB))
        Get-File "https://huggingface.co/$Repo/resolve/$Rev/$dir/$file" $dest
        $hash = Get-Sha $dest
        if ($entry.lfs) {
            $want = "$($entry.lfs.oid)".ToLower()
            if ($hash -ne $want) { throw "${file}: SHA-256 $hash does not match the published $want. Run the script again." }
        } elseif ((Get-Item $dest).Length -ne [int64]$entry.size) {
            throw "${file}: size does not match the published size. Run the script again."
        }
    }
}
$source = [ordered]@{ piper = $PiperUrl; piper_sha256 = $PiperSha; voice = ($Voice -join ", "); voice_repo = "https://huggingface.co/$Repo";
                      revision = $Rev; downloaded_at = (Get-Date).ToString("s") }
($source | ConvertTo-Json) | Set-Content -Path (Join-Path $Work "SOURCE.json") -Encoding UTF8

if (Test-Path $Zip) { Remove-Item $Zip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory($Work, $Zip, [System.IO.Compression.CompressionLevel]::Fastest, $false)
Remove-Item $Work -Recurse -Force
Write-Host ("Arabic voice zip: {0} ({1:N0} MB). Copy it to the office PC (Downloads) and run INSTALL_ARABIC_VOICE.cmd there." -f $Zip, ((Get-Item $Zip).Length / 1MB)) -ForegroundColor Green
Write-Output "ARABIC_VOICE_ZIP=$Zip"
