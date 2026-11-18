# Test report: SkyGuard QA 23.11.2 and extension 1.3.1

Every result below comes from a run that actually happened on 17 November 2026. The JUnit files and logs are in
`test-results/`.

Environment: Linux container (Python 3.13, pytest, Playwright with Chromium in new-headless mode), Wine 9.0 for the
Setup wizard, and GitHub Actions for Windows (section 5).

## 1. The reported conversation

`tests/test_missing_info_231102.py`, **21 passed**, and `tests/test_ui_231102.py`, **1 passed**.

They run against the stand-in online store (`tests/shop_site.py`). Its checkout has the same pages and fields as
Swag Labs: *Checkout: Your Information* (First Name, Last Name, Zip/Postal Code), *Overview*, *Finish*. Like Swag
Labs, it never shows who is signed in.

| Step (typed by the person) | Agent's answer | Result |
|---|---|---|
| checkout an order | Missing information: First Name, Last Name, Zip/Postal Code | passed |
| First Name; muhammad (the reported answer) | "Got it: First Name = muhammad." Still needs Last Name and Zip/Postal Code | passed |
| pre pare it (the reported answer) | Still needs Last Name and Zip/Postal Code (First Name is not asked again) | passed |
| last name Ali | "Got it: Last Name = Ali." Still needs Zip/Postal Code | passed |
| 54000 | The preview: Checkout order, muhammad, Ali, 54000, 1 ready | passed |
| Confirm & Execute | "Checkout order: done, confirmed by the application, number ORD-1002". The shop has one more order. | passed |

* `test_missing_info_231102.py` drives this through the agent and the run engine in Chromium.
* `test_ui_231102.py` types it into SkyGuard's own AI Agent page (SkyGuard served in-process, Chromium through
  Playwright). Screenshots:
  * `evidence/231102-ai-first-name-received.png`
  * `evidence/231102-ai-preview-values.png`
  * `evidence/231102-ai-order-done.png`
* Parser cases:
  * 13 cases, among them "first name is Muhammad", "First name: Muhammad, last name Ali, zip 12345",
    "Muhammad; Ali; 12345" and "my first name is Sara and my last name is Ahmed and the zip code is 54000";
  * "prepare it" and "muhammad ali" (two words for three fields) give no value;
  * one asked field takes the whole answer;
  * a value named for another field goes to that field;
  * a list field accepts only one of its options.
* Also tested:
  * answers are not offered as suggestions;
  * a new request replaces the question;
  * the authorization rule for a sign-in confirmed by the application. It is accepted without a role restriction.
    It is refused with a role restriction, in production mode, without a confirmed sign-in, and for another account.

**Found on the way.** The first run of the conversation got past the answers and then stopped twice. Both causes are
fixed in this release (ROOT_CAUSE_ANALYSIS.md, faults 2 and 3):

* "Zip/Postal Code: 54000" was stopped as a secret code.
* The run stopped with "The signed-in identity has not been verified from the application", because the shop, like
  Swag Labs, never shows the user name.

## 2. Full suite on Linux

    python -m pytest tests -q --junitxml=full-suite-linux.xml --ignore=tests/test_regression_pages.py \
        --ignore=tests/test_cumulative_patch_231100.py --ignore=tests/test_cumulative_patch_231000.py

**997 passed, 0 failed, 120 skipped** in 44 minutes (`full-suite-linux.xml`, `full-suite-linux.log`).

* The run includes:
  * all of 23.11.1's tests: tests A to H, 41 passed; the AI Agent page, 5 passed;
  * extension 1.3.0/1.3.1, 5 passed (the version test now checks 1.3.1);
  * the earlier extension tests, including `test_shared_files_are_identical_to_skyguard`, which shows that the
    changed `secret-check.js` is the same in SkyGuard and the extension;
  * the installer tests of section 3.
* The 24 tests of `test_cumulative_patch_231101.py` skip themselves, because they build 23.11.1 from the current tree
  and the tree is now 23.11.2. `test_cumulative_patch_231102.py` replaces them.
* The other skipped tests are the same kinds as in 23.11.1:
  * older patch tests that only run on their own tree;
  * JMeter (no Java here);
  * Windows-only tests (run in section 5);
  * tests that need a display or the OCR runtime.
* `tests/test_ui_231102.py` was written while the suite was running, so it is not in that run. It was run on its own
  (section 1), and again with the installer tests at the final state of the code: **47 passed** (installer 25, page 1, conversation and parser 21)
  (`missing-info-ui-installer-231102.xml`).
* Before the full run, the related suites ran on their own: **177 passed, 1 skipped**. These were the 23.11.1
  repair, the 23.10.0 agent tests, the companion, all extension tests, the AI Agent page, the operations agent tests
  and the new tests.

## 3. Installer (`install_patch.py`) on every real release

`tests/test_cumulative_patch_231102.py`, **25 passed, 0 failed**. Each of the 13 baselines is extracted with
`git archive` from its own release commit and given a database created by that release's own code.

* `test_patch_contents`: extension 1.3.1, the new tests, and `previous-23.11.1` documents.
* No network and no administrator rights.
* Wrong versions are refused and nothing is changed. A changed file is refused. A failure rolls back automatically.
* **Apply → verify → rollback → apply again** passed on all 13 releases: 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1,
  23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0 and **23.11.1**.
  * Each run checks the new *Missing information follow-up (23.11.2)* check and *AI Agent repair (23.11.1)*.
  * Each run also checks migration 20, that the knowledge database is kept, and that no table lost rows.
  * A rollback from 23.11.1 keeps 23.11.1's own files (for example `skyguard_agent/intent.py`).
* OCR runtime staged, swapped and rolled back.
* Protected folder and the side-by-side copy.
* Detect on 23.3.35 and 23.11.1.
* `--log` ("Patched 23.11.1 -> 23.11.2").

## 4. Setup wizard under Wine 9.0 over a real 23.11.1 installation

`tests/wine_setup_walkthrough.sh` was run over a 23.11.1 installation with a database holding data. The 23.11.1
application files came from commit `8e8df0d`. Screenshots are in `evidence/setup-*.png`.

* Pages walked through: Welcome, then Detect ("SkyGuard QA 23.11.1 was found and can be upgraded to 23.11.2"), then
  Backup, Install, Verify and Finish.
* The log says **Patched 23.11.1 -> 23.11.2**.
* Every check passed, among them:
  * *Missing information follow-up (23.11.2)*;
  * *AI Agent repair (23.11.1)*;
  * *Database migrations: schema version 20*;
  * *No data lost*.
* The one exception is *Offline OCR engine*: **WARN** under Wine, as in every earlier release, because Windows NumPy
  does not start under Wine. On real Windows it passes.
* The walkthrough was run twice: once with the first build, and again over a fresh copy of the 23.11.1 installation
  with the final Setup.exe (the one in the zip, which also carries `tests/test_ui_231102.py`). Both runs gave the
  same result. The screenshots are from the second run.

## 5. Windows (GitHub Actions), pull request #56

Two runs on the pull request, at commits `d116afd` (run 44147769003) and `89c7e5a` (run 44157838760).

| Job | Result |
|---|---|
| `setup-exe-windows` on real **23.3.35**, **23.11.0** and **23.11.1** installations | **passed** in both runs. Setup.exe refuses an unsupported version and changes nothing. The unattended upgrade and verify ran, the rollback restored the baseline, and the upgrade ran again with `APPLY_PATCH.ps1` under Windows PowerShell 5.1. |
| Full suite, Ubuntu, Python 3.11 / 3.12 / 3.13 | **passed** in both runs |
| Full suite, Windows, Python 3.11 | **passed** in both runs |
| Full suite, Windows, Python 3.12 | **passed** in the second run. In the first run, one 23.3.28 performance-report test failed (`PermissionError` reading `qa-report.pdf` while the report service was still writing it). 23.11.2 does not touch that code. |
| Full suite, Windows, Python 3.13 | **failed in both runs**, each time in the 23.11.1 tests that use the stand-in transport application. In the first run one test never reached its preview. In the second run the scripted person who teaches the transport application did not see the *Create Trip* form within 30 seconds, so the 15 tests that need that learning could not start. The same tests passed on Windows 3.11 and 3.12, on Ubuntu, and locally. The cause is not known yet. A third run carries diagnostics that report the page shown at the failed step. |
| `extension-install-windows`, `offline-package`, `offline-package-download`, `setup-exe-build`, `security-audit` | **passed** in both runs |

The tests of the reported fault (`test_missing_info_231102.py`) and the installer tests (`test_cumulative_patch_231102.py`)
passed on every Windows and Ubuntu job of both runs.

## 6. Not executed

* **Swag Labs and your own applications.** The stand-in shop was used. Run *Finish lab* once on Swag Labs after
  installing.
* **Microsoft Edge and Google Chrome on Windows.** Playwright's Chromium on Linux was used.
* **A live local Qwen.** Answers are read without the model, so they do not depend on it.
* **Windows 11 itself.** Windows Server runners (GitHub Actions) and Wine were used.
