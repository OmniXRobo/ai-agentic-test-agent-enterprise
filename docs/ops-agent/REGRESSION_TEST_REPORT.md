# Regression test report: SkyGuard QA 23.5.0

Only tests that were executed are reported as passed.

## Full suite (executed)

Environment: Linux container, Python 3.13, Playwright Chromium (headless), no Java / JMeter, no Windows.
Command: `python -m pytest -q -rs tests/` on the final source of this release.

| | Tests |
| --- | --- |
| Collected at the 23.4.0 baseline (commit 53e8009) | 602 |
| Collected in 23.5.0 | 692 (602 existing + 90 new) |
| **Passed** | **667** |
| Failed | 0 |
| Skipped | 25 |

Skipped, with the reason pytest reported:

| Count | Tests | Reason |
| --- | --- | --- |
| 17 | performance and voice tests that run JMeter (`test_perf_*`, `test_performance.py`, `test_voice.py`) | JMeter / Java not installed in this container (also skipped at the baseline in the same environment; CI installs JMeter and runs them) |
| 7 | `test_ops_windows.py`: DPAPI vault, Windows Credential Manager, PowerShell parse of the 5 `.ps1` scripts | Windows only; they run on the `windows-latest` CI jobs |
| 1 | `test_companion_security.py::test_listens_on_loopback_only` | the container has no second network address to prove the companion is not reachable on it |

All 602 tests that existed at 23.4.0 ran in this suite; none of them was changed except
`test_regression_pages.py`, which now expects the new `ops-agent` route in the menu.

## New tests (all executed and passed, except the Windows-only ones above)

| File | Tests | Covers |
| --- | --- | --- |
| `test_ops_agent_core.py` | 42 | secret detection and redaction, prompt-injection containment, local-only model, URL safety, rules, knowledge states / versions / rollback, unapproved workflow refused, confirmation hash, self-healing and ambiguity, fingerprints without values, audit chain, vault consent, export / import / backup / restore |
| `test_ops_agent_api.py` | 7 | `/api/ops` registration, credentials (consent, never returned), password typed in chat intercepted, approval and audit, view-only roles, admin-only restore and configuration, other web pages refused |
| `test_ops_agent_e2e.py` | 9 | real headless Chromium against the stand-in transport site: batch with rule rejections and verification, nothing without confirmation, no duplicates on repeat, self-healing on changed markup, missing control → manual action → learned, two accounts (create, then approve), wrong role refused before submit, one-time code pause, read-only discovery |
| `test_ops_patch_install.py` | 5 | patch contents, wrong version refused, changed patch file refused, failure after copy rolls back, apply → verify → rollback → apply again on a 23.4.0 tree from the baseline commit |
| `test_companion_security.py` | 13 | the browser extension's companion (12 passed, 1 skipped) |
| `test_extension_static.py` | 5 | MV3 least privilege, fixed ID, shared files identical, no remote code |
| `test_extension_e2e.py` | 1 | the extension loaded unpacked into Chromium: pair, sign in, credential page with consent, chat secret check, preview, confirm, execution, verified result |
| `test_ops_windows.py` | 8 | 1 runs everywhere (scripts present); 7 Windows-only |

## Continuous integration

The repository's CI runs the whole suite on Windows and Ubuntu with Python 3.11, 3.12 and 3.13, with JMeter. Its
results are on the pull request; they are not copied here because they are produced after this document.

## Not verified

* Windows 11 PC, real Google Chrome / Microsoft Edge, the real transport application, the Qwen and Faster-Whisper
  runtimes (see SECURITY_AUDIT.md and OFFLINE_INSTALLATION_TEST_REPORT.md).
