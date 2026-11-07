<#
  SkyGuard QA (formerly QA Enterprise AI) - patch 23.3.31 (cumulative), for 23.2.2 or 23.3.0 to 23.3.30.

  23.3.31: AI analysis reports. A screenshot after every step, shown next to the step in Recent Runs; an AI analysis
  report of a run (AI-Driven Automation, Guided Browser Automation), of a performance run and of the Dashboard, edited
  by the tester, then a PDF to open or download and an Outlook email with the subject and body written and the PDF
  attached.

  23.3.30: SkyGuard QA. Product name, tagline and logo set in Preferences (header, reports, PDFs, emails); voice
  pages in navy and cyan; Air Force scene at the top of Agent Workflow; report PDFs printed from a file with an Edge
  or Chrome fallback and Make PDF; Dashboard acceptance criteria counted from the field and from criteria work items.

  23.3.29: Performance schedules. Run a saved scenario once, daily, on chosen weekdays, every N weeks or every
  N minutes, with pinned or latest versions, overlap and missed-run policies, a maximum runtime, the PDF report and
  an Outlook email; one run per due time. Optional start at Windows sign-in from the Startup folder (no admin).
  23.3.28: Reports, history and comparisons. A PDF report per run (made automatically when a run ends) with every
  section the test plan asks for, comparison and consolidated reports across runs, run history filters by build
  and date, comparability checks between runs, and defect drafts with severity and evidence for review.
  23.3.27: Live dashboard. A running test shows users against the planned shape, requests per second against the
  rate target, the 95th percentile per second, errors, per-request figures and the generator's CPU and memory,
  read every 2 seconds from the results JMeter writes. Targets show as a live indication, not the verdict.
  23.3.26: Workload and validation run. Ready-made load shapes (load, stress, spike, soak) built from plain
  thread groups, a request-rate target that paces a fixed pool of users (built-in Precise Throughput Timer;
  users are never added to reach a rate), and a one-user validation run that checks status codes, assertions
  and correlations before a load test. The saved script is never changed by either.

  23.3.25: Performance Test Data. Data sets with typed columns (CSV import and export keep leading zeros and
  Unicode; Windows-1252 files are read too), a row generator and AI-generated rows, secret columns kept in the
  Credential Manager. Scripts read a data set (CSV Data Set Config) and request values (JSON, form, query,
  header) are bound to its columns. Pre-run checks: missing columns, wrong types, too few rows.

  23.3.24: Record a performance script from a browser journey. A browser opens on this PC; the requests of what
  the tester does become a JMeter script with one transaction per step. Other hosts and static files are left out,
  values handed out by the server (CSRF fields, JSON tokens) are extracted and reused automatically, and typed
  passwords are saved in the Credential Manager and given to JMeter only while a test runs. HAR import too.

  23.3.23: Performance script editor. Import JMX files saved by JMeter (every element is kept, also the ones without
  a form), open generated plans as scripts, edit them as a tree (requests, transactions, extractors, assertions,
  timers, headers, variables), edit and validate the raw JMX, keep a version on each save with a diff and restore.
  Pre-run checks read the script: hosts from variables and HTTP defaults, users and duration from its thread
  groups, JMeter plugins, data files. Listeners are switched off for runs.

  23.3.22: Performance Testing foundation. New Performance Scenarios page (create, edit, clone, archive, search;
  every existing performance plan gets its scenario, nothing is lost), target environments with allowed hosts and
  load limits, pre-run checks (Java, JMeter, plan, disk space, hosts, users), a run queue (one run at a time on this
  PC), Stop and Emergency stop, execution state shown apart from the verdict, a snapshot of exactly what ran
  (config.json), the load generator's CPU / memory / disk (psutil, installed offline from this patch), and runs
  left running by a closed or crashed application marked Interrupted with their partial results.

  23.3.21: Automation Studio > Scheduled Runs (schedule scripts once, daily, Monday to Friday, chosen days or every
  few hours, until a date or continuing; several browsers; runs at the same time; Run now; run history), Settings >
  Outlook Email (SMTP / Microsoft 365 or the Outlook app) for the run report, Passed / Failed written to TFS test
  runs; recording double-click, right-click, hover, drag and drop, iframes, pop-ups, new tabs and click-to-check;
  Apply all healed steps; Re-record from a step in Edit Script.

  23.3.20: Record more steps opens the browser without recording. Record / Pause / Resume (on the page and on a bar in
  the browser window) decide what is recorded; the steps go in at the chosen step, at several places in one session,
  or replace broken steps; the script's earlier steps can be played first.

  23.3.19: the Data button opens its Excel / CSV column list next to the button.

  23.3.18: date and time pickers are recorded as one step on the field (by its label) with a rule (Current system
  date +/- days, current system time +/- hours / minutes); pick_date uses the calendar's month and year drop-downs
  and types in the field's own format (OrangeHRM yyyy-dd-mm); older calendar clicks can be replaced with one date
  step in Edit Script; a Data button picks an Excel / CSV column for a step value.

  23.3.17: dates and times follow the calendar: recorded and AI-generated dates / times become rules (Current system
  date +/- days, current system time +/- hours and minutes) with a Rule button in Edit Script; hours and minutes now
  work in recorded scripts. New upload_file / download_file steps with checks of the downloaded PDF, Excel, CSV or
  Word file. Test data imported from Excel or CSV: one run per row with a result per row.

  23.3.16: Delete script on AI-Driven Automation > Script Inventory, Guided Browser Automation > Scripts and Hybrid
  Test Automation > Scripts (also on each script's own page), always after a "Delete script?" confirmation. A deleted
  script leaves the list, Download, Play Selected and Run all; its versions and evidence are kept (generated test files
  are moved to the workspace .deleted folder, recorded scripts to the scripts _archived folder). A running script is
  not deleted.

  23.3.15: AI-Driven Automation (Generate Automation) no longer fails with "On-Premise AI unavailable: the server
  did not answer in time". The AI is sent only the elements each Test Case needs (all pages still listed, e.g. about
  60 of 290 selectors), waits up to the generation timeout (10 minutes) instead of 2, gets an answer limit sized to
  the number of steps and the answer shape enforced by the AI server; a call that still times out is asked once more
  with fewer elements. The run log shows how many elements and tokens were sent.

  23.3.14: new Dashboard (Azure DevOps figures, QA progress bars, pie and bar charts for user stories, acceptance
  criteria, test cases, test data, defects and pass/fail; Performance), no agent cards on the Dashboard or the
  Voice Command Center, Air Force squadron agents everywhere, Agent Workflow on a defense backdrop with a
  blinking running agent and Remaining / Total time.

  23.3.13: test data is also written to the test case's "Test Data" tab (custom Test Data and Test Data ID
  fields) when the Test Case type has it: for new test cases from AI Test Design and for Write to TFS. The
  Summary is written as before.

  23.3.12: test data generation works with a small local model: short answer keys, a JSON schema the AI server
  enforces while the model writes, each row checked on its own, a cut-off answer keeps its complete rows, and no
  second "repair" call. 3 rows per AI call.

  23.3.11: -UseAppServer (and -FastModel) stop your own llama-server and let the application start it: one slot
  of 8,192 tokens on a CPU, so llama-server keeps the instructions it has read and reads only the new criterion of
  each call; your own server's model is kept (or the fast 4B model with -FastModel). The test case prompt is
  ordered so everything shared comes first. MANIFEST.json with a UTF-8 BOM no longer stops the patch.

  23.3.10: test case generation no longer fails with HTTP 500 "Context size has been exceeded": calls run two at a
  time only when the AI server gives each one at least 8,192 tokens; an answer that runs out of context is written
  again one call at a time (not retried as it is); the answer limit never exceeds the context that is left; the run
  log shows the AI context per call.

  23.3.9: faster test case generation.
  Includes 23.3.8 (no more HTTP 400 in test case generation) and 23.3.7-r5 (Faster-Whisper, reserved-port fix).

  23.3.9:
  * the AI server this application starts (llama-server) gets 2 parallel slots, all layers on the GPU when it has
    one, no hidden "thinking" text and prompt reuse; options an older llama-server does not know are left out, and
    if it still does not start it is started again with the 23.3.8 options,
  * test case generation sends 2 AI calls at a time (a CPU writes two answers almost as fast as one) and asks for
    shorter cases (3 to 6 steps, one-sentence objective),
  * the AI check measures the writing speed (tokens per second) and the patch prints how the AI server is set up,
  * -FastModel: switches the local model to Qwen3-4B-Instruct-2507 (about 2.5 GB, about twice as fast as a 7B/8B
    model); see GET_FAST_LOCAL_MODEL.ps1.

  23.3.7:

  * installs Faster-Whisper 1.2.1 (CTranslate2) and its packages from this zip into <app>\runtime\faster-whisper
    (offline; the application's own Python packages are not touched),
  * installs an English speech model (small.en by default): from -ModelZip, from a faster-whisper-<model>.zip next
    to the patch or in Downloads, Desktop or Documents, or (unless -Offline) downloaded from Hugging Face with the
    SHA-256 checked,
  * the application starts the Faster-Whisper server on 127.0.0.1:8766 when it starts and stops it when it stops,
  * Voice settings point at it (Speech recognition: Local speech server, type Faster-Whisper on this PC),
  * Check Voice Setup now sends a real test recording: "HTTP 404" is a FAIL, no longer a PASS,
  * includes everything from 23.3.0 to 23.3.6.

  It stops the application, backs up the files it replaces, copies the 23.3.7 files, records them in MANIFEST.json,
  starts the application again and checks /api/health and the voice setup. The wake name, reply wording, TFS
  connection, On-Premise AI settings, database and reports are kept. If anything fails while application files are
  replaced, the previous files are restored automatically. If Faster-Whisper cannot be set up, the application is
  still updated and your current speech recognition setting is left as it was.

  Usage (PowerShell, in the extracted patch folder):
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -ModelZip "$env:USERPROFILE\Downloads\faster-whisper-small.en.zip"
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -Model base.en      # faster on a slow PC, less accurate
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -Offline            # never use the Internet
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -InstallDir "D:\Apps\AIAgenticTestAgent"
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -FastModel        # the app runs llama-server with the fast model
    powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -UseAppServer     # the app runs llama-server with your model
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [ValidatePattern('^[A-Za-z0-9._-]+$')][string]$Model = "small.en",
    [string]$ModelZip = "",
    [switch]$Offline,
    [switch]$NoStart,
    [switch]$FastModel,
    [string]$FastModelFile = "",
    [switch]$UseAppServer
)
$UseApp = $UseAppServer -or $FastModel -or ($FastModelFile -ne "")

$ErrorActionPreference = "Stop"
$PatchVersion = "23.3.31"
$PatchBuild = "ai-reports"   # cumulative: 23.3.7-r5 + 23.3.8 to 23.3.30 + AI analysis reports
$Here = $PSScriptRoot

function Say([string]$msg) { Write-Host $msg }
function Step([string]$msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host ""; Write-Host "PATCH FAILED: $msg" -ForegroundColor Red; exit 1 }

# --- 1. Find the installation ------------------------------------------------------------------------------------
Say "QA Enterprise AI patch $PatchVersion ($PatchBuild)"
Step "Finding QA Enterprise AI"
$roots = @()
if ($InstallDir) { $roots += $InstallDir }
$roots += (Join-Path $env:LOCALAPPDATA "Programs\AIAgenticTestAgent")
$App = $null
foreach ($r in $roots) {
    if (Test-Path (Join-Path $r "app\qa_platform\__init__.py")) { $App = (Resolve-Path (Join-Path $r "app")).Path; break }
    if (Test-Path (Join-Path $r "qa_platform\__init__.py")) { $App = (Resolve-Path $r).Path; break }
}
if (-not $App) { Fail "No installation found in: $($roots -join '; '). Run again with -InstallDir ""<folder that holds app or qa_platform>""." }
if (-not (Test-Path (Join-Path $Here "app\qa_platform\__init__.py")) -or -not (Test-Path (Join-Path $Here "faster-whisper\wheels")) -or -not (Test-Path (Join-Path $Here "wheels"))) { Fail "The patch folder is incomplete. Extract the whole zip again." }
if ($ModelZip -and -not (Test-Path $ModelZip)) { Fail "The model zip or folder was not found: $ModelZip" }

$Installed = Test-Path (Join-Path $App "INSTALLED.json")
if ($Installed) { $Root = Split-Path $App -Parent; $Data = Join-Path $Root "data" } else { $Root = $App; $Data = Join-Path $App "data" }
if ($env:QA_HOME) { $Data = $env:QA_HOME }

$Py = Join-Path $App "runtime\python\python.exe"
if (-not (Test-Path $Py)) { $Py = Join-Path $App ".venv\Scripts\python.exe" }
if (-not (Test-Path $Py)) { Fail "The application's Python was not found in $App\runtime\python or $App\.venv." }

$init = Get-Content (Join-Path $App "qa_platform\__init__.py") -Raw
$Before = if ($init -match '__version__\s*=\s*"([^"]+)"') { $Matches[1] } else { "unknown" }
Say "Application folder : $App"
Say "Data folder        : $Data  (kept as it is)"
Say "Installed version  : $Before  ->  $PatchVersion"
if (@("23.2.2", "23.3.0", "23.3.1", "23.3.2", "23.3.3", "23.3.4", "23.3.5", "23.3.6", "23.3.7", "23.3.8", "23.3.9", "23.3.10", "23.3.11", "23.3.12", "23.3.13", "23.3.14", "23.3.15", "23.3.16", "23.3.17", "23.3.18", "23.3.19", "23.3.20", "23.3.21", "23.3.22", "23.3.23", "23.3.24", "23.3.25", "23.3.26", "23.3.27", "23.3.28", "23.3.29", "23.3.30", $PatchVersion) -notcontains $Before) {
    Fail "This patch needs version 23.2.2 or 23.3.0 to 23.3.30 (found $Before). Apply qa-enterprise-ai-patch-23.2.2-cumulative.zip first, then this one."
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "patch-$PatchVersion-$Stamp.txt"
try { Start-Transcript -Path $Log -Append | Out-Null } catch { }

# --- 2. Stop the running application -----------------------------------------------------------------------------
Step "Stopping the application"
$Port = 8765
$settingsFile = Join-Path $Data "settings.json"
if (Test-Path $settingsFile) {
    try { $p = (Get-Content $settingsFile -Raw | ConvertFrom-Json).server.port; if ($p) { $Port = [int]$p } } catch { }
}
$pids = @()
try { $pids += (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).OwningProcess } catch { }
$pids += (Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) }).Id
$pids = $pids | Where-Object { $_ -and $_ -ne 0 } | Sort-Object -Unique
foreach ($id in $pids) {
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) { Say "Stopping $($proc.ProcessName) (PID $id)"; Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
}
if ($pids) { Start-Sleep -Seconds 3 } else { Say "It was not running." }

# --- 3. Back up and replace the application files ----------------------------------------------------------------
Step "Backing up the files this patch replaces"
$Backup = Join-Path $Root "patch-backups\$Before-to-$PatchVersion-$Stamp"
New-Item -ItemType Directory -Force -Path $Backup | Out-Null
$items = Get-ChildItem (Join-Path $Here "app") | ForEach-Object { $_.Name }
foreach ($name in ($items + "MANIFEST.json")) {
    $src = Join-Path $App $name
    if (Test-Path $src) { Copy-Item $src -Destination $Backup -Recurse -Force }
}
Say "Backup: $Backup"

function Restore-Backup {
    Say "Restoring the previous files from $Backup ..."
    & robocopy $Backup $App /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
}

Step "Copying the $PatchVersion application files"
try {
    Get-ChildItem (Join-Path $App "qa_platform") -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    & robocopy (Join-Path $Here "app") $App /E /IS /IT /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy could not copy the files (exit code $LASTEXITCODE). Is a file still open?" }

    $env:QA_HOME = $Data
    Push-Location $App
    $ErrorActionPreference = "Continue"   # Windows PowerShell 5.1 turns a native command's stderr into an error
    try { $now = (& $Py -E -s -c "import qa_platform, qa_platform.main; print(qa_platform.__version__)" 2>&1 | Select-Object -Last 1) }
    finally { Pop-Location; $ErrorActionPreference = "Stop" }
    if ("$now".Trim() -ne $PatchVersion) { throw "The new files do not start correctly: $now" }
    Say "Application files: version $now loads correctly."
} catch {
    Restore-Backup
    try { Stop-Transcript | Out-Null } catch { }
    Fail "$($_.Exception.Message) The previous version was restored. Log: $Log"
}

$ErrorActionPreference = "Continue"   # from here on a problem is reported, never half-applied
$list = Join-Path $env:TEMP "qa-patch-files-$Stamp.txt"
$appSrc = (Resolve-Path (Join-Path $Here "app")).Path
Get-ChildItem $appSrc -Recurse -File | ForEach-Object { $_.FullName.Substring($appSrc.Length + 1) } | Set-Content -Path $list -Encoding UTF8
& $Py -E -s (Join-Path $Here "patch_helper.py") manifest --app $App --files $list
Remove-Item $list -ErrorAction SilentlyContinue

# --- 3a. psutil for the load generator monitor (23.3.22) ---------------------------------------------------------
Step "Load generator monitoring (psutil)"
& $Py -E -s (Join-Path $Here "patch_helper.py") psutil-install --wheels (Join-Path $Here "wheels")
if ($LASTEXITCODE -ne 0) { Write-Host "psutil was not installed; performance runs still work without CPU / memory monitoring." -ForegroundColor Yellow }

# --- 3b. Local AI server (23.3.9) ---------------------------------------------------------------------------------
Step "Local AI server"
$Helper = Join-Path $Here "patch_helper.py"
& $Py -E -s $Helper llm-stop-old --data $Data
& $Py -E -s $Helper llm-speed --data $Data
$OwnModel = ""
$OwnPids = @()
if ($UseApp) {
    # Your own llama-server holds the AI port; stop it so the application can start llama-server with its options.
    $aiPort = 8080
    try { $u = "$((Get-Content $settingsFile -Raw | ConvertFrom-Json).ai.base_url)"; if ($u -match ':(\d+)(/|$)') { $aiPort = [int]$Matches[1] } } catch { }
    $conns = @(Get-NetTCPConnection -LocalPort $aiPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($cn in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($cn.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        if ($proc.Name -like "llama-server*") {
            $cmd = "$($proc.CommandLine)"
            if ($cmd -match '(?:^|\s)(?:-m|--model)\s+(?:"([^"]+)"|(\S+))') {
                $OwnModel = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
                if (-not [IO.Path]::IsPathRooted($OwnModel) -and $proc.ExecutablePath) { $OwnModel = Join-Path (Split-Path $proc.ExecutablePath -Parent) $OwnModel }
            }
            Say "Your llama-server on port $aiPort (PID $($proc.ProcessId)): $cmd"
            $OwnPids += $proc.ProcessId
        } else {
            Write-Host "Port $aiPort is used by $($proc.Name) (PID $($proc.ProcessId)), not by llama-server: it was left running and the application cannot start its own AI server on that port." -ForegroundColor Yellow
        }
    }
}
$FastOk = $false
if ($FastModel -or $FastModelFile) {
    $gguf = $FastModelFile
    if (-not $gguf) {
        $found = & $Py -E -s $Helper llm-find-model --patch-dir $Here
        if ($LASTEXITCODE -eq 0 -and $found) { $gguf = "$found".Trim(); Say "Found the fast model: $gguf" }
    }
    if (-not $gguf -and -not $Offline) {
        Say "Downloading the fast model (about 2.5 GB) into Downloads; an interrupted download resumes when run again..."
        try {
            $lines = @(& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here "GET_FAST_LOCAL_MODEL.ps1"))
            foreach ($line in $lines) { if ("$line" -like "MODEL_FILE=*") { $gguf = "$line".Substring(11).Trim() } else { Write-Host $line } }
        } catch { Say "Download failed: $($_.Exception.Message)" }
    }
    if ($gguf -and (Test-Path $gguf)) {
        & $Py -E -s $Helper llm-model --data $Data --app $App --source $gguf
        $FastOk = ($LASTEXITCODE -eq 0)
    } else {
        Write-Host "The fast model was not installed; the current model is kept. Run GET_FAST_LOCAL_MODEL.ps1, then this patch again with -FastModel." -ForegroundColor Yellow
    }
}
if ($UseApp) {
    if ($FastOk -or -not $OwnModel) { & $Py -E -s $Helper llm-manage --data $Data --app $App }
    else { & $Py -E -s $Helper llm-manage --data $Data --app $App --source $OwnModel }
    if ($LASTEXITCODE -eq 0) {
        foreach ($id in $OwnPids) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        if ($OwnPids) {
            Start-Sleep -Seconds 2
            Say "Stopped your own llama-server. From now on the application starts llama-server itself when it starts:"
            Say "do not start your own any more (if it starts with Windows, remove it from there)."
        }
    } else {
        Write-Host "The application cannot start llama-server (see above); your own llama-server was left running." -ForegroundColor Yellow
    }
}
& $Py -E -s $Helper llm-report --data $Data --app $App

# --- 4. Voice reply wording -------------------------------------------------------------------------------------
Step "Voice agent settings"
& $Py -E -s (Join-Path $Here "patch_helper.py") voice-style --data $Data

# --- 5. Faster-Whisper ---------------------------------------------------------------------------------------------
Step "Faster-Whisper speech recognition"
$Helper = Join-Path $Here "patch_helper.py"
$FwHome = Join-Path $App "runtime\faster-whisper"
$FwReady = $false
$dl = $null
New-Item -ItemType Directory -Force -Path $FwHome | Out-Null
& $Py -E -s $Helper fw-install --wheels (Join-Path $Here "faster-whisper\wheels") --home $FwHome
$PkgOk = ($LASTEXITCODE -eq 0)
if ($PkgOk) {
    $ModelDir = Join-Path $FwHome "models\$Model"
    $src = $ModelZip
    if (-not $src -and (Test-Path (Join-Path $ModelDir "model.bin"))) {
        Say "Model $Model is already installed in $ModelDir."
    } else {
        if (-not $src) {
            $found = & $Py -E -s $Helper fw-find-model --patch-dir $Here --model $Model
            if ($LASTEXITCODE -eq 0 -and $found) { $src = "$found".Trim(); Say "Found the model: $src" }
        }
        if (-not $src -and -not $Offline) {
            Say "No faster-whisper-$Model.zip found; downloading $Model from Hugging Face (about 465 MB for small.en)..."
            $dl = Join-Path $env:TEMP "qa-fw-model-$Stamp"
            try {
                & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here "GET_FASTER_WHISPER_MODEL.ps1") -Model $Model -OutDir $dl -NoZip
                if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $dl "faster-whisper-$Model\model.bin"))) { $src = Join-Path $dl "faster-whisper-$Model" }
            } catch { Say "Download failed: $($_.Exception.Message)" }
            if (-not $src) { Say "The model could not be downloaded (see the message above)." }
        }
        if ($src) {
            $modelArgs = @("fw-model", "--source", $src, "--home", $FwHome)
            if ($PSBoundParameters.ContainsKey("Model")) { $modelArgs += @("--name", $Model) }
            $lines = @(& $Py -E -s $Helper @modelArgs)
            $rc = $LASTEXITCODE
            foreach ($line in $lines) {
                if ("$line" -like "MODEL=*") { $Model = "$line".Substring(6).Trim() } else { Write-Host $line }
            }
            if ($rc -ne 0) { Say "The model was not installed." }
            $ModelDir = Join-Path $FwHome "models\$Model"
            if ($dl -and (Test-Path $dl)) { Remove-Item $dl -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }
    if (Test-Path (Join-Path $ModelDir "model.bin")) {
        # A Windows voice says a command; Faster-Whisper must write it down as the service will.
        $wake = "Captain"
        try { $w = (Get-Content (Join-Path $Data "settings.json") -Raw | ConvertFrom-Json).voice.agent_command_name; if ($w) { $wake = $w } } catch { }
        $sentence = "$wake, open Test Plans."
        $wav = Join-Path $env:TEMP "qa-fw-selftest-$Stamp.wav"
        try {
            Add-Type -AssemblyName System.Speech
            $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
            $synth.SetOutputToWaveFile($wav, $fmt); $synth.Speak($sentence); $synth.Dispose()
        } catch { $wav = "" }
        $prompt = "$wake, open Test Plans. Test Suites, Test Cases, Test Data, user story, acceptance criteria, TFS, Playwright, regression, smoke test, Sprint 25."
        $testArgs = @("fw-selftest", "--home", $FwHome, "--model", $Model, "--prompt", $prompt)
        if ($wav -and (Test-Path $wav)) { $testArgs += @("--wav", $wav, "--expect", $sentence) }
        & $Py -E -s $Helper @testArgs
        $FwReady = ($LASTEXITCODE -eq 0)
        if ($wav) { Remove-Item $wav -ErrorAction SilentlyContinue }
    }
}
if ($FwReady) {
    & $Py -E -s $Helper fw-settings --data $Data --model $Model
    Say "Faster-Whisper: READY. It starts with the application on http://127.0.0.1:8766 (no Internet needed)."
} else {
    Write-Host ""
    Write-Host "Faster-Whisper: NOT READY. Your speech recognition setting was left as it was." -ForegroundColor Yellow
    if (-not $PkgOk) { Say "The Faster-Whisper packages could not be installed (see above)." }
    else {
        Say "The speech model is missing or could not be loaded (see above). On a PC with Internet, run in the extracted"
        Say "patch folder:"
        Say "    powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1 -Model $Model"
        Say "copy faster-whisper-$Model.zip into Downloads on this PC, and run this patch again."
    }
}
Say "Wake-name listening: on by default (the wake name stays as you set it under Voice & Command Agent)."

# --- 6. Start and check ------------------------------------------------------------------------------------------
$Health = $null
if (-not $NoStart) {
    Step "Starting QA Enterprise AI"
    $launcher = Join-Path $Root "START_QA_PLATFORM.cmd"
    if (-not (Test-Path $launcher)) { $launcher = Join-Path $App "START_QA_PLATFORM.cmd" }
    Start-Process -FilePath $launcher -WorkingDirectory (Split-Path $launcher -Parent)
    for ($i = 0; $i -lt 45 -and -not $Health; $i++) {
        Start-Sleep -Seconds 2
        try { $Health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 } catch { }
    }
}

if ($Health -and $FwReady) {
    Step "Voice setup check (as CHECK VOICE SETUP shows it)"
    try {
        $diag = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/voice/diagnostics" -ContentType "application/json" -Body "{}" -TimeoutSec 200
        foreach ($c in $diag.checks) {
            $color = if ($c.status -eq "PASS") { "Green" } elseif ($c.status -eq "FAIL") { "Red" } else { "Yellow" }
            Write-Host ("  {0,-38} {1,-6} {2}" -f $c.name, $c.status, $c.detail) -ForegroundColor $color
        }
    } catch { Say "The voice check did not answer: $($_.Exception.Message)" }
}

if ($Health) {
    Step "On-Premise AI check with the writing speed (a CPU model may need a few minutes to load)"
    $aiReady = $false
    for ($i = 0; $i -lt 60 -and -not $aiReady; $i++) {
        try { $m = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ai/models" -TimeoutSec 10; $aiReady = $true } catch { Start-Sleep -Seconds 5 }
    }
    if (-not $aiReady) { Say "The AI server did not answer within 5 minutes; check it under AI settings." }
    else {
        try {
            $ai = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/api/ai/diagnostics" -ContentType "application/json" -Body "{}" -TimeoutSec 600
            foreach ($c in $ai.checks) {
                $color = if ($c.status -eq "PASS") { "Green" } elseif ($c.status -eq "FAIL") { "Red" } else { "Yellow" }
                Write-Host ("  {0,-24} {1,-6} {2}" -f $c.name, $c.status, $c.detail) -ForegroundColor $color
            }
        } catch { Say "The AI check did not answer: $($_.Exception.Message)" }
    }
}

Step "Result"
if ($Health) { Say "Running: http://127.0.0.1:$Port/  version $($Health.version)" }
elseif ($NoStart) { Say "Patched to $PatchVersion. Start it with $Root\START_QA_PLATFORM.cmd." }
else { Say "Patched to $PatchVersion, but the health check did not answer within 90 seconds. Look at the application window." }
Say ""
Say "Next: press Ctrl+F5 once in the browser. Allow the microphone when the browser asks."
Say "The voice assistant at the top right shows 'Say ""<your wake name>""' when it is listening."
Say "Log: $Log"
try { Stop-Transcript | Out-Null } catch { }
exit 0
