# SkyGuard QA 23.5.0: changelog (from 23.4.0)

## New: Adaptive AI Application Learning & Operations Agent

* **Agent core `skyguard_agent`** (standard library only; shared with the browser extension's companion):
  * `store.py`: its own SQLite knowledge database (WAL, versioned migrations, schema 1).
  * `knowledge.py`: applications, accounts, screens, workflows, rules, relationships, roles and locators with states
    (discovered, AI suggested, awaiting approval, approved, rejected, superseded), versions, source and approver;
    correct, roll back, export (no credentials), import (always awaiting approval), backup and restore.
  * `learning.py`: discovery (read-only page visits), guided learning from a demonstration, workflow proposal.
  * `planner.py`: turns a request into records (dates, time series, numbered sequences, account per stage), using the
    local model when it is available and a deterministic parser otherwise.
  * `rules.py`: required, pattern, allowed values, date, time, range, unique, no overlap, depends.
  * `executor.py`: inspection of existing records, preview bound by hash, confirmation, step-by-step execution with
    verification of the application's own response, per-record result, pause, resume, stop, cancel remaining,
    interrupted-run recovery, account switching through logout, login and identity check.
  * `locator.py`: element fingerprints, label / role / form-section matching, self-healing with ambiguity detection.
  * `secrets_guard.py`: detects passwords, one-time codes and similar secrets in chat, voice transcripts, logs,
    audit details and learning events; redacts or refuses them.
  * `vault.py`: credential vault (Windows Credential Manager in SkyGuard, DPAPI in the companion), consent required,
    revoke, expiry.
  * `audit.py`: hash-chained audit trail with verification.
  * `guard.py`, `authz.py`, `sessions.py`: untrusted-content handling, role checks, per-account sessions.
  * `drivers/playwright_driver.py` and `js/agent_dom.js`: page observation, highlighting and human-like input through
    the browser's own editing commands (no value assignment by script).
* **SkyGuard integration**
  * `qa_platform/opsagent/service.py`: reuses SkyGuard's sign-in, AI gateway (Qwen), speech recognition, secret store
    and Browser Manager; runs Playwright jobs on their own threads; knowledge in `data\opsagent\`.
  * `qa_platform/api/opsagent.py`: `/api/ops/*` (info, config, applications, accounts, credentials, knowledge,
    locators, export, import, backup, restore, chat, voice, discovery, learning, runs, audit).
  * `qa_platform/auth.py`: `/api/ops/admin/*` and `/api/ops/config` are admin only; view-only roles cannot change
    anything (as for every SkyGuard page).
  * `web/js/pages/ops-agent.js` and `web/js/secret-check.js`: the AI Operations Agent page with Chat, Execution,
    Applications, Learn, Knowledge and Audit tabs; the chat refuses to send a message that looks like a secret.
  * `web/js/app.js`, `web/css/app.css`: menu group *AI Operations Agent*, chat and attention styles (respects reduced
    motion).
* **Version** 23.5.0 (`qa_platform/__init__.py`).

## Fixes made while testing (all in new code)

* Restoring a knowledge backup within the same second as the backup could overwrite the backup with the safety copy;
  backup names are now unique.
* Rollback removed `__pycache__` folders after it tried to delete the folders the patch added, so an empty
  `skyguard_agent\` folder was left; caches are now removed first.
* A pattern rule with an invalid regular expression is refused before approval.
* The secret check no longer treats words such as *code* followed by a trip number as a one-time code unless a
  separator or digit makes it one; *trip no* and *trip number* are recognised as the same field.
* Account names are matched longest first so *Entry Operator* is not read as *Operator*.

## Unchanged

* SkyGuard's main database: no migration (still 18). The agent's data is a separate file.
* No new Python package, no new download, no cloud service.
* Every existing page, API and installer behaviour of 23.4.0.
