# Release inclusion manifest: SGQA-23.3.35 → 23.11.1

This upgrade is cumulative. It contains every SkyGuard QA release made after the fixed baseline **SGQA-23.3.35**, and
the new release **23.11.1**. Every release below exists in the source history. Each is identified by its commit and its
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
| **23.11.1** (new) | this release | #55 | 2026-11-17 | AI Agent repair: intent routing, plans linked to their request, server-side confirmation, multi-step and record-page learning, verified retrieval, dynamic suggestions; extension and companion 1.3.0. |

PR #54 (23.9.0) was still open when 23.10.0 was built on top of it. 23.10.0 contains all of 23.9.0 (commit `33b3a50`).
23.11.0 is built on 23.10.0 in the same pull request (#55, open), so it contains all of 23.10.0 (commit `8c8dcc4`).
23.11.1 is built on 23.11.0 in the same pull request, so it contains all of 23.11.0 (commit `292743c`). See
SOURCE_ARCHITECTURE_AUDIT.md for why the repair requested for "23.10.0" is numbered 23.11.1.

## What 23.11.1 adds

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
