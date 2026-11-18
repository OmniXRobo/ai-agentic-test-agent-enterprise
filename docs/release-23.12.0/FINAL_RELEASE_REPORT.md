# Final release report: SkyGuard QA 23.12.0 (cumulative from 23.3.35) and extension 1.3.1

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | One fresh, cumulative, offline upgrade to 23.12.0 for any release from 23.3.35 to 23.11.2. It holds `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` (Welcome → Detect → Backup → Install → Verify → Finish), `Manual_Install` (`APPLY_PATCH.cmd`, `VERIFY_PATCH.cmd`, `ROLLBACK_PATCH.cmd`), `README_FIRST.txt`, the documents (manifest, test report, security report, guides), the SBOM, test results, evidence screenshots and `SHA256SUMS.txt`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Extension and companion 1.3.1 (unchanged in 23.12.0). `UPDATE_EXTENSION.cmd` updates an installed one; `INSTALL.cmd` is for new installs. |
| `.sha256` files | The SHA-256 of each zip. |

No internet is needed. Your data, and what the agent learned, are backed up first and
kept. If any step fails, the previous version is restored automatically (this was seen happen, see below).

## What is new

* **API Testing** in Automation Studio: applications with authorized targets, Test Connection with real diagnostics,
  every common authentication type through the secure credential store, offline OpenAPI / Swagger import, cURL /
  Postman / Python import that never runs anything, generated tests, editor, runs and chains, response viewer,
  pytest + httpx scripts with tabs and versions, failures as Defect Review drafts, TFS only after approval.
* **Scheduled Runs**: API tests and Integration Comparisons can be scheduled; Visible / Headless / Smart Auto with the
  reason on each result; the PC's time zone is shown.
* **Integration Comparison**: mismatches become Defect Review drafts; saved comparisons can be scheduled.
* **Agent Workflow**: six missing agents added to the same page, in the same layout; a card says UNAVAILABLE with the
  reason instead of READY when its engine cannot work.
* **Python editor**: full screen, taller / shorter, resizable, error lines marked.
* **Setup**: the footer shows only "SkyGuard QA 23.12.0", as asked. The publisher **Muhammad Tufail** is on the
  Welcome page and in Setup.exe's properties.

**Fixed after the first package** (PATCH_CHANGELOG.md, last section):

* **AI Agent:** "Finish lab" on SauceDemo no longer refused with "The signed-in identity has not been verified".
  Simpler to read: no extra "again" message after saving a password, *Learned* instead of *Uncertain*, a *Why* line
  when a task did not finish.
* **AI Agent on Swag Labs:** a completed "Finish lab" order ("Thank you for your order!") is now shown as
  Completed instead of FAILED / Uncertain; "buy item" is found by its name; no "Zip/Postal Code must be unique"
  guess. The extension's companion gets the same fix.
* **Integration Comparison:** *Apply filters* InvalidSelectorError fixed; a last page whose Next button stays active
  is no longer reported as INCOMPLETE.

Details: PATCH_CHANGELOG.md, USER_GUIDE.md, ROOT_CAUSE_ANALYSIS.md.

## Test results (TEST_REPORT.md)

* **API Testing:** 24 passed, 1 skipped (live IPv6: no IPv6 on the build machine). Secrets were searched for in the
  database, logs, results, report and export and were not found.
* **Pages in a real browser (Chromium):** API Testing end to end, Scheduled Runs and Agent Workflow, 3 passed, no
  JavaScript errors; every page of 23.3.35 still loads.
* **Full suite on Linux:** 999 passed, 19 failed, 201 skipped on the first run. The 19 had two causes (one in the
  upgrade's own verification, one in a page list), both fixed; those tests were run again and passed.
* **Installer:** apply, verify, rollback and apply again on 14 of 14 earlier releases, 23.3.35 to 23.11.2
  (27 passed).
* **Second round of fixes** (TEST_REPORT.md section 9): AI Agent and comparison 132 passed, 9 skipped; API and pages
  31 passed, 1 skipped; installer and browser 31 passed; Setup under Wine with the publisher: every check passed.
* **Setup under Wine** over a real 23.11.2 installation: the first build was refused at Verify and **rolled back by
  itself with no data lost**; after the fix the upgrade completed (TEST_REPORT.md section 6). OCR gives a WARN under
  Wine, as in every release.

## If you installed the first 23.12.0 package

It did not start ("Form data requires python-multipart"). Run this package's Setup.exe over it: Detect says
23.12.0 is already installed and offers a repair, which replaces the files and keeps your data. Or run
`patch-tools\23.12.0\ROLLBACK_PATCH.cmd` in the SkyGuard QA folder to go back to the previous version first.
Details in TEST_REPORT.md section 8.

## Security (SECURITY_REPORT.md)

bandit: no injectable query (5 B608 findings read one by one, all bound parameters); one XML finding fixed.
pip-audit: no known vulnerabilities. Secrets never in scripts, reports or logs; only authorized targets are
contacted; SkyGuard's own port, metadata addresses and unauthorized redirects are refused; imported scripts are never
run; TLS verification is on by default; tests that change data need confirmation and never run on Production from a
schedule; nothing goes to TFS without approval. Residual risks are listed in the report.

## About "Unknown publisher"

Windows SmartScreen takes the publisher only from a digital signature. Setup.exe now names Muhammad Tufail as its
publisher, but it is not signed, so SmartScreen still says *Unknown publisher* (choose *More info*, *Run anyway*).
To change that, Setup.exe must be signed with a code-signing certificate issued to Muhammad Tufail; the build signs it
automatically when the certificate is given (INSTALLATION_GUIDE.md).

## Not verified

* A real Windows 10 / 11 PC, Microsoft Edge and Google Chrome (Wine and Chromium were used).
* saucedemo.com and tabulizer.com (the network refused them; stand-ins with the same behaviour were used). Run
  *Finish lab* and the comparison once after installing.
* A live local Qwen, your own APIs, and a live IPv6 request.
