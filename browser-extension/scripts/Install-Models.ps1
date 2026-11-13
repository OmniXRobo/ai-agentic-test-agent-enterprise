<#
  Installs the local AI model (Qwen2.5-1.5B-Instruct Q4_K_M GGUF) and the speech model (Faster-Whisper small.en) for
  the SkyGuard companion, from an existing SkyGuard QA installation on this PC or from a folder you provide (offline
  media). Every file is checked against the SHA-256 the SkyGuard offline lock pins. Nothing is downloaded.

  powershell -ExecutionPolicy Bypass -File Install-Models.ps1 [-From <folder>] [-SkipSpeech] [-SkipAI]
#>
[CmdletBinding()]
param([string]$From = "", [switch]$SkipSpeech, [switch]$SkipAI)
$ErrorActionPreference = "Stop"
$Target = Join-Path $env:LOCALAPPDATA "SkyGuardAgent"
$Models = Join-Path $Target "models"
New-Item -ItemType Directory -Force -Path $Models | Out-Null
$GGUF = @{ name = "qwen2.5-1.5b-instruct-q4_k_m.gguf"; sha = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e" }
$FW = @{ "model.bin" = "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a"; "config.json" = "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae";
         "tokenizer.json" = "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"; "vocabulary.txt" = "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf" }
$roots = @()
if ($From) { $roots += $From }
$roots += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent\app\runtime"), (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent\runtime")
function Hash([string]$p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() }

if (-not $SkipAI) {
    $found = $null
    foreach ($r in $roots) { if (Test-Path $r) { $found = Get-ChildItem -Path $r -Recurse -Filter $GGUF.name -ErrorAction SilentlyContinue | Select-Object -First 1; if ($found) { break } } }
    if (-not $found) { Write-Warning "$($GGUF.name) not found in: $($roots -join '; '). Use -From <folder>. The agent still works without AI (deterministic parsing)." }
    elseif ((Hash $found.FullName) -ne $GGUF.sha) { Write-Warning "$($found.FullName) does not match the pinned SHA-256; not installed." }
    else { Copy-Item $found.FullName (Join-Path $Models $GGUF.name) -Force; Write-Host "AI model installed: $(Join-Path $Models $GGUF.name)" }
}
if (-not $SkipSpeech) {
    $dir = $null
    foreach ($r in $roots) { if (Test-Path $r) { $c = Get-ChildItem -Path $r -Recurse -Filter "model.bin" -ErrorAction SilentlyContinue | Where-Object { (Hash $_.FullName) -eq $FW["model.bin"] } | Select-Object -First 1; if ($c) { $dir = $c.DirectoryName; break } } }
    if (-not $dir) { Write-Warning "Faster-Whisper small.en not found (model.bin with the pinned SHA-256). Voice stays off; typing works the same way." }
    else {
        $dest = Join-Path $Models "faster-whisper-small.en"
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        foreach ($k in $FW.Keys) {
            $src = Join-Path $dir $k
            if (-not (Test-Path $src) -or (Hash $src) -ne $FW[$k]) { Write-Warning "$src is missing or does not match its SHA-256; speech model not installed."; Remove-Item -Recurse -Force $dest; $dest = $null; break }
            Copy-Item $src (Join-Path $dest $k) -Force
        }
        if ($dest) { Write-Host "Speech model installed: $dest" }
    }
}
Write-Host "Restart the companion (Start menu: SkyGuard Agent (start companion), after closing the running one) to use the models."
