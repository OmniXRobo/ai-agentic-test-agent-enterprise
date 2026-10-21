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
