# SkyGuard QA 23.6.1: security review

**Scope:** every file changed for 23.6.1 (`js/recorder.js`, `js/components.js`, `components.py`, `semantic.py`,
`runner.py`, `recorder.py`, `pytest_export.py`, the cumulative installer) and the dependency set.
**Tools:** manual review; `bandit 1.9.4` (medium and high) on the changed Python files; `pip-audit` on `requirements.txt`.

| Area | Check | Result |
|---|---|---|
| Dependencies | `pip-audit -r requirements.txt` | No known vulnerabilities found |
| Dependencies | CI `pip-audit --strict` on `packaging/requirements-offline-win.txt` | CVE-2026-126360 in `opentelemetry-api 1.45.1` (a FastAPI dependency) was published during the review; the pin was raised to 1.45.2 (same dependencies, hashes from PyPI). Both offline requirement files: no known vulnerabilities found. |
| Dependencies | New packages | None (offline wheel set unchanged) |
| Static analysis | bandit, high | 0 |
| Static analysis | bandit, medium | 3, all present before 23.6.1 and unchanged: two `f"SELECT ... {where}"` in `runner.py` where `where` is one of two fixed strings and values are bound parameters, and one in the installer that formats a table name read from `sqlite_master` (row counts). Not exploitable. |
| Typed text in recordings | Secrets | Password fields are still recorded as `{{CRED:role.password}}`; the new "typed" value of a suggestion is never taken from a password field and is dropped if it is the password placeholder. Credential placeholders (`{{...}}`) are never re-typed as suggestions. |
| Recorder page script | Page data | Reads the page only. It keeps the time elements appeared in a `WeakMap` (released with the page) and sends nothing new beyond the existing step binding. |
| Component engine | Page changes | Still writes only the marker attribute `data-sg-target` on the element Playwright clicks next; no page script is run, no coordinates are used. |
| Counters | Runaway clicking | At most 30 presses per step; stops when a press does not change the number or the control is disabled. |
| Suggestion search | Wrong click | Rows are taken only from a pop-up layer (absolute / fixed) drawn next to the field; page content below the search box is never a candidate; ambiguous matches are offered as choices, never guessed. |
| Booking / payment guard | Unchanged | The 23.6.0 guard for "Book now", "Pay now" etc. still applies to every recorded click; when the suggestion handler cannot choose, the step falls back to that guarded click path. The new handlers themselves only click list rows and counter controls. |
| Installer | Admin rights, network | No administrator rights, no registry, no services, no network imports (test `test_installer_needs_no_network_and_no_administrator`). Every patch file is checked against SHA-256 before anything is copied; any failure restores the backup. |
| Test stand-in | Content | `tests/site/tripcom/` is a local test page only (not shipped in `app/`), contains no Trip.com code, images or trademarks beyond class names and labels needed to reproduce the DOM, and makes no network requests. |
