# Security report: SkyGuard QA 23.11.0

Scope: everything 23.11.0 adds or changes since 23.10.0 (61 files, RELEASE_INCLUSION_MANIFEST.md), and the cumulative
patch as shipped. Earlier releases' reviews are under `previous-*`. The extension is the same 1.2.0 as in 23.10.0,
with only its installer script fixed. That fix is a folder-creation order and adds no permission.

## 1. Results of the tools that were run (17 November 2026)

| Check | Tool | Result |
|---|---|---|
| Known vulnerabilities in the packages bundled in the offline application | `pip-audit 2.10.1 --strict --no-deps --disable-pip -r packaging/requirements-offline-win.txt` (the CI job's command) | **No known vulnerabilities** |
| Same, Faster-Whisper packages | `… -r packaging/requirements-faster-whisper-win.txt` | **No known vulnerabilities** |
| Same, the 21 OCR runtime wheels in the patch | `pip-audit --no-deps` over the exact wheel versions | **No known vulnerabilities** |
| Python static analysis of every changed Python file | `bandit 1.9.4 -ll` (26 files). Only findings on lines changed in 23.11.0 are listed below; the rest are unchanged since earlier reviews. | 5 findings on changed lines. 1 fixed, 4 reviewed: **no real issue** |
| Secrets in every changed file | Regular-expression scan for private keys, cloud and GitHub tokens, JWTs, API keys and password assignments | **No secrets.** Only made-up test passwords of the local test sites (`tests/…`). |
| No new package dependency | `requirements*.txt` unchanged | 23.11.0 adds no package |

Bandit, the 5 findings on changed lines:

* **B324**, SHA-1 in `smart_wait.py:89`. It fingerprints a table's state so the wait can tell that the table changed.
  It is not used for security, and is now marked `usedforsecurity=False` (fixed).
* **B608**, SQL built from strings, in `defects/service.py:221` and `:719`. These are the `?` placeholders for a fixed
  status tuple, and a `WHERE` built from fixed clauses with `?` parameters. No user input reaches the SQL text.
* **B608** at `defects/service.py:470`. This one is a false positive: it is a WIQL query to TFS, not SQL. The tag is
  built from a correlation ID SkyGuard generates (`uuid4().hex[:12]`) and is quoted with the existing `wiql_quote`.
* **B314**, `xml.etree` parse in `python_scripts.py:378`. It reads the JUnit file that the local pytest run just
  wrote into the run's own evidence folder. That file is not received from outside. A test author who can write a
  hostile XML file already runs code as the same Windows user (section 3).

## 2. New endpoints and who may call them

Every request goes through two rules: a view-only role (developer, manager) may only read, and `ADMIN_ONLY` paths are
for administrators. On top of that, the new services check roles themselves, so the rule also holds for calls that do
not come through HTTP (scheduler, voice).

| Endpoint | Access | Checks |
|---|---|---|
| `POST /api/automation/scripts/{id}/python/validate`, `/format` | tester / admin | No state change. Source at most 400 kB. |
| `PUT /api/automation/scripts/{id}/python`, `POST …/python/{revert, regenerate, restore, run}` | `automation.python_roles` (default admin, tester) | `PYTHON_NOT_ALLOWED` (403) otherwise. Save refuses invalid code (`PYTHON_INVALID`, with line numbers). Regenerate over edits needs `confirm` (`PYTHON_EDITED`, 409). Every save is a version with its author. |
| `GET …/python/versions/{v}` | every signed-in role | Read only |
| `PUT /api/automation/scripts/{id}/wait-policy`, `POST …/waits` | tester / admin | Every wait is validated (`clean_spec`): known type, timeout 0.5 to 300 s, selector or URL where needed, bounded lengths. |
| `POST /api/defects/drafts/{id}/approve` | `defects.approver_roles` (default admin, tester) | `DEFECT_NOT_ALLOWED` (403) otherwise. Recorded in the history. |
| `POST /api/defects/drafts/{id}/publish`, `/retry`, `/create` | `defects.publisher_roles` (default admin, tester) | Requires approved status. Runs the mandatory-field validation, the attachment checks and the duplicate check before anything is sent. |
| `POST /api/defects/drafts/{id}/reject`, `/ai-analyze`, `PATCH …` | tester / admin | An edit after approval removes the approval. |
| `GET /api/defects/drafts/{id}/events` | every signed-in role | Read only |
| `GET /api/defects/drafts/{id}/evidence/{index}` | every signed-in role | Serves **only** the files listed in that draft's evidence, by position. It never takes a path from the request, so there is no path traversal. |
| `POST /api/comparison/defects/{did}/review` | tester / admin | Creates or updates a draft. Nothing is sent to TFS. |

These are covered by tests (`test_python_editor_231100.py`, `test_defects.py`, `test_comparison_tfs_231100.py`,
`test_smart_wait_231100.py`, `test_ui_231100.py`), including the refusals: wrong role, unapproved send, invalid code,
and regenerate over edits.

## 3. Editable Python: what the policy is and what it is not

Saved Python runs as a `pytest` subprocess:

* with the application's own Python,
* in the run's evidence folder,
* with the same Windows rights as the user running SkyGuard,
* with a time limit (`automation.python_timeout_seconds`, default 900).

Before saving and before running, a static check refuses:

* imports outside an allowlist (for example `subprocess`, `socket`, `ctypes`, `shutil`, `importlib`);
* `eval`, `exec`, `compile`, `__import__`;
* `os.system`, `os.exec*`, `os.spawn*`, `os.kill`, `os.startfile`;
* deleting or renaming files;
* `__dunder__` attribute access;
* `sys.modules`.

**This check prevents mistakes and obvious misuse. It is not a sandbox.** Python cannot be fully confined by static
checks; for example, the allowed `open()` can write files. That is why saving and running are limited to the Admin and
Tester roles, every version records who saved it, and a reviewer can see each version in Version History. Treat
edit rights like the right to write test code.

Credentials:

* are never written into the file;
* are passed in environment variables for that run only;
* are masked in the stored output;
* when one is typed, no Playwright trace is kept, because a trace would contain it.

Environment variables of the SkyGuard process whose names end in `_PASSWORD`, `_TOKEN` or `_SECRET` are not passed to
the test.

## 4. Rules from the request and how they hold

| Rule | How |
|---|---|
| Never publish to TFS without approval | `publish` requires the `approved` status. Automatic mode only drafts. An edit after approval resets it. Tested in `test_defects.py` (automatic mode never publishes) and `test_ui_231100.py` (approval alone sends nothing; sending without a configured TFS is refused and no ID is shown). |
| Claim success only after server confirmation | Published only after the work item is read back. A lost response is reconciled by correlation-ID tag before any retry (`test_comparison_tfs_231100.py` uses a stand-in TFS that creates the item and then drops the connection; exactly one bug results). |
| No passwords, OTPs, MFA secrets, cookies or codes in prompts, memory, scripts, screenshots or logs | Draft facts are redacted (`triage.redact`) before storage and before the local AI sees them. Python output is masked. A trace is dropped when a secret was typed. Exported and edited Python reads secrets from the environment. |
| Qwen produces structured output through validated tools | The only new AI use is classification. The answer is a fixed schema (`FailureView`); an unknown class is discarded; nothing is executed from it, and a suggested change is applied only by the person. Page text reaches the model only as data, and the prompt says so. |
| No bypass of MFA, CAPTCHA or permissions; no machine-wide changes; offline | Unchanged from 23.10.0. Smart Wait only listens to Playwright events and reads the page. It does not patch Playwright or the page's code. The installer still imports no network module and needs no elevation (tested). |

## 5. Open points

* **Setup.exe is not code-signed** (no company certificate was available), as in 23.10.0. SHA-256 values are in
  `SHA256SUMS.txt` and in the `.sha256` files.
* `pip-audit` reflects the advisory database on 17 November 2026.
