# Release inclusion manifest: SGQA-23.3.35 → 23.10.0

This upgrade is cumulative. It contains every SkyGuard QA release made after the fixed baseline **SGQA-23.3.35**, and
the new release **23.10.0**. Every release below exists in the source history. Each is identified by its commit and its
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
| **23.10.0** (new) | this release | this pull request | 2026-11-17 | Simple AI Agent, guided recording for the Integration Comparison, extension 1.2.0, the Setup wizard. |

PR #54 (23.9.0) was still open when 23.10.0 was built on top of it. 23.10.0 contains all of 23.9.0 (commit `33b3a50`).

## What 23.10.0 adds

### 1. A simple AI Agent (replaces the complicated agent page for everyday use)

CONNECT → LEARN → USE NORMALLY → TEXT / VOICE COMMAND → AI OPERATES.

| Part | Where |
|---|---|
| Connect by address: the name is suggested from the address, and the same site is reused. You choose Edge or Chrome, and the window opens maximized. | `skyguard_agent/autopilot.py` (`connect`, `suggest_name`), `qa_platform/opsagent/service.py` (`connect_and_learn`, browser choice), `POST /api/ops/simple/connect` |
| It learns while you browse. There are no workflow names to type and no approval for each observation. A task seen to succeed is trusted automatically: **verified** when seen at least twice, **uncertain** when seen once. AI guesses stay suggestions. | `Autopilot.trust` (called after each digest), `skyguard_agent/agent.py` (`after_digest`) |
| Read-only discovery | `POST /api/ops/simple/explore` (the existing discovery, which never submits) |
| Business rules learned from what the application refused or accepted | the existing rule miner, trusted with the rest (`trust`) |
| Plain-language and voice commands, for example "Create two trips for tomorrow for driver John", "Show pending trips", "Approve the selected trips" or "Authorize all approved trips". Dates such as "tomorrow" or "next Monday" are resolved, the next free key is filled, and anything missing is asked for once. | `Autopilot.command`, `resolve_date_words`, `match_by_verb`, `_prepare`, `_prepare_row_action`, `POST /api/ops/simple/command`, `POST /api/ops/simple/voice` |
| One consolidated preview. An operation that changes the state of records (approve, authorize, reject, cancel, delete and similar) also needs a ticked confirmation box. The server refuses the confirmation without it (`ACKNOWLEDGE_REQUIRED`). | `run_is_consequential`, `POST /api/ops/simple/runs/{id}/confirm` |
| Bulk runs with checkpoints, several roles (for example a manager approves and a second manager authorizes), and per-record results (completed, failed, uncertain, skipped). Sign-in, MFA and CAPTCHA are never bypassed. | the existing engine, `Autopilot.tasks` |
| Four tabs: **AI Chat & Voice**, **Connect & Learn**, **Applications & Knowledge**, **Tasks & History** | `web/js/pages/ai-agent.js` |
| Advanced Administrator Settings: the previous agent page, for administrators only | `web/js/app.js` (hidden route `ops-agent`, `isAdmin()`), `PUT /api/ops/config` with `auto_trust` (admin only) |

### 2. Integration Comparison: guided recording

| Part | Where |
|---|---|
| Start Guided Recording opens the application in a maximized browser, in the same live session the filters use. | `qa_platform/comparison/guided_nav.py` (`Recorder`), `Workspace.guided_start` |
| Stop & Save turns what the tester did into steps, the filters (kept apart from their values), whether pagination was used, and the detail page as a parameterized address (`/orders/{key}`). Sign-in, pagination, sorting and change-like clicks are left out, and the reason for each is shown. | `skyguard_agent/navrec.py` (`events_to_recording`), `Workspace.guided_stop` |
| Edit Recorded Steps and change filter values without recording again | `navrec.parse_steps_text` (unchanged lines keep their selectors), `Workspace.guided_update`, `PUT …/guided` |
| Replay & Verify reads every page (Next, numbers, Load more, scrolling, with loop and duplicate protection) and the detail of every record (master-detail), each detail line with its parent key. | `Workspace.guided_replay`, `ComparisonService._capture_details` |
| Re-record, View Captured Tables | the Connect dialog in `web/js/pages/integration-workspace.js` |
| Import from browser extension: a recording made in Edge or Chrome with the extension. It is checked again and kept to the application's own site. | `navrec.sanitize_recording`, `Workspace.guided_import`, `POST …/guided/import` |
| Read-only: a step that looks like a change is refused when recorded, edited or imported. | `navrec.CHANGE`, `WS_STEP_CHANGES_DATA` |

The existing comparison features are unchanged: typed steps (now under *Advanced (manual navigation)*), filters,
OCR, mapping, results and reports.

### 3. Shared browser automation core

* `skyguard_agent/js/agent_dom.js` is the one page reader and observer used by SkyGuard (learning, the agent, guided
  recording) and by the extension. The two copies are byte-identical, which a test and the build both check. Table
  context now includes the row's cells, the headers and the column.
* `skyguard_agent/navrec.py` is the one recording converter, used by SkyGuard and the extension's companion.
* `qa_platform/automation/browser.py` is the one window policy (23.9.0), used by every visible browser, including
  guided recording.

### 4. Edge/Chrome extension and companion 1.2.0

The chat uses the simple agent. The preview has the confirmation box for consequential runs, and the companion
enforces it. A missing password opens the secure credential page. The Learn tab has *Connect an application* and
*Record for Integration Comparison*. New companion routes: `/v1/simple/connect`, `/v1/simple/knowledge/{app}`,
`/v1/simple/trust/{app}`, `/v1/simple/tasks` and `/v1/comparison/recording`. No new browser permissions were added.

### 5. Installer

* `install_patch.py` accepts the ten releases above (and 23.10.0 again, as a repair) and refuses every other version
  before changing anything. It adds `detect` (read only, used by Setup) and `--log`.
* **Setup.exe** is a wizard (Welcome → Detect → Backup → Install → Verify → Finish), runs per user with no
  administrator rights, works offline, and has an unattended mode (`/S /APP=…`).
* `APPLY_PATCH.cmd` remains as the manual alternative (in `Manual_Install`).

### Database changes

* Main database: none. It stays at 19 migrations. Guided recordings are kept in the comparison application's
  settings (`ws_guided`).
* Agent knowledge database: a new table, `conversation_state` (`CREATE TABLE IF NOT EXISTS`), which holds the pending
  question and the current selection of a chat. Earlier versions ignore it, so a rollback needs no database restore.

## Files

`PATCH_MANIFEST.json` lists every application file (changed or added since 23.3.35) with its SHA-256. The files that
changed between 23.9.0 and 23.10.0:

```
qa_platform/__init__.py                 version 23.10.0
qa_platform/api/agent_simple.py         new: /api/ops/simple
qa_platform/api/opsagent.py             auto_trust setting
qa_platform/api/workspace.py            guided recording endpoints and import
qa_platform/comparison/guided_nav.py    new: the live recorder
qa_platform/comparison/service.py       master-detail capture
qa_platform/comparison/workspace.py     guided start/status/stop/update/replay/import
qa_platform/main.py                     router
qa_platform/opsagent/service.py         simple agent, browser choice, connect and learn
skyguard_agent/__init__.py              agent core 1.2.0
skyguard_agent/agent.py                 chosen task, after-digest hook
skyguard_agent/autopilot.py             new: the simple agent
skyguard_agent/navrec.py                new: recording conversion and checks
skyguard_agent/js/agent_dom.js          table cells, headers and column
web/js/app.js                           AI Agent menu, admin-only advanced settings
web/js/pages/ai-agent.js                new: the four-tab page
web/js/pages/integration-workspace.js   the guided recording panel and import
```
