# Release inclusion manifest: SGQA-23.3.35 → 23.14.1

This upgrade is cumulative. It contains every SkyGuard QA release made after the fixed baseline **SGQA-23.3.35**, and
the new release **23.14.1**. Every release below exists in the source history. Each is identified by its commit and its
`__version__`, and is tested as an installation baseline. No release number was invented. There is no 23.3.36 and no
later 23.3.x release: 23.4.0 directly follows 23.3.35.

| Release | Commit (version file) | Merged in pull request | Date | What it added |
|---|---|---|---|---|
| **23.3.35** (baseline) | `6d448fa` | #39 | 2026-11-08 | The fixed starting point of this upgrade. |
| 23.4.0 | `53e8009` | #41, #42 | 2026-11-12 | Healing confirmation, performance parameters, TFS field tests, offline installer, release documents. |
| 23.5.0 | `900e4e0` | #43 | 2026-11-13 | AI Application Learning & Operations Agent (`skyguard_agent`), the Chrome/Edge extension 1.0.0 and its local companion. |
| 23.6.0 | `c229f6b` | #44 | 2026-11-13 | Adaptive component playback and four bug fixes. |
| 23.6.1 | `e344315` | #45 | 2026-11-14 | Guided playback of suggestion lists, calendars and counters. |
| 23.7.0 | `fbefa64` | #45 | 2026-11-14 | Learning while the person browses (journal, understanding), extension 1.1.0, an opentelemetry-api CVE pin. |
| 23.7.1 | `a15c01f` | #47 to #50 | 2026-11-15 | Advanced locator and departure-date fixes for guided playback. |
| 23.7.2 | `51a9a29` | #51, #52 | 2026-11-15 | AI-driven automation writes every Test Case step. |
| 23.8.0 | `c838bf8` | #53 | 2026-11-16 | One Integration Comparison workspace. |
| 23.9.0 | `33b3a50` | #54 | 2026-11-17 | Offline OCR runtime, live capture in layers, filters, meaning-aware mapping, six result statuses, one maximized-window policy. This is the version installed on your PC this morning (23.8.0 → 23.9.0). |
| 23.10.0 | `8c8dcc4` | #55 | 2026-11-17 | Simple AI Agent, guided recording for the Integration Comparison, extension 1.2.0, the Setup wizard. The extension companion installer fix (`55f73be`) followed on the same day. |
| 23.11.0 | `292743c` (version file `6a9b149`) | #55 | 2026-11-17 | Editable Python tests, Smart Wait, one Defect Review with approval before TFS, the Integration Comparison → TFS fix. |
| 23.11.1 | `8e8df0d` (version file `05a266a`) | #55 | 2026-11-17 | AI Agent repair: intent routing, plans linked to their request, server-side confirmation, multi-step and record-page learning, verified retrieval, dynamic suggestions; extension and companion 1.3.0. |
| 23.11.2 | `54b69f0` (version file `62da1ee`) | #56 | 2026-11-17 | Missing-information follow-up: answers read field by field and kept, only the rest asked; postal codes not taken for secrets; sign-in confirmed by applications that never show the user; extension and companion 1.3.1. |
| 23.12.0 | `fd5a117` | #58 | 2026-11-19 | API Testing module (offline OpenAPI / Swagger, authorized targets, generated tests, chains, every authentication type, pytest + httpx scripts, failure classification, Defect Review drafts); API tests and Integration Comparisons in Scheduled Runs with Visible / Headless / Smart Auto modes; six Agent Workflow agents with real availability; Python editor full screen and adjustable height; Setup window footer shows only the product and version. |
| 23.13.0 | `e0f9ac4` | #59 | 2026-11-20 | Edge as the default when preferred (with fallback when a browser is not installed); dashboard charts first with drill-down; Arabic / UAE Arabic voice and the male-voice fix; TFS "Assign to" list; Guided Browser tabs; API Testing Open and Windows-trusted TLS; HTTPS with a friendly name; AI Agent fixes (lifecycle filter, public sites, dynamic URLs, one browser, Arabic); personal TFS PAT and project per user with a welcome email and user manual; report buttons on 15 testing pages; Analytics AI; healing that is kept; extension and companion 1.4.1. |
| 23.13.1 | `0a7ed57` | #60 | 2026-11-21 | Offline Arabic voice replies, male or female (Piper Kareem; Windows Hoda / Naayf incl. OneCore voices); Arabic reply voice setting; GET_ARABIC_VOICE.ps1 / INSTALL_ARABIC_VOICE.cmd; INSTALL_SPEECH_MODEL.cmd; Arabic wake name in any spelling and misheard command words; small.ar used for Arabic. |
| 23.13.2 | `89a6429` | #61 | 2026-11-21 | Spoken AI Agent replies (English or Arabic) on the page and in the extension 1.4.2; the Arabic voice shared with the extension's companion. |
| 23.13.3 | `9b42d1b` | release branch (`claude/project-thread-slw9kh`) | 2026-11-21 | Literal Arabic to English ("open page"), clean "which page" question with page buttons; asked and told in the language spoken (task steps too); websites with changing addresses; extension 1.4.3. |
| 23.14.0 | `18d9fde` | #62 | 2026-11-22 | Security Testing (8 screens: OWASP WSTG / Top 10 / API Top 10 checks, read-only discovery, offline OWASP ZAP integration, bounded fuzzing, findings, regression, reports); Red Hat Fuse Integration Testing (Karaf / Spring Boot / EAP through Jolokia or actuator, black box; read-only; A → Fuse → ActiveMQ → B / C with correlation IDs); ActiveMQ Classic and Artemis testing (separate adapters, test destinations only); unified result actions (Analyze with AI, Create TFS Defect, Generate PDF Report, Email Developer, View Evidence, Retest); intranet SMTP settings and send history; dashboard figures; 19 new agents in Agent Workflow with five selectable looks and Stop / Pause / Resume; voice commands and voice tests; migration 23; the installed Arabic voice detected and kept. |
| **23.14.1** (new) | this release | (this pull request) | 2026-11-22 | TFS fast again with personal PATs and projects: one reused connection pool per server / project / PAT (no new TLS context and handshake per call), the person's PAT and project read once instead of on every call, concurrent requests for the same TFS figure share one call, a 10 s connect timeout; the Dashboard shows everything at once and waits at most 8 s for TFS; a quicker Connection Test (same six checks); background work runs with the signed-in person's PAT and project; 100 request threads; the TFS status light per person. No migration, no new dependency. |

PR #54 (23.9.0) was still open when 23.10.0 was built on top of it. 23.10.0 contains all of 23.9.0 (commit `33b3a50`).
23.11.0 is built on 23.10.0 in the same pull request (#55, open), so it contains all of 23.10.0 (commit `8c8dcc4`).
23.11.1 is built on 23.11.0 in the same pull request, so it contains all of 23.11.0 (commit `292743c`). See
SOURCE_ARCHITECTURE_AUDIT.md for why the repair requested for "23.10.0" is numbered 23.11.1.
23.11.2 is built on 23.11.1 in the same pull request, so it contains all of 23.11.1 (commit `8e8df0d`).
23.12.0 is built on 23.11.2 (branch of PR #56, commit `54b69f0`), so it contains all of 23.11.2.

23.13.0 is built on 23.12.0 (merge commit `fd5a117`, PR #58), so it contains all of 23.12.0.
23.13.1 is built on 23.13.0 (merge commit `e0f9ac4`, PR #59), so it contains all of 23.13.0.
23.13.2 is built on 23.13.1 (merge commit `0a7ed57`, PR #60), so it contains all of 23.13.1.
23.13.3 is built on 23.13.2 (commit `89a6429`, PR #61, same branch), so it contains all of 23.13.2. What it adds: PATCH_CHANGELOG.md.

## What 23.13.0 adds

See PATCH_CHANGELOG.md for the full list, numbered by the 13 items of the request.

### Database changes

Migration **22**: `ALTER TABLE defect_drafts ADD COLUMN assigned_to TEXT`. The `users` table gains `email` and
`tfs_project` (added by the user store when missing). Only columns are added; earlier versions ignore them, so a
rollback keeps the database as it is.

### Installer

`install_patch.py` 23.13.0 accepts 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0,
23.10.0, 23.11.0, 23.11.1, 23.11.2 and 23.12.0, and 23.13.0 again to repair. Verification adds one check:
**Edge, healing, AI Agent, HTTPS (23.13.0)**. The patch carries two new files for the application folder:
`ENABLE_HTTPS.cmd` and `TRUST_SKYGUARD_QA.cmd`, and the user manual `web/manual/SkyGuard_QA_User_Manual.pdf`.

### Browser extension

Extension and companion **1.4.1**: the shared agent core of 23.13.0 (public sites, dynamic URLs, one browser) and
Arabic commands translated to English (*Understood as:*). No new permission.

## Earlier: what 23.12.0 added

See previous-23.12.0/PATCH_CHANGELOG.md for the user-facing description, previous-23.12.0/ROOT_CAUSE_ANALYSIS.md for why each part was needed, and
SOURCE_ARCHITECTURE_AUDIT.md for where each part is.

### Database changes

Migration 21 (the main database goes from 20 to 21 migrations) adds eight tables: `api_apps`, `api_tests`,
`api_test_versions`, `api_suites`, `api_runs`, `api_results`, `api_scripts` and `api_audit`. Nothing is removed or
rewritten; earlier versions ignore the new tables, so a rollback keeps the database as it is. The agent's knowledge
database stays at schema 2.

### Installer

`install_patch.py` 23.12.0 accepts:

> 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1, 23.11.2

It also accepts 23.12.0 again, to repair. Verification adds one check: **API Testing and new agents (23.12.0)**. It
imports an OpenAPI document offline, confirms that SkyGuard's own port on this PC is refused as a test target, that a
generated script passes the script policy, and that every agent is registered exactly once. The Setup wizard's footer
now shows only "SkyGuard QA 23.12.0" (the words "offline, no administrator rights" were removed, as asked). The
wizard pages, the backup, the automatic restore and the manual install are unchanged.

### Browser extension

The extension zip carries extension and companion **1.3.1** (unchanged in 23.12.0: API Testing does not use the
extension). `UPDATE_EXTENSION.cmd` updates an installed 1.0.0, 1.1.0, 1.2.0 or 1.3.0 in place.

## Earlier: what 23.11.2 added


See PATCH_CHANGELOG.md and ROOT_CAUSE_ANALYSIS.md.

### Database changes

None. The main database stays at 20 migrations, and the agent's knowledge database stays at schema 2. The answers
kept with a pending question are stored in the existing `conversation_state` table.

### Installer

`install_patch.py` 23.11.2 accepts:

> 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1

It also accepts 23.11.2 again, to repair. Verification adds one check: **Missing information follow-up (23.11.2)**.
It confirms that "First Name; muhammad" is read as First Name, that "pre pare it" gives no value, that a postal code
is not a secret while "the code is 482913" is, and that the session records a confirmed sign-in. The Setup wizard,
the backup, the automatic restore and the manual install are unchanged.

### Browser extension

The extension zip carries extension and companion **1.3.1**. `UPDATE_EXTENSION.cmd` updates an installed 1.0.0,
1.1.0, 1.2.0 or 1.3.0 in place.

## Earlier: what 23.11.1 added

See PATCH_CHANGELOG.md for the user-facing description, ROOT_CAUSE_ANALYSIS.md for the causes, and
SOURCE_ARCHITECTURE_AUDIT.md for where each part is.

### Database changes

None. The main database stays at 20 migrations, and the agent's knowledge database stays at schema 2.

### Installer

`install_patch.py` 23.11.1 accepts:

> 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0

It also accepts 23.11.1 again, to repair. Verification adds one check: **AI Agent repair (23.11.1)**. It confirms that
the intent router, the shared confirmation and the retrieval check are installed, and that a navigation request is
classified as navigation. The Setup wizard, the backup, the automatic restore and the manual install are unchanged.

### Browser extension

The extension zip carries extension and companion **1.3.0**. `UPDATE_EXTENSION.cmd` updates an installed 1.0.0, 1.1.0
or 1.2.0 in place.

## Earlier: what 23.11.0 added


See PATCH_CHANGELOG.md for the user-facing description. Where each part is:

| Part | Where |
|---|---|
| Editable Python test: versions, validation (syntax with line numbers, import and call policy), format, run, revert, regenerate with a warning, download of the saved version, roles | `qa_platform/automation/python_scripts.py`, `qa_platform/api/automation.py` (`/scripts/{id}/python…`), `web/js/python-editor.js`, `qa_platform/automation/service.py` (`run_script`, `run_data_rows` route to the saved Python) |
| Smart Wait service, used by recorder, runner and exported Python | `qa_platform/automation/smart_wait.py`, `recorder.py` (`_watch_loading`), `runner.py`, `pytest_export.py` |
| Wait settings, Insert waits, + Wait (8 types) | `PUT /scripts/{id}/wait-policy`, `POST /scripts/{id}/waits`, `web/js/smart-wait-ui.js`, `web/js/pages/automation.js` |
| Drafts for every AI-Driven and Guided failure, with full evidence and no duplicates | `qa_platform/automation/framework/studio.py` (`_on_diagnosis`), `qa_platform/defects/service.py` (`_on_event`, `_store`), `qa_platform/defects/analysis.py`, `runner.py` (trace, busy page) |
| Classification into 6 classes with confidence and evidence; optional local Qwen refinement | `qa_platform/defects/triage.py` |
| One Defect Review with 8 statuses, approval, roles, history | `qa_platform/defects/service.py`, `qa_platform/api/defects.py`, `web/js/pages/defect-review.js` |
| Shared TFS publishing: validation, correlation ID, reconciliation, read-back, retry | `DefectService.publish`, `_find_by_correlation`, `_read_back`, `retry` |
| Integration Comparison → TFS through the shared publisher; evidence; confirmed mismatches only | `qa_platform/comparison/service.py` (`review_draft`, `create_in_tfs`), `qa_platform/api/comparison.py`, `web/js/pages/integration-workspace.js` |

### Database changes

Migration 20 (the main database goes from 19 to 20 migrations):

* `defect_drafts` gets new columns: classification, confidence, analysis, approved_by, approved_at, correlation_id,
  tfs_url, published_at, publish_attempts, source_ref;
* new tables: `defect_events` (history), `script_python` (versions), `script_python_state`.

Nothing is removed or rewritten, and earlier versions ignore the new tables and columns. So a rollback keeps the
database as it is, and `--restore-database` remains available.

### Installer

`install_patch.py` 23.11.0 accepts:

> 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0

It also accepts 23.11.0, to repair. Verification adds two checks: **Python editor, Smart Wait, Defect Review
(23.11.0)** and **Review and Python tables**. The Setup wizard, the backup, the automatic restore and the manual
install are unchanged.
