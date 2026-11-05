<#
  Downloads the fast local AI model for QA Enterprise AI 23.3.9: Qwen3-4B-Instruct-2507, Q4_K_M (about 2.5 GB,
  Apache-2.0 licence), from Hugging Face, and checks it against the SHA-256 Hugging Face publishes for the file.
  It is an instruct model without hidden "thinking" text, and about twice as fast as a 7B/8B model on the same PC.

  Run it on any Windows PC with Internet; no Python is needed. APPLY_PATCH.ps1 -FastModel finds the file in
  Downloads, Desktop, Documents or next to the patch by itself (and runs this script when it finds none).

  Usage (PowerShell, in the extracted patch folder):
    powershell -ExecutionPolicy Bypass -File .\GET_FAST_LOCAL_MODEL.ps1                       # into Downloads
    powershell -ExecutionPolicy Bypass -File .\GET_FAST_LOCAL_MODEL.ps1 -OutDir D:\usb
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [string]$Repo = "",
    [string]$File = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
try { [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials } catch { }

if (-not $OutDir) { $OutDir = Join-Path $env:USERPROFILE "Downloads" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Dest = Join-Path $OutDir $File
$Curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1

function Get-Json([string]$Uri) {
    for ($i = 1; $i -le 4; $i++) {
        try { return Invoke-RestMethod -Uri $Uri -UseBasicParsing -TimeoutSec 60 }
        catch {
            if ($i -eq 4) { throw "Hugging Face did not answer: $Uri ($($_.Exception.Message))" }
            Start-Sleep -Seconds ([int][math]::Pow(2, $i))
        }
    }
}

function Get-File([string]$Uri, [string]$OutFile) {
    # curl.exe (Windows 10 and 11): progress, resumes a partial file, gives up on a stalled connection and retries.
    if ($Curl) {
        for ($i = 1; $i -le 8; $i++) {
            & $Curl.Source -L --fail --ssl-no-revoke -C - --speed-limit 10240 --speed-time 60 --connect-timeout 30 -o $OutFile $Uri
            if ($LASTEXITCODE -eq 0) { return }
            if ($LASTEXITCODE -eq 33 -and (Test-Path $OutFile)) { Remove-Item $OutFile -Force }
            if ($i -eq 8) { throw "Download failed: $Uri (curl exit code $LASTEXITCODE). Run the script again: it resumes." }
            Write-Host "  connection stalled or dropped (curl $LASTEXITCODE); resuming in 5 s (try $($i + 1) of 8)..."
            Start-Sleep -Seconds 5
        }
    }
    Start-BitsTransfer -Source $Uri -Destination $OutFile -DisplayName "QA Enterprise AI local model" -ErrorAction Stop
}

# The same file is published by two well-known GGUF converters; the first one that has it is used.
$repos = if ($Repo) { @($Repo) } else { @("unsloth/Qwen3-4B-Instruct-2507-GGUF", "bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF") }
$entry = $null
foreach ($r in $repos) {
    try {
        $tree = @(foreach ($item in @(Get-Json "https://huggingface.co/api/models/$r/tree/main")) { $item })
        $entry = $tree | ForEach-Object { $_ } | Where-Object { "$($_.type)" -eq "file" -and "$($_.path)" -like "*Q4_K_M.gguf" } | Select-Object -First 1
        if ($entry) { $Repo = $r; break }
    } catch { Write-Host "  $r : $($_.Exception.Message)" }
}
if (-not $entry) { throw "No Q4_K_M .gguf file was found in: $($repos -join ', ')." }
$size = [int64]$entry.lfs.size
$want = "$($entry.lfs.oid)".ToLower()
$url = "https://huggingface.co/$Repo/resolve/main/$($entry.path)"
Write-Host ("== {0} from https://huggingface.co/{1} ({2:N0} MB)" -f $entry.path, $Repo, ($size / 1MB)) -ForegroundColor Cyan

$have = if (Test-Path $Dest) { (Get-Item $Dest).Length } else { -1 }
if ($have -gt $size) { Remove-Item $Dest -Force; $have = -1 }
if ($have -ne $size) { Get-File $url $Dest }
$got = (Get-Item $Dest).Length
if ($got -ne $size) { throw "$got bytes received, $size expected. Run the script again: it resumes." }
Write-Host "  Checking the SHA-256 (a minute)..."
$hash = (Get-FileHash $Dest -Algorithm SHA256).Hash.ToLower()
if ($want -and $hash -ne $want) {
    Remove-Item $Dest -Force
    throw "The SHA-256 $hash does not match the published $want; the file was deleted. Run the script again."
}
Write-Host "Model: $Dest (SHA-256 matches Hugging Face)." -ForegroundColor Green
Write-Output "MODEL_FILE=$Dest"
