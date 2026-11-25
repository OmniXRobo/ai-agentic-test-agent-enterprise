# Source and architecture audit: 23.12.0

## Release number

23.12.0. 23.11.2 (PR #56, branch `claude/project-thread-ssy5qz`, commit `54b69f0`) was the latest release when this
work started. API Testing is a new module with a database migration, so the minor number goes up (23.11 → 23.12),
as for 23.11.0. The browser extension stays 1.3.1: it does not change in this release, and API Testing does not use it.

## Where each part is

| Part | Where |
|---|---|
| Offline JSONPath subset | `qa_platform/apitesting/jsonpath.py` |
| Offline JSON Schema validator (draft 4 to 2020-12 keywords used by OpenAPI, local `$ref`, reports what it could not check) | `qa_platform/apitesting/schema.py` |
| Target policy (authorized targets, SSRF rules, own port, metadata, redirects), rate limiter, state-changing methods | `qa_platform/apitesting/safety.py` |
| OpenAPI 3.x / Swagger 2.0 (JSON, YAML, multi-file), cURL, Postman, Python imports | `qa_platform/apitesting/importers.py`; YAML: `qa_platform/vendor/yaml` (PyYAML 6.0.3, pure Python, `safe_load`) |
| Test generation (documented expectations, Needs review otherwise) | `qa_platform/apitesting/generator.py` |
| Assertions (status, header, content type, JSONPath operators, body, schema, response time) | `qa_platform/apitesting/assertions.py` |
| HTTP client: authentication providers, redaction, manual redirects, retries, size limit, TLS | `qa_platform/apitesting/client.py` |
| Failure classification and the local AI refinement | `qa_platform/apitesting/analysis.py`; triage classes added in `qa_platform/defects/triage.py` |
| pytest + httpx script generation, validation and the network guard | `qa_platform/apitesting/scriptgen.py`; policy `qa_platform/automation/python_scripts.py` (`allowed`, `browser` parameters) |
| Service (applications, secrets, certificates, Test Connection, imports, tests, versions, suites, runs, results, drafts, scripts, audit), three agents, Scheduled Runs provider | `qa_platform/apitesting/service.py` |
| REST API `/api/apitest/...` | `qa_platform/api/apitesting.py`; view-only exceptions in `qa_platform/auth.py` (`VIEW_WRITES`) |
| Database (migration 21) | `qa_platform/storage/db.py` |
| Page | `web/js/pages/api-testing.js`, navigation in `web/js/app.js`, styles in `web/css/app.css` |
| Scheduled Runs providers and execution modes | `qa_platform/automation/scheduler.py`, `qa_platform/api/schedules.py`, `web/js/pages/schedules.js` |
| Integration Comparison schedule, agent card and review drafts | `qa_platform/comparison/schedule.py`, `qa_platform/api/workspace.py`, `web/js/pages/integration-workspace.js` |
| New agents and availability | `qa_platform/agents/registry.py`, `qa_platform/api/core.py` (`_availability`), `web/js/agent-state.js`, `agent-card.js`, `pages/workflow.js`, `robots.js`, CSS |
| AI Agent card | `qa_platform/opsagent/service.py` (`_CardReporter`) |
| Notifications | `qa_platform/events/names.py`, `qa_platform/notifications.py` |
| Defect Review: API source | `qa_platform/defects/service.py`, `analysis.py`, `web/js/pages/defect-review.js` |
| Python editor size and full screen | `web/js/python-editor.js`, `web/css/app.css` |
| Setup footer | `packaging/setup/SkyGuardSetup.nsi` (`BrandingText`) |
| Installer | `packaging/cumulative-patch/install_patch.py` (baselines, migration count 21, check *API Testing and new agents (23.12.0)*), `build_patch.py`, `packaging/setup/build_release.py` |

## Design choices

* **Reuse, not duplication.** Secrets use the existing `SecretStore`; drafts use `DefectService.record_draft` and the
  shared TFS publisher; scripts use the existing Python policy; cards use `BaseAgent.activity`; scheduling uses the
  existing scheduler through a small provider interface (`handles`, `info`, `items`, `run`).
* **No new third-party runtime dependency.** httpx, pytest and pydantic were already shipped. JSONPath and JSON Schema
  are small local implementations so that nothing is downloaded and only the subset that can be explained is
  supported. PyYAML is vendored as pure Python.
* **The Agent Workflow page was not redesigned.** The new agents are new cards in the same grid with their own robot
  icons; the only visual addition is the UNAVAILABLE state pill.
