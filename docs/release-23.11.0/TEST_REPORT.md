# Test report: SkyGuard QA 23.11.0

Every result below comes from a run that actually happened on 17 November 2026. Failures and what was done about
them are listed, not hidden. The JUnit files and logs are in `test-results/`.

Environment: Linux container (Python 3.13, pytest 9.1.1, Playwright with Chromium, Xvfb), Wine 9.0 for the Setup
wizard, and GitHub Actions for Windows (`windows-latest`, Windows PowerShell 5.1).

## 1. Full suite on Linux

    python -m pytest -q --junitxml=full-suite-linux.xml \
        --ignore=tests/test_cumulative_patch_231100.py --ignore=tests/test_cumulative_patch_231000.py

**900 passed, 1 failed, 96 skipped** in 35 minutes (`full-suite-linux.xml`, `full-suite-linux.log`).

**The failure:** `test_smart_wait_231100.py::test_timeout_is_reported_and_next_failure_is_classified_timing`. The
draft was classified *application defect* where the test expected *timing*.

**Cause:** a race in the test, not in the product. The test site answered after 1.2 seconds. Under the full suite's
load, that answer had already arrived by the time the failure was examined, so the evidence correctly no longer showed
a busy page. The test now uses a 5-second answer, so the page is certainly still loading when the step gives up.
`test_without_waits_the_ajax_steps_fail` had the same kind of race on two GitHub runners (section 4) and was changed
the same way.

**After the fix:**
* `test_smart_wait_231100.py` passed 3 times in a row while the installer test ran on the same machine (8 of 8 each
  time).
* The final run of the three 23.11.0 feature files, `test_smart_wait_231100.py`, `test_python_editor_231100.py`
  and `test_comparison_tfs_231100.py`: **20 passed, 0 failed** (`smart-wait-final.xml`).

Skipped tests are the same kinds as in 23.10.0: older patch tests that only run on their own tree, JMeter (no Java
here), Windows-only tests (run in section 4), and tests needing a display or the OCR runtime (run in section 2).

## 2. OCR and window tests under Xvfb with the OCR runtime

**31 passed, 0 failed** (`ocr-display-linux.xml`). This code did not change in 23.11.0; the run confirms it still
works.

## 3. Installer (`install_patch.py`) on every real release

`tests/test_cumulative_patch_231100.py`. Each of the 11 baselines is extracted with `git archive` from its own
release commit and given a database created by that release's own code.

**23 passed, 0 failed** (`cumulative-patch-231100-linux.xml`):

* `test_patch_contents`;
* no network and no administrator;
* wrong versions (23.3.31, 23.12.0) refused, changing nothing;
* a changed file refused;
* automatic rollback after a failure;
* **apply → verify → rollback → apply again** on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2,
  23.8.0, 23.9.0 and **23.10.0** (11 of 11). Each one checks migration 20 and the new tables, the new 23.11.0
  verification check, and that no table lost rows;
* OCR runtime staged, swapped and rolled back;
* protected folder never forced, and the side-by-side copy works;
* Detect on 23.3.35 and 23.10.0;
* `--log`.

The first run, before the release documents existed, had 1 failure: `test_patch_contents` checks that the patch
contains them. It passed once they were written.

## 4. Windows (GitHub Actions), pull request #55

**Run 44060836075, commit `52e77df`: every job passed.**

| Job | Result |
|---|---|
| Full suite, Windows, Python 3.11 / 3.12 / 3.13 | 917 passed, 125 skipped, 0 failed (each) |
| Full suite, Ubuntu, Python 3.11 / 3.12 / 3.13 | 910 passed, 132 skipped, 0 failed (each) |
| `setup-exe-windows` on real **23.3.35**, **23.9.0** and **23.10.0** installations | passed. Setup.exe refuses an unsupported version (exit 3, nothing changed); unattended upgrade and verify; rollback; upgrade again with `APPLY_PATCH.ps1` under `powershell.exe` 5.1 |
| `extension-install-windows` | passed. The 23.10.0 companion installer fails as reported; the fixed one installs on a fresh profile with TEMP on another drive, the companion answers, and reinstalling works |
| `offline-package`, `offline-package-download`, `setup-exe-build`, `security-audit` | passed |

**The run before it** (44054295785, commit `75b3435`) had 4 of the 6 test jobs fail, each on one Smart Wait test
(the two timing races described in section 1). Its installer, extension and package jobs passed.

The commits after `52e77df` change only:
* the Setup Finish page text,
* the robustness of `test_without_waits_the_ajax_steps_fail`,
* these documents.

Their CI run is listed on the pull request.

## 5. Setup wizard under Wine 9.0 over a real 23.10.0 installation

`tests/wine_setup_walkthrough.sh` over 23.10.0 (commit `8c8dcc4`), using the Windows Python 3.13.17 and Windows
packages of the offline package. The installation had a database with data. Screenshots: `evidence/setup-*.png`.

* Welcome, Detect ("SkyGuard QA 23.10.0 was found and can be upgraded to 23.11.0"), Backup, Install, Verify and
  Finish: each page was shown and passed with Next.
* The log says: **Patched 23.10.0 -> 23.11.0**.
* Every check PASSED, including *Python editor, Smart Wait, Defect Review (23.11.0)*, *Database migrations: schema
  version 20* and *Review and Python tables*.
* The one exception is *Offline OCR engine*: **WARN** under Wine, the same as in 23.10.0 (Windows NumPy does not
  start under Wine). On real Windows it passes (section 4).

This walkthrough used the Setup.exe built before the Finish page text was changed to mention the new features. The
shipped Setup.exe has that text change only. It is the same installer and payload, and is covered by the Windows CI
of the final commit.

## 6. New features in a real browser (Playwright, Chromium)

| Flow | Test | Result |
|---|---|---|
| Python card toolbar; Edit Script; a syntax error listed as *Line N, column M*; Save gives version 1; Version History view; the download is the saved version; the recorded steps are unchanged | `test_ui_231100.py` against a running SkyGuard | passed. Screenshots `evidence/ui-11` to `ui-15` |
| Wait settings saved with the script; Insert waits after dropdowns twice inserts 2 waits, not 4 | same | passed. `ui-12`, `ui-13` |
| A failing run of the saved Python becomes a Needs Review draft with traceback, screenshot and trace; Approve sends nothing; Send to TFS without a configured TFS is refused, no ID is shown | same | passed. `ui-16` to `ui-18` |
| Edited Python runs and downloads; regenerate warns; revert; versions; roles; data rows each run as a test | `test_python_editor_231100.py` (4) | passed |
| Smart Wait: AJAX dropdown, table refresh, loader; the global policy; bulk insert/update/remove without duplicates; exported Python waits the same way; recorder records waits; strict wait; timeout reported and classified as timing | `test_smart_wait_231100.py` (8) | passed |
| Integration Comparison → TFS: real ID and URL after read-back; duplicate and unreachable TFS as clear answers (no 500); **lost response reconciled, not created twice**; server error keeps the draft with a correlation ID, and Retry works; no publish without approval, and an edit removes the approval; mandatory field asked, not invented; secrets redacted; AI answer validated | `test_comparison_tfs_231100.py` (8) | passed |
| AI-Driven failures drafted and classified; the automatic mode never publishes | `test_studio.py`, `test_defects.py` | passed |

## 7. Security tools

See SECURITY_REPORT.md:
* pip-audit: no known vulnerabilities in the offline packages, Faster-Whisper or the 21 OCR wheels;
* bandit: 5 findings on changed lines, 1 fixed and 4 reviewed;
* secret scan: no secrets.

## 8. Not executed

* **Windows 11 itself.** Windows Server 2025 runners (GitHub Actions) were used, plus Wine.
* **A real TFS / Azure DevOps Server.** A stand-in server was used (KNOWN_LIMITATIONS.md).
* **A live local Qwen** server.
* SSO, MFA and CAPTCHA applications.
* JMeter performance tests in this container (they run in CI).
