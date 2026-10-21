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

## What works in Stage 5

* **Performance Test Plans** for Load, Stress, Spike and Soak tests: HTTP requests (method, URL, body, headers,
  expected status code and text), users, ramp-up, duration, think time, stress steps or spike window, and SLAs
  (average, 90th/95th/99th percentile, error rate, throughput).
* Create a plan from an **Application Discovery** (its successful pages and observed GET API endpoints) or enter
  requests manually. Download the generated JMX to open it in JMeter.
* **Run with JMeter in non-GUI mode** with live progress on Agent Workflow, and stop a run at any time (results so far
  are kept). Java and JMeter are found from Performance settings, the portable package's `runtime` folder,
  `JAVA_HOME` / `JMETER_HOME`, or PATH.
* **Performance Results**: samples, error rate, average and percentiles and throughput; charts of response time,
  throughput, errors and active users over time; a per-request table with response codes and most frequent errors, SLA checks per
  request and overall, and JMeter's own HTML dashboard.
* **AI performance analysis** (On-Premise AI): observations and suspected bottlenecks that cite the measured values,
  and what cannot be determined from the results.

## What works in Stage 6

* When an automated test fails, or a performance run fails its SLAs, the **Failure & Quality Analysis Agent**
  classifies what the run recorded (assertion, navigation, interaction, locator, test setup, error rate, latency,
  throughput) and drafts a defect: title, severity, steps to reproduce, the error, the element, the environment and the
  failure screenshot. It never states a root cause the evidence does not show.
* The **Defect Intelligence Agent** compares each draft with the open defects in TFS (wording, the same element, the
  same linked test case) and lists likely and possible duplicates. The same failure seen again adds an occurrence to the
  existing draft and, once it is in TFS, a comment on that defect instead of a new one.
* **Defect creation** mode on **Defect Review**: *Review Before Create* (default), *Automatic* (product failures with
  no likely duplicate are created right away; locator and test setup failures still wait) or *Disabled*.
* **Create in TFS** uses your process's defect type (Bug, Defect or Issue), maps severity to its allowed values, skips
  fields the process does not have and lists them, attaches screenshots, and links the defect to the test case and
  user story. You can also link a draft to an existing defect or dismiss it with a reason.
* **Analyze failure** on a failed run in Execution History or Performance Results drafts a defect on demand.

## What works in Stage 7

* **Voice Commands**: press the microphone (on the Voice Commands page or in the top bar on any page) and speak, or
  type the command. The Voice Command Agent acknowledges by voice, runs the action through the same services the
  pages use, follows it while it runs, and speaks the result, for example "The test execution failed at step 8. I found
  a locator failure on the checkout button" or "The performance test completed successfully. P95 response time was
  1.4 seconds and the error rate was 0.2 percent."
* Supported commands: open any page, open a user story / test case / defect by number, generate test cases, generate
  automation, run or play a test case or a named test, run all automated tests, start a browser recording, run a
  performance test (with the users and duration you say, for that run only), stop the running test, show failed tests,
  show the performance report, analyze the failure, create defects for failed tests, and status. Anything else gets
  "I cannot perform that instruction because no supported QA action is mapped to it."
* Follow-ups use the conversation's context and the page you are on: "open user story 118", then "generate test
  cases"; "run test case 1280", then "run it again". A missing detail is asked for ("Which test case?").
  Creating defects in TFS asks you to say confirm first.
* **Speech recognition**: a local whisper.cpp server or any OpenAI-compatible speech server (works offline; nothing is
  downloaded), or the browser's recognition (needs Internet in Chrome and Edge), or off (typed commands only).
  Sentences outside the fixed phrases are mapped by the On-Premise AI when it is available; it can only choose a
  supported command and never adds an ID you did not say.
* **Spoken replies** use voices installed on the PC only, so they work without Internet.

## What works in Stage 8

* **Dashboard KPIs** now all come from real data: User Stories, Acceptance Criteria (counted from the criteria written
  on your user stories), Test Cases and Open Defects live from TFS; Test Data, Automated, Not Automated and Automation
  Coverage from saved data sets, automated tests and the TFS test case count; Passed, Failed and Functional Pass Rate
  from the latest result of each automated test; Performance Tests, Performance Failures and Performance SLA Pass Rate
  from finished JMeter runs. A figure that cannot be measured shows "—" with the reason.
* **Analytics** (Quality → Analytics): automation coverage, pass and failure rate, not automated, executions per day,
  defects drafted per day, flaky tests (tests that both passed and failed, with how often the result flipped), and
  self-healing per day and per test. Performance: P95, P99, throughput and error rate per run, SLA pass and fail,
  maximum tested concurrency, and, for one plan, each request's P95 in the latest run against the previous run.
  Periods are 7, 14, 30 or 90 days of the PC's local calendar.
* **Reports** (Quality → Reports): choose functional, performance or both, the period, optionally a user story and up
  to five performance runs. The Reporting & Analytics Agent builds an HTML report and prints the same page to PDF with
  the bundled Chromium (offline). Functional sections: executive summary, coverage, trends, user story, acceptance
  criteria, test cases with automation and test data, execution results (passed, failed, not run), defects,
  self-healing and failure screenshots. Performance sections: summary, load profile, response times with P90/P95/P99,
  throughput, error rate, graphs, transaction results, SLA compliance, performance defects and the AI analysis when one
  was requested. Reports are listed with View, HTML, PDF and Delete; a notification says when one is ready.
