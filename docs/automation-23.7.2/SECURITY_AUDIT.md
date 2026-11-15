# SkyGuard QA 23.7.2: security review

**Scope:** the files changed for 23.7.2 (`automation/framework/steps.py`, `walker.py`, `builder.py`, `studio.py`,
`registry.py`, `runner.py`, the framework runtime files `utilities/locators.py` and `utilities/reporting.py`, the
cumulative installer and its build script). Earlier reviews are under `previous-*`.

| Area | Check | Result |
|---|---|---|
| Dependencies | New packages | None. The step reader is regular expressions; the walker uses the Playwright already installed. |
| Generated code | Injection | Every value written into a script (step titles, reasons of flagged steps, data keys, URLs) goes through `codegen.py_str` (a JSON string literal) or `codegen.snake` (letters, digits, underscore). The generated files still pass the validator's script-safety check (no subprocess, network, eval or file deletion). |
| Credentials | Exposure | The walk reads the saved Application Credentials from the secret store and types them into the browser only. Scripts reference `credentials.username` / `credentials.password`; the password is never written into a script, test data, a note or the generation log. Password field values are never read back (`value` is not collected for `type=password`). |
| Browser walk | Side effects | Generation performs the Test Case steps once on the application, exactly what a run of the script does. Logout/sign-out, cancel, delete and remove controls are never chosen to advance a form. It can be switched off (`automation.live_step_walk: false`). Use a test environment, as for any run. |
| AI requests | Data sent | The per-step request contains the step text, its expected result and the names/selectors of at most 40 elements of the current page; no credentials, no field values. A selector returned by the AI is used only if it is one of those elements. |
| Missing fields | Integrity | A field the application does not have is never mapped to another element; the script states it as NOT AUTOMATED. |
| Locators | Selector building | `test_id` values are JSON-quoted inside the CSS attribute selector. |
| Installer | Admin rights, network | Unchanged rules: no administrator rights, no registry, no services, no network imports; every file checked against SHA-256 before copying; automatic restore on failure. |
| Test stand-in | Content | `tests/site/saucedemo/` is a local test page reproducing the demo shop's element ids and texts for offline tests; not shipped in `app/`, no network requests. |
