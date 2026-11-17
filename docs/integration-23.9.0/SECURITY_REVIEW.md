# Security review: 23.9.0

These are my own review notes on the changes. This is not a penetration test, and no external audit was done.

## Credentials and secrets

* Application passwords are still kept in Windows Credential Manager (keyring). They are never stored in the
  database, settings, datasets, logs or reports. The settings API still refuses secrets (`SECRET_IN_SETTINGS`) and
  now also refuses unknown comparison keys.
* A password is typed only into the password field. Screenshots and previews mask `input[type=password]`. The filter
  discovery script skips password, hidden and file inputs, and does not treat login forms as filters.
* HTTP Basic / Windows credentials are restricted to the application's own origin
  (`http_credentials.origin`). In 23.8.0 they went to every host the page contacted.
* Each application has its own browser context and session. Cookies never cross applications and are never exported.
* **The local AI (Qwen) receives field names and up to 4 sample values per field, never cookies, tokens, passwords or
  URLs with credentials.** The system prompt declares sample text as data, never instructions
  (`mapping.AI_SYSTEM`). An AI answer can only propose mappings between field names that exist. Invented names are
  dropped, and every AI mapping needs a tester's approval. The AI error log records the error code and message only,
  with no sample values (covered by `test_08_invalid_output_keeps_the_deterministic_result_and_logs_why`).

## Web pages are untrusted

* Page text, labels and options are data. They are never executed as commands and never sent to the AI as
  instructions. Filter values are set through the page's own controls, the same way a user does it.
* Main-frame navigation is allowed only to the configured application, the same site or approved hosts. A redirect
  elsewhere is blocked and reported. Requests to private-network addresses (10/8, 172.16/12, 192.168/16, 127/8,
  169.254/16, link-local IPv6) are blocked unless the application itself is on the intranet. Public sub-resources
  (scripts, styles, data from a CDN) are allowed, because without them modern pages render nothing. The 23.8.0
  behaviour, where only the listed hosts are allowed, is available with `strict_hosts`.
* Login, MFA and CAPTCHA are never bypassed. A detected CAPTCHA or a manual sign-in pauses the filter session
  (`FILTER_NEEDS_USER`, state "paused"). The tester continues in the visible browser and clicks Resume.
* Screenshots and uploads are validated before OCR: format allow-list, an 80-megapixel limit and Pillow `verify()`.
  OCR runs in a separate process with a timeout (`ocr_worker.py`, started with `python -I`).

## Windows controls are not weakened

* The patch needs no administrator rights. It writes only to the installation folder of the user, or to
  `%LOCALAPPDATA%\SkyGuardQA`. It changes no registry key, PATH, service, firewall rule or execution policy.
* A protected application folder is never forced (no ACL changes, no elevation). The installer explains the
  situation and offers a side-by-side copy in the user's own folder.
* Windows OCR no longer passes `-ExecutionPolicy Bypass` (23.8.0 did). The wrappers keep the process-scoped
  `-ExecutionPolicy Bypass` for `APPLY_PATCH.ps1` itself, as in every earlier patch. That changes nothing on the
  machine. A patch can also be applied with `python install_patch.py apply ...`.
* The OCR wheels are verified by SHA-256 against `ocr-runtime/WHEELS.json`. The unpacker refuses any path outside the
  runtime folder (zip slip). The runtime is in its own folder, added only to the OCR worker's path.
* `test_installer_needs_no_network_and_no_administrator` checks that the installer imports no network module and that
  the tooling contains no RunAs, HKLM, `requireAdministrator`, service creation or Program Files path.

## Access control

* Changing filters, links and filter sets requires an editor role. View-only roles get 403 (`_editor()` in
  `api/workspace.py`), and so do mapping approvals.
* Every filter discovery, fetch, link change and run is recorded in the comparison audit (`workspace.filters.*`).

## Residual risks

* Public sub-resources are allowed by default. A page that loads a malicious script from a CDN runs it in the
  isolated, non-persistent Playwright context, as it would in the tester's own browser. Use `strict_hosts` for
  sensitive applications.
* The bundled OCR wheels are third-party binaries, pinned by version and hash. They were not code-reviewed here. The
  C++ runtime DLLs (msvc_runtime wheel) are Microsoft's redistributable files; they are loaded only inside the OCR
  worker process and are not registered in Windows.
* The Windows wrappers and the Windows-specific code paths (Credential Manager, Windows OCR, side-by-side launcher)
  were not executed in this review. See KNOWN_LIMITATIONS.md.
