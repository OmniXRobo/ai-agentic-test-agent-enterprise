# AI Agentic Test Agent Enterprise

An AI agentic QA orchestration platform: requirements intelligence, test
design, Playwright automation, JMeter performance testing, self-healing,
defect intelligence with TFS / Azure DevOps traceability, voice commands and
reporting. It runs on a Windows PC online or fully offline, with no
administrator rights needed.

The platform is built in stages; see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
It never displays sample or demo data: every number comes from your configured
systems or from real runs.

## Quick start (development)

```
SETUP_DEV.cmd          # creates .venv and installs requirements (per user)
START_QA_PLATFORM.cmd  # starts the runtime and opens http://127.0.0.1:8765/
```

On Linux or macOS:

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m qa_platform
```

Run the tests with `python -m pytest`.

## Data and settings

Runtime data (SQLite database, logs, evidence, reports, `settings.json`) lives
in `data/` next to the app, or in the folder named by `QA_HOME`. It is
git-ignored. Secrets are never written to `settings.json` or logs.

## What works in Stage 1

* Dashboard shell with navigation, light/dark theme (saved), live connection indicator
* Agent Workflow page: 18 agents with role-specific robots, status driven only by runtime events,
  Live Agent Activity stream over SSE
* Notification bell with real unread counts
* Execution History with run details
* System diagnostics run by the QA Orchestration Agent (Python, data folder, database,
  Playwright, Java, JMeter, On-Premise AI server, TFS profile)
* Structured JSON errors on every endpoint, JSON logs with secret redaction

## What works in Stage 2

* TFS Connection settings for **Azure DevOps Online** and **Local TFS / Azure DevOps Server**, with one active profile.
  The PAT is write-only and stored in the Windows Credential Manager.
* Connection test with PASS/FAIL and the exact reason for: Server Reachability, Authentication,
  Organization/Collection, Project, Work Item API, Test Management API
* TFS Explorer (scoped to the active profile's project) with Sprint/Iteration, Work Item Type, State, Assigned To and ID/title filters, plus a detail
  panel with acceptance criteria and traceability (linked test cases, defects, other links)
* User Stories, Acceptance Criteria, Test Cases, Defects, Test Plans and Test Suites pages on real TFS data, with
  create/edit written back to TFS
* Work item types come from the project's process categories, so "Bug", "Defect" or "Issue" are detected rather
  than hard-coded; fields missing from a process template are skipped instead of failing the save
* Dashboard KPIs (user stories, test cases, open defects) from the active project, cached for 60 seconds

## What works in Stage 3

* **On-Premise AI** settings (server URL, model, timeout, temperature, max tokens) with diagnostics:
  Configuration, Server Reachability, Model Loaded, Completion, Structured JSON Output. Works with any
  OpenAI-compatible local server (llama.cpp `llama-server` with a Qwen GGUF model, Ollama). No Internet, no downloads.
* **AI Test Design** from a user story: Requirements Intelligence, Test Planning & Strategy, Test Case Design and
  Test Data Intelligence agents run in turn and stream their progress to Agent Workflow
* Review the generated requirements, ambiguities, strategy, test cases (steps and expected results) and datasets,
  then the Test Suite Design Agent creates the selected test cases in TFS with steps, a "Tested By" link to the
  story, and optionally adds them to a test plan suite
* **Test Data** page: datasets linked to their TFS test cases, editable
* If the AI server is unavailable the agents fail with the reason; nothing is generated without it

## What works in Stage 4

* **Automation Studio** with four ways to create Playwright automation, all producing the same versioned script
  format and running through one runner:
  * **AI-Driven Automation**: the Automation Engineering Agent reads a TFS test case's steps and maps them to
    elements actually discovered on your application. Selectors it cannot verify are listed on the script for review.
  * **Guided Browser Automation**: a browser opens on your PC and records clicks, typing, selections and checks.
    Typed passwords are never stored; they become `{{CRED:role.password}}`.
  * **Application Discovery & Update**: crawls same-site pages (logout links skipped), inventories elements, forms
    and the XHR/fetch API endpoints the pages call, and shows what changed since the previous discovery. Build a
    script from selected elements.
  * **Hybrid Test Automation**: record extra steps into any script at any position.
* **Script editor**: steps table, runtime values (`{{TODAY+3d:%d/%m/%Y}}`, `{{DATA:field}}`, `{{CRED:role.username}}`),
  readable Playwright Python export, version history with restore, and archive instead of delete.
* **Execution** with visual playback: a cursor, element highlight and click ripple are injected into the browser at
  run time (never into the application's code), plus screenshots per failure and at the end, optional video, and
  steps with timings in Execution History.
* **Self-healing**: only after the recorded locator and its alternates fail, candidates from the live page are scored
  against the recorded fingerprint. A match is used only above 0.62 confidence and clearly ahead of the runner-up.
  Every attempt is stored with candidates and a screenshot. The script changes only when you apply a healing whose
  run then passed, and the previous version is kept.
* **Application Credentials**: role-based logins per application. Passwords are kept in the Windows Credential
  Manager, separate from the TFS PAT, resolved only at run time and masked in logs and evidence.
