# Regression test report, SkyGuard QA 23.4.0

**Date:** 12 November 2026. **Environment:** Linux build container, Python 3.13, Playwright 1.63 with headless
Chromium, pytest 9 with 3 workers. Command: `python -m pytest -n 3 -q tests`.

## Result

| | 23.3.35 baseline | 23.4.0 |
|---|---|---|
| Passed | 561 | **585** |
| Failed | 0 | **0** |
| Skipped | 17 | 17 |

All 561 tests from 23.3.35 still pass. 24 new tests were added. The 17 skipped tests are the same as in the baseline:
they need Java and Apache JMeter, which are not installed in the build environment.

One existing test was changed: `tests/test_regression_pages.py` keeps a list of pages added since 23.2.2, and the 21
new pages (TFS Integration, Browser Manager, AURA and the 18 Comparison pages) were added to it. The test still
requires every new page to load without failed requests or script errors and every old page to load as before.
Result: 62 pages crawled, no differences from the 23.3.35 baseline.

During the first full run this was the only failure (the new pages were not in that list yet). Nothing else failed.

## New tests

| File | Tests | What they check |
|---|---|---|
| `test_comparison_engine_final.py` | 7 | Normalization, mapping confidence and review, reconciliation, deterministic aggregates (COUNT, SUM, AVG, GROUP BY, percentages, monthly, running, pivot), CONFIG_MISMATCH |
| `test_comparison_agent_final.py` | 4 | End to end through the API against two local portals with planted defects (login, pagination, grid, KPI, chart, mapping, run, defects, reports, evidence package, audit chain); SSRF blocking and upload validation (signature, metadata removal, tamper detection, CSV import); project access limited to members; OCR table, KPI and Tesseract TSV parsing |
| `test_tfs_fields_final.py` | 3 | Mandatory Test Result filled with "0 Not Started"; a mandatory Environment asked for, saved as default, matched ("uat" to "UAT"); a rule-only field read from TF401320 and filled; Validate and Test Work Item Creation (nothing saved) |
| `test_healing_final.py` | 3 | Context signals break ties; old fingerprints score as before; review band; an ambiguous match inside shadow DOM waits for confirmation, only offered candidates are accepted, the confirmed script passes |
| `test_perf_params_final.py` | 3 | Dates, users, ids, passwords and tokens found; dates made dynamic with `__timeShift`; other expressions refused; Excel import keeps leading zeros and exports; error classification |
| `test_final_patch_settings.py` | 4 | Settings validation; sign-in method saved and the password never returned; browser check; AURA refuses secrets, integrity, backup, export, confirmed reset, import |

## Manual checks

Screenshots in `evidence/` were taken from the running application: light and dark themes of AI Voice Command
Center, Agent Workflow and Voice & Command Agent, the collapsed sidebar, Comparison Dashboard and Applications, TFS
Integration, Browser Manager, AURA, and the sign-in fields on Application Discovery. The sign-in fields were also used
on Guided recording and Hybrid recording.

## Not verified

These need Windows or systems that were not available. Their code paths ran only against test doubles or not at all:

* Windows OCR and UI Automation (desktop source), capture of RDP/Citrix windows.
* Window placement on a second physical monitor.
* Launching installed Microsoft Edge and Google Chrome (Chromium was launched).
* The Windows Credential Manager (an in-memory store was used in tests; in the container the real keyring is not
  available, so password saves answer 503 there by design).
* A real TFS / Azure DevOps Server (a faithful REST double was used, including TF401320 rule errors).
* JMeter runs (17 skipped tests, unchanged from the baseline).
* The PowerShell wrappers `APPLY_PATCH.ps1`, `ROLLBACK_PATCH.cmd` and `VERIFY_PATCH.cmd` (the Python installer they
  call was tested, see OFFLINE_INSTALLATION_TEST_REPORT.md).
* The Generate Automation tab's sign-in fields were not checked on screen, because that tab needs a TFS connection.
