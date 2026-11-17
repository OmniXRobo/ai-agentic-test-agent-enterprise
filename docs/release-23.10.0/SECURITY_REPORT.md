# Security report: SkyGuard QA 23.10.0 and extension 1.2.0

Scope: everything 23.10.0 adds or changes (RELEASE_INCLUSION_MANIFEST.md), the cumulative patch as shipped, the Setup
wizard, and the browser extension and companion 1.2.0. The earlier releases' reviews are in `previous-*`.

## 1. Results of the tools that were run

| Check | Tool | Result |
|---|---|---|
| Known vulnerabilities in the packages bundled in the offline application | `pip-audit 2.10.1 --strict --no-deps -r packaging/requirements-offline-win.txt` (PyPI advisory database / OSV), the same command as the CI job | **No known vulnerabilities** |
| Same, Faster-Whisper packages | `pip-audit … -r packaging/requirements-faster-whisper-win.txt` | **No known vulnerabilities** |
| Same, the 21 OCR runtime wheels in this patch (rapidocr 3.10.0, onnxruntime 1.32.0, opencv-python-headless 5.0.0.93, numpy 2.5.5, pillow 13.0.0 and their dependencies) | `pip-audit --no-deps` over the exact wheel versions | **No known vulnerabilities** |
| Python static analysis of every changed file and of the installer | `bandit 1.9.4 -ll` | 4 high (SHA-1), 4 medium (SQL strings). All 8 reviewed. **No real issue**, see below. |
| Secrets in the source (every file changed since 23.3.35) and in the built patch | Regular-expression scan for private keys, cloud and GitHub tokens, JWTs, API keys and password assignments (794 files) | **No secrets.** The only matches are made-up test passwords of the local stand-in sites (`tests/…`) and the UI label "Password". |
| No remote code in the extension | `tests/test_extension_static.py` (no `eval`, no `new Function`, no remote script or URL) | Passed |

Bandit details:

* B324 (SHA-1) in `comparison/service.py:294,1038` and `comparison/workspace.py:2139,2140`: these build record
  identifiers and change signatures. They are not used for security. Marked `usedforsecurity=False`; the identifiers
  did not change. Five older non-security SHA-1 uses elsewhere (`tfs`, `defects`, `reconcile`, `extract_web`) are
  unchanged.
* B608 (SQL built from strings) in `comparison/service.py:332,363,364` and `install_patch.py:162`: these are fixed
  column lists, a fixed `WHERE` fragment with `?` parameters, and table names read from `sqlite_master` and quoted.
  No user input reaches the SQL text.

## 2. New endpoints and who may call them

SkyGuard enforces two rules on every request: an account with a view-only role (developer, manager) may only read,
and the `ADMIN_ONLY` paths are for administrators.

| Endpoint | Access | Checks |
|---|---|---|
| `POST /api/ops/simple/connect` | tester / admin | Address: http(s) only; no credentials in the URL; `file:`, `javascript:` and `data:` refused (`URL_INVALID`). |
| `POST /api/ops/simple/learn/{sid}/stop`, `/explore` | tester / admin | Explore is the existing read-only discovery. |
| `POST /api/ops/simple/command`, `/voice` | tester / admin | Secrets in the text are intercepted before storage or the AI model (`secret_intercepted`). Voice goes through the same path after local transcription. |
| `GET /api/ops/simple/status`, `/knowledge/{app}`, `/tasks` | every signed-in role | Never return passwords; accounts show only *has credential*. |
| `POST /api/ops/simple/knowledge/{id}/forget`, `/trust` | tester / admin | Audited. |
| `POST /api/ops/simple/runs/{id}/confirm` | tester / admin | Needs the preview hash. A consequential run also needs `acknowledge: true` (`ACKNOWLEDGE_REQUIRED`). |
| `PUT /api/ops/config` (`auto_trust`) | admin | Already in `ADMIN_ONLY`. |
| `POST /api/comparison/workspace/applications/{id}/guided/start`, `stop`, `replay`; `PUT …/guided`; `GET …/guided` | tester / admin (GET: all) | Edited steps that look like a change are refused (`WS_STEP_CHANGES_DATA`). |
| `POST …/guided/import` | tester / admin | `sanitize_recording`: only the step actions the extractor runs; no change-like steps; no `[secret]`; a detail address with exactly one `{key}`; bounded sizes; every address must be the application's own site (`WS_RECORDING_OTHER_SITE`), so an imported file cannot send the extractor elsewhere. |
| Companion `POST /v1/simple/connect`, `/v1/simple/trust/{app}`, `/v1/comparison/recording` | companion operator / admin | Pairing, sign-in token, origin and host checks, rate and size limits, as for every companion route. Viewers get 403. |
| Companion `GET /v1/simple/knowledge/{app}`, `/v1/simple/tasks` | any signed-in companion user | Read only. |
| Companion `POST /v1/runs/{id}/confirm` | operator / admin | Now also needs `acknowledge` for consequential runs. |

All of these are covered by tests (`test_agent_simple_api_231000.py`, `test_extension_120.py`,
`test_guided_comparison_231000.py`), including the refusals.

## 3. Rules from the request and how they hold

| Rule | How |
|---|---|
| No passwords, OTPs, MFA secrets, session cookies or authentication codes in prompts, learning memory, scripts, screenshots or logs | The page observer reports password and OTP fields as `[secret]`; `navrec` and the learner drop them. Typed secrets are intercepted (SkyGuard and companion). Credentials go only to Windows Credential Manager (DPAPI) through the consent form. Tests check that a password typed in the chat or entered while recording appears nowhere: not in the history, the recording, the database files or the logs. |
| No bypass of MFA, CAPTCHA or application permissions | A sign-in page with MFA or CAPTCHA pauses the run for the person. A recording keeps no sign-in steps. The application's own refusal (for example *already assigned*) is reported as failed, never retried another way. |
| Qwen produces structured plans executed through validated tools | Unchanged engine. Qwen only proposes; the engine validates against learned workflows and rules, shows the preview, and executes only after the confirmation bound to the preview hash. The simple agent rewrites plain language deterministically and adds no execution path. No shell, Python or JavaScript is executed from model output. |
| The comparison stays read-only | Change-like clicks are dropped when recorded, refused when edited or imported, and the extractor's request policy blocks changing requests (23.8.0). Tested: the stand-in report's *Delete* button is never pressed (`DELETES == []`). |
| No protected folders, machine registry, system PATH or machine-wide services | Setup runs per user (`RequestExecutionLevel user`). The installer writes only inside the installation, its data folder and `%LOCALAPPDATA%`. A protected application folder gets a side-by-side copy. A test scans the installer, wrappers and Setup script for elevation, machine registry, service and protected-folder use. |
| No bypass of Windows security or enterprise extension restrictions | Nothing is elevated and nothing is disabled. SmartScreen is not suppressed; the guide says what to expect, and the manual install is the alternative where unsigned programs are blocked. The extension adds no permission, and host access is asked per site. |
| Offline | The installer imports no network module (tested). Setup contains everything. Nothing is downloaded at install time. |

## 4. Prompt injection

Page text that the agent reads (for example record lists) can reach the local Qwen model only as data inside the
existing structured prompts. A page cannot add a workflow, a step, a target or a confirmation: these come only from
learned and trusted knowledge, and execution always needs the person's confirmation of the preview. An imported
navigation recording is data checked by `sanitize_recording`; it is never sent to the model.

## 5. Open points

* **Setup.exe is not code-signed.** No company code-signing certificate was available to the build. Its SHA-256 is in
  `SHA256SUMS.txt`. Sign it with the company certificate before wide distribution if your policy requires it.
* The extension is loaded unpacked, as in 1.0 and 1.1. Enterprise distribution through policy is described in
  `docs/ENTERPRISE_DEPLOYMENT.md` of the extension package.
* `pip-audit` reflects the advisory database on 17 November 2026. Run it again before distribution if time passes.
