# Security report: SkyGuard QA 23.15.0

> **23.15.0** adds one thing that touches secrets: the sign-in password and the authorization code a person types
> **while teaching** are kept, so the agent does not have to ask again. This section says exactly how, and what is
> never kept. Everything else in 23.14.x is unchanged; for those controls see `previous-23.14.1/SECURITY_REPORT.md`
> and the other `previous-*` folders.

## 1. Secrets learned while teaching

| Question | Answer (enforced in code, tested) |
|---|---|
| What is kept? | A sign-in **password** (stored as the account's credential) and a lasting **authorization / approval code** (stored as an application secret with its field label). Nothing else. |
| What is never kept? | A **one-time code**: a field the browser marks as a one-time code (`autocomplete="one-time-code"`, an OTP / 2FA / verification / SMS label or name) is skipped in the page script, so its value never leaves the browser tab. |
| Where is it stored? | In the credential vault: Windows Credential Manager, or DPAPI-encrypted when the Credential Manager is unavailable. The knowledge database holds only a reference (`secret_ref`), never the value. |
| Does it reach the knowledge database, the chat or a log? | No. The value is removed from the captured events **before** anything is stored or interpreted (`capture_secrets` pops `secret_value` / `secret_kind` at ingest), so the learning pipeline, the recording, the chat transcript, the audit log and the local AI never see it. The test asserts the value's bytes are absent from the knowledge database file and from everything the page is shown. |
| What do the APIs show? | `********`, with the label, kind, number of uses and dates. `GET /v1/simple/secrets/{app_id}` lists them masked; there is no API that returns a value. |
| Who can read the value? | Only the executor, at the moment it signs in or fills that field, in memory. |
| Can a person remove one? | Yes: "Forget" on the Workflow Operator's secrets list (`DELETE`), audited. The test covers it. |
| Is it per application? | Yes: a code belongs to one application and one field label; a password belongs to one account of one application. |
| Capture switch | Capture happens only while a learning session is running and only when the setting `remember_secrets` is on (default on, per installation). A field the page marks as a password is always masked in the recording, as before. |

The earlier protection stays: a secret typed **in the chat** is still intercepted and refused ("secret_intercepted"),
never stored, and the person is sent to Applications & Accounts. The extension checks the text in the page before it
is sent, and the server checks it again.

## 2. The rest of 23.15.0

| Change | Security note |
|---|---|
| Create TFS Defect accepts severity / priority as numbers | Values are validated to 1 to 4 (anything else is refused with a message that names the field); the title, body and assignee keep their length limits; the body is escaped where it is rendered as HTML for the work item. |
| Evidence attached to a defect | Only files inside the run's own evidence folder are attached, at most 20, by the existing publisher; the preview no longer returns local file paths to the browser. |
| Values and prompts remembered | Stored in the agent's knowledge database. The `SECRET_FIELD` rule keeps a password / token / code field out of remembered values, so a remembered value can never be a secret. |
| Batch requests (10 records from one request) | Previews and the confirmation are unchanged: a consequential batch still shows every record and needs the tick box; the record limit and the per-run budget are unchanged. |
| Confirmation by word ("yes", "نعم") | Confirms only the latest preview of that conversation and only by its preview hash: a stale or altered preview cannot be confirmed by a word, and a word is never taken as a confirmation when the conversation is waiting for a retry answer. |
| Arabic fuzzy matching, hands-free listening | Deterministic text handling and microphone handling in the browser; no new network call, no new stored data. Audio still goes only to the local speech recognizer on this PC. Hands-free listening never runs an action by itself: a request that changes records waits for the preview confirmation. |
| Rename to Workflow Operator | Labels only; no change to permissions, routes or APIs. |

## 3. Tools run (25 November 2026)

| Check | Result |
|---|---|
| bandit 1.8.6 on every module changed in 23.15.0 (`evidence/bandit-231500.json`) | **No high finding.** 12 medium, all B608 on pre-existing statements built from fixed column names with `?` placeholders (values are always bound parameters; no user text reaches the SQL text) - the same set reported in 23.14.x, none of it new. Low: 13 × B110 (best-effort fallbacks that never hide a result) and 2 × B101 (asserts in non-security paths). Every SQL statement added in 23.15.0 (`remembered_values`, `remembered_prompts`, `app_secrets`) uses bound parameters only. |
| Secrets-in-storage test | `tests/test_ai_agent_memory_231500.py` asserts the password and the code are absent from the knowledge database file and from the understanding view, that the APIs return `********`, and that a one-time code is never captured (the rule is asserted in the page script itself). |
| Dependencies | No new package, no download, offline unchanged. |

## 4. Residual risks

* A lasting authorization code kept in the vault lets a run complete a step a person would otherwise type. Remove it
  ("Forget") if the code should be entered by a human every time; one-time codes always are.
* Hands-free listening keeps the microphone open for the length of one spoken request at a time while it is on.
  Anyone within earshot can speak a request; the preview and its confirmation remain the gate for anything that
  changes records. It is off by default and does not survive a browser restart unless it is turned on again.
* Remembering your usual values means a request that leaves a field unsaid is filled from the last value you used.
  The preview always names where each value came from, so an unintended value is visible before anything runs.
