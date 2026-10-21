# Architecture

AI Agentic Test Agent Enterprise is a QA orchestration platform that runs on a
Windows PC, online or fully offline, without administrator rights.

```
Browser dashboard (web/, plain ES modules, no CDN)
        │  REST (JSON)            ▲  Server-Sent Events (/api/events/stream)
        ▼                         │
FastAPI backend (qa_platform/)  ──┘
  ├─ api/           HTTP routes, structured JSON errors
  ├─ agents/        Enterprise QA agents (BaseAgent → events)
  ├─ events/        names.py (central event names), bus.py (event bus), state.py (agent state)
  ├─ notifications  event-driven notification service
  ├─ runs           execution history (RUN IDs)
  ├─ storage/db     SQLite (WAL) with numbered migrations
  ├─ config         settings.json + filesystem layout (no secrets)
  ├─ logging_setup  JSON logs with secret redaction
  └─ terminology    central display-name mapping
        │
        ├─ TFS / Azure DevOps REST          (Stage 2)
        ├─ AI gateway → On-Premise LLM      (Stage 3)
        ├─ Playwright Python                (Stage 4)
        └─ JMeter non-GUI + Java            (Stage 5)
```

## Runtime event model

Every major operation publishes a `QAEvent` through `EventBus.publish`:

```json
{
  "id": 42, "timestamp": "2026-10-21T10:24:12.000+04:00", "name": "agent.started",
  "agent_id": "test_execution", "agent_name": "Test Execution Agent",
  "status": "RUNNING", "activity": "Running Test Case #1280", "progress": 0,
  "run_id": "RUN-20261021-102412-A1B2C3", "test_case_id": "1280", "user_story_id": "118",
  "source": "playwright.runner", "details": {}
}
```

* Event names live in `qa_platform/events/names.py`; unknown names are rejected.
* Events are persisted (SQLite), applied to `AgentStateManager`, turned into
  notifications where a rule exists, and streamed to SSE clients.
* `publish` is thread-safe, so Playwright and JMeter worker threads can report progress.
* `details` are redacted before storage, so secrets never reach the database or the UI.

### Agent status is never guessed

Agents change state only through `BaseAgent.activity()`:

```python
with agent.activity("Reading User Story #118", user_story_id="118") as act:
    act.progress(40, "Acceptance Criteria retrieved: 4")
    act.complete("Requirements Intelligence Agent PASSED")
```

Entering emits `agent.started` (RUNNING), leaving emits `agent.completed`
(PASSED) or, on exception, `agent.failed` (FAILED). The dashboard highlights a
card only while it is RUNNING; there are no frontend timers that change status.
After a restart, an agent whose last event was `agent.started` is shown as
FAILED (interrupted) instead of glowing forever.

### Live updates

The dashboard opens one `EventSource` to `/api/events/stream`. The stream sends
a snapshot first, then events, notifications and unread counts, plus a
heartbeat every 15 s. Browsers reconnect automatically with `Last-Event-ID`
and the server replays missed events. Pages update individual cards and append
feed rows instead of rebuilding the page.

## Errors

All API errors use one shape:

```json
{"ok": false, "error_code": "TFS_AUTH_FAILED", "message": "Authentication failed.",
 "details": "...", "agent": "Requirements Intelligence Agent", "run_id": "..."}
```

The frontend (`web/js/api.js`) only parses JSON responses and maps 401, 403,
404, 409, 429 and 500 to readable messages.

## Time

The Windows system clock is the only time source (`qa_platform/clock.py`).
Timestamps are local time with the system's UTC offset. There is no
application time zone or GMT setting.

## Configuration and secrets

* `data/settings.json` holds non-secret settings (server port, theme, TFS
  URLs/projects, AI server URL, defect creation mode, JMeter/Java paths).
  `PATCH /api/settings` refuses keys that look like secrets.
* TFS PATs and application credentials will be stored in Windows Credential
  Manager via `keyring` (Stage 2), separately from each other.
* `QA_HOME` overrides the data folder; by default it is `data/` next to the app,
  so a portable copy keeps its data with it.

## Visible terminology

`qa_platform/terminology.py` maps stable internal ids (e.g. `option_2`,
`local_llm`) to professional names (Guided Browser Automation, On-Premise LLM).
Internal ids are what get persisted; only display text uses the mapping.

## Agents

| id | Name | Delivered |
|---|---|---|
| requirements | Requirements Intelligence Agent | Stage 3 |
| test_planning | Test Planning & Strategy Agent | Stage 3 |
| test_suite_design | Test Suite Design Agent | Stage 3 |
| test_case_design | Test Case Design Agent | Stage 3 |
| test_data | Test Data Intelligence Agent | Stage 3 |
| app_discovery | Application Discovery Agent | Stage 4 |
| automation_engineering | Automation Engineering Agent | Stage 4 |
| guided_browser | Guided Browser Automation Agent | Stage 4 |
| test_execution | Test Execution Agent | Stage 4 |
| self_healing | Self-Healing Locator Agent | Stage 4 |
| performance_engineering | Performance Engineering Agent | Stage 5 |
| jmeter_load | JMeter Load Testing Agent | Stage 5 |
| failure_analysis | Failure & Quality Analysis Agent | Stage 6 |
| defect_intelligence | Defect Intelligence Agent | Stage 6 |
| orchestration | QA Orchestration Agent | Stage 1 (system diagnostics) |
| manual_playback | Manual Playback Agent | Stage 4 |
| reporting | Reporting & Analytics Agent | Stage 8 |
| voice_command | Voice Command Agent | Stage 7 |

## Build stages

1. Foundation (this stage): backend, event bus, agent registry, terminology,
   structured errors, dashboard shell, Agent Workflow, notifications, theme,
   system diagnostics.
2. TFS / Azure DevOps (Online and Server): connection diagnostics, Explorer,
   work item pages, credential storage.
3. On-Premise AI gateway and diagnostics; requirements and test design agents.
4. Playwright: AI-driven generation, guided recording, discovery, hybrid,
   execution with evidence, visual playback, self-healing.
5. JMeter: plan generation, non-GUI execution, JTL metrics, SLA evaluation,
   AI performance analysis.
6. Failure analysis and Defect Intelligence with duplicate detection.
7. Voice Command Agent.
8. Reports (HTML/PDF) and analytics.
9. Portable offline Windows package (`INSTALL_OFFLINE.cmd`, `VALIDATE_OFFLINE.cmd`).
