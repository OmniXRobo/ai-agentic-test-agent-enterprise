# Test report: SkyGuard Adaptive AI Browser Extension 1.0.0

Only tests that were actually executed are reported as passed. Environment: Linux container, Python 3.13, Playwright's
Chromium (new headless mode) with the extension loaded unpacked. No Windows PC, no real Google Chrome or Microsoft
Edge build and no real transport application were available.

## Automated tests (executed)

| Test file | What it covers | Result |
| --- | --- | --- |
| `tests/test_extension_e2e.py` | The extension loaded unpacked into Chromium with the companion running and the stand-in transport site: fixed extension ID, Options (port, pairing with a real code, first admin, sign-in), the separate credential page (refused without consent, saved with consent), a password typed in the side panel chat is not sent and nothing is stored, a request for two trips becomes a preview in the side panel, nothing is submitted before Confirm, the extension's service worker executes in a normal tab, both trips are created through the site's own form (2 POSTs), the run ends *completed* with both items *confirmed_success* and interface *extension*, the operator password appears in no event or audit record, no page errors | 1 passed |
| `tests/test_companion_security.py` | health is the only unauthenticated request; DNS-rebinding Host refused; pairing code single use with attempt limit; pairing and token required; token expiry and refresh; login lockout; JSON only, size limits and schema; roles; password in chat never stored or logged; credential needs consent and is never returned; unsafe application URLs refused; voice accepts only WAV; loopback only | 12 passed, 1 skipped (loopback-only check needs a second network address, which the container does not have) |
| `tests/test_extension_static.py` | MV3 and least privilege; fixed ID matches the companion; shared files identical to SkyGuard's; no remote code, eval or inline script; all pages exist | 5 passed |
| `tests/test_ops_agent_core.py` | the shared agent core: secret detection and redaction, prompt-injection containment, local-only model, URL safety, rules, knowledge states and versions, approval, confirmation hash, self-healing and ambiguity, fingerprints without values, audit chain, vault consent, export / import, backup / restore | 42 passed |

The same agent core is exercised further by SkyGuard's tests (`test_ops_agent_e2e.py`, 9 passed: batch with rules,
no run without confirmation, no duplicates, self-healing, manual intervention and learning, two accounts, wrong role,
one-time code pause, read-only discovery).

## Package checks (executed)

`packaging/offline_install_check.sh` was run against the built zips in a network namespace without internet:

* every file matches `SHA256_CHECKSUM.txt`; the zip matches its `.zip.sha256`;
* the Python 3.13.17 package contains `python.exe` and `pythonw.exe`; the llama.cpp b10549 zip contains
  `llama-server.exe` (both match their pinned SHA-256);
* the 23 Faster-Whisper wheels install offline with `--no-index --require-hashes` for win_amd64 / CPython 3.13
  (pip resolution for the Windows target, done on Linux);
* the companion runs from the extracted package: `version`, `pair` (code shown), `check`, `serve`, `status`;
  `/health` answers; a web page origin and a foreign Host get 403; the extension origin gets the hello; data routes
  without pairing get 401.

The output is in `evidence/offline_install_check.txt` of the SkyGuard patch zip.

## Not executed / not verified

* **Windows 11**: INSTALL.cmd, UNINSTALL.cmd and the PowerShell scripts were not run (no Windows, no PowerShell in the
  container). Their logic was reviewed; the parts they call (the companion commands, pip offline install) were run on
  Linux as above.
* **Real Google Chrome and Microsoft Edge**: only Playwright's Chromium. Developer-mode prompts, Edge's side panel,
  enterprise policies, SmartScreen and the permission prompt UI were not exercised (site access was granted in the
  test manifest because a headless browser cannot answer the prompt).
* **Windows DPAPI vault**: tests use the in-memory backend.
* **Qwen (llama-server) and Faster-Whisper**: not started; tests use stand-ins. Voice was tested only up to the WAV
  upload check.
* **The real transport application**: all execution tests use the stand-in site `tests/transport_site.py`.
* `Install-Models.ps1` with real model files.
