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
        ├─ TFS / Azure DevOps REST          (tfs/client.py, tfs/service.py)
        ├─ AI gateway → On-Premise LLM      (ai/gateway.py)
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

1. Foundation: backend, event bus, agent registry, terminology,
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

## TFS / Azure DevOps integration (Stage 2)

* `tfs/client.py`: one REST client for both modes. Online uses `https://dev.azure.com/{org}`, Server uses
  `https://server/tfs/{collection}`. Auth is a PAT (Basic, empty user). An HTML sign-in page (HTTP 203/302) is
  reported as `TFS_AUTH_FAILED`, never parsed. The Test Plans API falls back to the legacy Test API on older servers.
* `tfs/service.py`: profiles (non-secret parts in settings.json, PAT in Credential Manager under
  `tfs:online` / `tfs:offline`), the 6-step connection test, WIQL queries, detail with relations, traceability
  grouping, create/update through JSON Patch, test plans and suites, dashboard KPIs.
* Work item types are resolved from process categories (`Microsoft.RequirementCategory`, `Microsoft.BugCategory`,
  `Microsoft.TestCaseCategory`). Writes check the type's real field list and skip missing fields, reporting
  them in `skipped_fields`.
* Caching: query results 60 s, metadata 10 min; every write clears the caches. `tfs.unavailable` is published at most
  once per reason every 5 minutes.
* Limitation: Windows-integrated (NTLM/Kerberos) authentication for on-premise TFS is not supported yet; use a PAT
  (TFS 2017 or later).

## On-Premise AI and test design (Stage 3)

* `ai/gateway.py` calls `{base_url}/models` and `{base_url}/chat/completions` (OpenAI-compatible) with
  `trust_env=False`, so no proxy or Internet route is used. Requests are serialized because one local model
  serves them sequentially. `complete_json` sends the pydantic JSON schema, parses fenced or `<think>`-prefixed
  replies, validates, and asks once for a repair before failing with `AI_OUTPUT_INVALID`.
* `design/service.py` pipeline (one agent per step, all events carry the run id and user story id):
  Requirements Intelligence (read story, acceptance criteria, extract requirements) → Test Planning & Strategy →
  Test Case Design (coverage check against requirement ids) → Test Data Intelligence. Results are stored in
  `design_results`. Publishing (Test Suite Design Agent) creates test cases with `Microsoft.VSTS.TCM.Steps`,
  links them `TestedBy-Reverse` to the story, adds them to a suite, and stores datasets in `test_data`.

## Playwright automation (Stage 4)

* `automation/dsl.py`: scripts are JSON steps (`goto`, `click`, `fill`, `select`, `check`, `expect_*`, ...), each
  target carrying a selector, recorded alternates and an element fingerprint (role, accessible name, text, id,
  test id, label, placeholder, classes). `ValueResolver` resolves runtime values from the Windows system clock and
  the credential store and masks resolved passwords. `to_playwright_python` exports a readable script.
* `automation/store.py`: `data/automation/SCR-*/script.json` with `versions/vNNNN.json` and `history.log` on every
  save; delete moves the folder to `_archived`. `CredentialProfiles` keeps usernames in SQLite (`app_credentials`)
  and passwords in Credential Manager under the `appcred` namespace (TFS uses `tfs`).
* `automation/js/`: `fingerprint.js` (stable selectors and candidates), `overlay.js` (cursor and highlight inside a
  closed shadow root), `recorder.js` (sends actions through an exposed binding). All are added with
  `context.add_init_script`; nothing is written to the application under test.
* `automation/runner.py`: one browser run at a time in a worker thread. Each run writes `execution_results`,
  `data/evidence/{run_id}/` (screenshots, optional video, `execution.json`) and agent events. The Test Execution
  Agent runs AI/discovery/hybrid scripts; the Manual Playback Agent runs recorded ones.
* `automation/healing.py` + runner: weighted fingerprint similarity, threshold 0.62, minimum margin 0.08, and the
  winning selector must match exactly one visible element. `healing_events` stores original/healed selectors,
  confidence, top candidates and a screenshot; status moves `healed` → `validated` when the run passes. Applying
  a validated healing saves a new script version with the old selector kept as an alternate.
* `automation/discovery.py`: breadth-first same-origin crawl; endpoints normalized (`/items/42` → `/items/{id}`)
  for JMeter generation in Stage 5; stored in `discoveries` with a diff against the previous run of the same URL.
* `automation/generator.py`: prompt contains the TFS steps and the discovered element inventory only; generated
  selectors are checked against it and anything else is reported in the script notes.
* Browsers: `runtime/ms-playwright` is used when present (offline package); otherwise Playwright's per-user default.
  Without a desktop session runs default to headless.
* Tests (`tests/test_automation.py`) drive real headless Chromium against `tests/site/`: pass/fail with evidence,
  self-healing of a renamed id, low-confidence refusal, recording, discovery with endpoints and diff, AI generation
  with a scripted LLM, and checks that passwords never reach events, results or evidence.

## JMeter performance testing (Stage 5)

* `performance/models.py`: `PerfPlan` (requests, `LoadProfile`, `Sla`). `thread_groups()` turns each test type into
  standard JMeter thread groups with scheduler delay/duration: load and soak = one group; stress = N groups
  starting one step apart; spike = baseline plus a burst group. No JMeter plugins are needed.
* `performance/jmx.py`: JMX with a cookie manager, HTTP samplers, header managers, response assertions and a
  constant think-time timer per thread group.
* `performance/service.py`: writes `data/performance/{run_id}/plan.jmx` and runs
  `jmeter -n -t plan.jmx -l results.jtl -j jmeter.log -e -o report` with CSV output. A live tail of the JTL
  publishes `performance.sample_summary` every 5 s. Stop sends `StopTestNow` to JMeter's non-GUI UDP port
  (4445-4455) and kills the process tree after 30 s if needed. One performance run at a time.
* `performance/jtl.py`: per-request and overall metrics (nearest-rank percentiles, as JMeter's aggregate report),
  response codes, top errors, and a timeline of up to 300 points. `performance/sla.py` checks limits per request
  and overall; a plan without limits reports `NOT_DEFINED`.
* AI analysis sends only the computed metrics and SLA results and requires each statement to cite them.
* Results are stored in `perf_runs` and in Execution History (`kind = performance`).
* Tests (`tests/test_performance.py`) cover profiles, JMX structure, JTL math, SLA, the API, and, when Java and
  JMeter are available (CI installs them), a real non-GUI run against a local site and a stop.

## Failure analysis and Defect Intelligence (Stage 6)

* `defects/analysis.py`: rule-based classification of the recorded failure (failed step and action, error text,
  self-healing attempts, SLA checks). Each category has a plain-language finding, a default severity and whether it is
  likely a product defect (`locator` and `test_setup` are not assumed to be). Repro steps always say the root cause
  was not determined. A signature (script, step, category, selector; or plan, SLA, scope) identifies the same failure.
* `defects/service.py`: listens on the event bus for `execution.failed` and `performance.failed` with a failed SLA and
  analyzes the run shortly after. Drafts live in `defect_drafts` (migration 6) with status draft, needs_review, created,
  linked or dismissed. A repeat signature appends an occurrence; if the draft is already in TFS a comment is added there.
* Duplicates: open defects of the process's defect type are scored by title and body similarity, plus the same selector
  and the same linked test case. 85% and above is a likely duplicate, 60% a possible one.
* Creation mode is the `defects.creation_mode` setting: `review`, `automatic` or `disabled`. Automatic creation skips
  likely duplicates and failures that may not be product defects, and records why on the draft.
* Creation uses the TFS work item type for the `bug` category, reads Severity allowed values to map 1-4, falls back to
  `System.Description` when the type has no Repro Steps field, uploads screenshots via `_apis/wit/attachments` and adds
  `System.LinkTypes.Related` links to the test case and user story. Skipped fields are listed on the draft.
* Tests (`tests/test_defects.py`) cover classification, signatures, scoring, review and automatic modes against the fake
  TFS server with real Playwright runs, linking to an existing defect, dismissal, recurrence comments, and a process
  with an Issue type and no Severity or Repro Steps fields.

## Voice Command Agent (Stage 7)

```
microphone ─► speech recognition ─► intent analysis ─► validation ─► spoken acknowledgement
              (local whisper server    (fixed grammar,     (IDs, plan,     │
               or browser)              then On-Premise AI) running work)  ▼
                                                            dashboard services (design, automation,
                                                            performance, defects, TFS) ─► monitor ─► spoken result
```

* `voice/intents.py`: a fixed grammar maps a sentence to one of the supported commands and its parameters (number
  words such as "a hundred" and "five minutes" are understood). Page names come from the same labels as the sidebar.
* `voice/service.py`: `VoiceService.handle()` records each command in `voice_commands` (migration 7), emits
  `voice.command.received`, then `accepted` (work started), `completed` or `failed`. Long work (test design,
  automation generation, test runs, JMeter runs, TFS creation) runs in a background thread under the Voice Command
  Agent card. Progress lines ("The load test has started.") and the final result are events whose `details.speak`
  the dashboard speaks for that conversation.
* Unmatched sentences go to the On-Premise AI with the list of commands. Its answer is validated: only a listed
  command, and only IDs, numbers and names that appear in what the user said. With no AI the reply is the
  unsupported-instruction message.
* Context per conversation: the user story, test case, script, run and performance plan last referred to, plus the
  page the user is on. Missing parameters are asked for and the next answer completes the command. TFS writes
  ("create defects for failed tests") wait for a spoken confirm, which expires after two minutes.
* Spoken performance runs pass `users`, `duration_s` and `kind` as overrides for that run only; the saved plan does
  not change and the plan actually used is in the run folder (`plan.json`, `plan.jmx`).
* After a failed run the agent waits for the automatic failure analysis (`DefectService.wait_for_automatic`) and
  reports a new defect, an existing defect that received the new evidence, a likely duplicate, or a draft to review.
* Speech recognition: `POST /api/voice/transcribe` takes 16 kHz mono WAV recorded in the browser and forwards it to the
  configured server (`/inference` for whisper.cpp, `/audio/transcriptions` for OpenAI-compatible servers) without
  proxies. Browser recognition is offered with a warning that Chrome and Edge use an online service. Replies use
  `speechSynthesis` voices with `localService` only.
* Tests (`tests/test_voice.py`) cover every example sentence from the requirements, follow-ups, asking for missing
  details, the AI fallback and its ID guard, confirmations, a real Playwright failure reported with the new TFS defect
  and its recurrence, a real JMeter run with spoken overrides, and the speech server API.

## Reports and analytics (Stage 8)

* `reports/analytics.py`: `AnalyticsService` computes everything from stored rows: `execution_results`,
  `healing_events`, `defect_drafts`, `perf_runs` and `test_data`, plus live TFS counts (`TfsService.kpis()` and
  `field_values("requirement", AcceptanceCriteria)` split the way test design reads criteria). When TFS is not
  reachable the TFS figures are absent with the reason, never estimated. Days are local dates of the system clock.
  * Pass rate on the dashboard uses the latest finished result per automated test; the Analytics page uses every
    execution in the period.
  * Flaky: a test with both outcomes in the period; flip rate = result changes between consecutive runs.
* `reports/service.py`: `ReportService.start()` stores a row in `reports` (migration 8) and generates in a background
  thread under the Reporting & Analytics Agent (`report.started`, `report.completed`, `report.failed`; the last two
  raise notifications). Files are `<data>/reports/<id>/report.html` and `report.pdf`. Reports still `running` when the
  application starts are marked failed as interrupted.
* The HTML is self-contained (inline CSS, server-side SVG charts from `reports/charts.py`, failure screenshots as data
  URIs, at most eight, only from the evidence folder) and is served with a CSP that allows no scripts. The PDF is the
  same page printed by Playwright Chromium (`page.pdf`, A4, page numbers); if printing fails the HTML is still kept and
  the reason is shown.
* API: `GET /api/analytics/summary|functional|performance`, `POST /api/reports` (202), `GET /api/reports`,
  `GET /api/reports/{id}`, `GET /api/reports/{id}/html|pdf` (`?download=true` for a file), `DELETE /api/reports/{id}`.
* Tests (`tests/test_reports.py`): empty install shows no figures, functional and performance analytics from recorded
  rows, a full report (HTML sections, escaping, PDF bytes, events, notification, delete), a report without TFS,
  validation, interrupted reports and the chart renderer.
