# Test report: SkyGuard QA 23.12.0 and extension 1.3.1

Every result below comes from a run that actually happened on 18 November 2026. The JUnit files and logs are in
`test-results/`. Nothing below is claimed from reading code alone.

Environment: Linux container (Python 3.13, pytest, Playwright with Chromium in new-headless mode), Wine 9.0 for the
Setup wizard. A real Windows PC, Microsoft Edge, Google Chrome and a running local Qwen were not available (section 7).

## 1. API Testing (new)

`tests/test_api_testing_231200.py`: **24 passed, 1 skipped** (`api-comparison-231200.xml`).

They run against a local Orders API written for the tests (`tests/api_site.py`): HTTP and HTTPS (with a private CA
uploaded as the application's CA), Basic, Bearer, API key, OAuth 2.0 client credentials, redirects, slow and broken
endpoints, and an OpenAPI 3 document. What was checked:

* Application configuration: base URL or protocol + host + port + base path; IPv4, host names and IPv6 literals are
  parsed and built correctly; invalid configurations are refused with a clear message; the "authorized" tick is
  required.
* Test Connection: authorized target, name resolution, TCP connection, TLS and the HTTP answer are each reported.
* Authentication: Basic, Bearer, API key (header and query) and OAuth 2.0 client credentials. Secrets are stored
  through the existing secure credential store. **The password and its Basic token were searched for in the database file, the
  log files, the run results, the audit, the export and the HTML report, and were not found anywhere**; a script that
  contains a stored secret value is refused; headers show `[redacted]`.
  An API key sent in the query string is redacted from stored URLs.
* Role-based authorization tests (a role that must be refused).
* Imports: OpenAPI 3 JSON, Swagger 2.0 YAML split over several files, unresolved external references listed (never
  downloaded), malformed documents and a YAML "alias bomb" refused. cURL, Postman and Python imports are read and
  **never executed** (the test plants a command that would create a file; the file never appears).
* Generation: positive, required-field, data-type, authentication, boundary, pagination, filtering and sorting tests;
  data-driven rows.
* Runs: chains with extracted variables; a missing variable makes the step FAILED; a step after a failed step is
  BLOCKED. Response-time assertions, per-test timeouts, connection failures.
* Failure classification (API contract, server error, authentication, performance ...) and **Defect Review drafts**;
  the same failure again adds an occurrence (2 drafts, not 4). **TFS publication only after approval** (against the
  fake Azure DevOps server), with the Work Item ID kept.
* Security: own port 8765, loopback forms of it, cloud metadata addresses and redirects to unauthorized hosts are
  refused; tests that change data need confirmation, and are refused from a schedule on Production; a view-only user
  cannot change anything.
* The generated pytest + httpx script validates, runs, and contains no secret.
* A scheduled API run executes and sends the "Scheduled run started" notification.
* The six new agents are each listed exactly once.

Skipped: **a live IPv6 request**, because this machine has no IPv6 loopback (`::1`). IPv6 parsing and URL building
passed.

## 2. Integration Comparison schedule and drafts

`tests/test_comparison_schedule_231200.py`: **3 passed**. A saved comparison appears in Scheduled Runs, runs from the
schedule, and every confirmed mismatch becomes one Defect Review draft; a manual run reports how many drafts it made.

## 3. Pages in a real browser

`tests/test_ui_231200.py`: **3 passed** (SkyGuard served in-process, Chromium through Playwright acting as the person;
`ui-installer-231200.xml`). Screenshots are in `evidence/screenshots/`.

* API Testing end to end: create the application, store its password ("stored", the password is never in the page),
  Test Connection ("Reachable", "TCP connection"), import the OpenAPI document (at least 10 tests generated), run all (the confirmation
  for tests that change data is accepted), open a result in the response viewer (assertions, JSON tree, Authorization
  shown as `[redacted]`), generate the Python script, open every script tab, full screen and Esc.
* Scheduled Runs: Page filter "API Testing" lists "Orders API: all API tests"; the schedule form says the item runs
  without a browser.
* Agent Workflow: the new agents are shown in the existing layout (screenshot `231200-agent-workflow.png`).
* No JavaScript error on any page.

`tests/test_regression_pages.py` (every page of 23.3.35 and 23.12.0 loads and nothing disappeared): **passed**.

## 4. Full suite on Linux

    python -m pytest -q

**First run: 999 passed, 19 failed, 201 skipped** in 40 minutes (`full-suite-linux-first-run.log`).

All 19 failures had two causes, both fixed and re-run:

* 18 in `test_cumulative_patch_231200.py`: the upgrade's own verification refused 23.12.0 because the 23.11.0 check
  counted exactly six failure classes and 23.12.0 adds three (ROOT_CAUSE_ANALYSIS.md). One test also used "23.12.0" as
  a made-up future version, which is now the real version; it uses 23.13.0.
* 1 in `test_regression_pages.py`: the new API Testing page was not yet in the list of pages added since 23.3.35.

After the fixes: `test_cumulative_patch_231200.py` **27 passed**; `test_regression_pages.py` **1 passed**; the API,
comparison and agent-registry tests again **37 passed, 1 skipped**. The other 999 tests did not touch the changed
lines (the check string in `install_patch.py` and the two test files).

The skipped tests are the same kinds as in 23.11.2: older patch tests that only run on their own tree, JMeter (no
Java here), Windows-only tests, and tests that need a display or the OCR runtime.

## 5. Installer (`install_patch.py`) on every real release

`tests/test_cumulative_patch_231200.py`, **27 passed, 0 failed** (`ui-installer-231200.xml`). Each of the 14
baselines is extracted with `git archive` from its own release commit and given a database created by that release's
own code.

* **Apply → verify → rollback → apply again** passed on all 14 releases: 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1,
  23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1 and **23.11.2**. Each run checks the new
  *API Testing and new agents (23.12.0)* check, migration 21 (the API tables), that the knowledge database is kept and
  that no table lost rows.
* No network and no administrator rights. Wrong or unknown versions are refused and nothing is changed. A changed
  patch file is refused. A failure rolls back automatically.
* OCR runtime staged, swapped and rolled back; protected application folder and the side-by-side copy; detect on
  23.3.35, 23.11.1 and 23.11.2; `--log` ("Patched 23.11.2 -> 23.12.0").

## 6. Setup wizard under Wine 9.0 over a real 23.11.2 installation

`tests/wine_setup_walkthrough.sh` over a 23.11.2 installation (application files from commit `54b69f0`, Windows
Python 3.13 runtime, a database holding data). Screenshots: `evidence/setup-*.png`.

* **First run (first build): the upgrade was refused, and the rollback worked.** Verify reported
  *Python editor, Smart Wait, Defect Review (23.11.0) FAIL* (cause in section 4). Setup restored 23.11.2 by itself;
  afterwards the installation was 23.11.2 again and the 5 rows put in the database before the upgrade were all there.
  Log: `test-results/setup-first-run-rolled-back.txt`.
* **Final run (fixed build):** Welcome, Detect ("SkyGuard QA 23.11.2 was found and can be upgraded to 23.12.0"),
  Backup, Install, Verify and Finish. See the result line below.
* The footer of every page reads only **"SkyGuard QA 23.12.0"**; "offline, no administrator rights" is gone.
* *Offline OCR engine* gives **WARN** under Wine, as in every earlier release, because Windows NumPy does not start
  under Wine (Wine shows a python.exe crash window for it). On real Windows it passes.

**Result of the final run: Patched 23.11.2 -> 23.12.0.** Every check passed on both verifications (the one at the
end of the install and the one on the Verify page), among them *API Testing and new agents (23.12.0)*,
*Python editor, Smart Wait, Defect Review (23.11.0)*, *Missing information follow-up (23.11.2)*, *Database
migrations: schema version 21* and *No data lost*; the only exception is the OCR WARN above. Finish says "SkyGuard QA
23.12.0 is installed". This upgrade run was repeated with the build that has the python-multipart fix (section 8):
the same result.

The walkthrough script itself was improved on the way: it now waits for the Setup log to show both verifications (it
used to look for a log file that Setup no longer writes, and took the last screenshots too early), and it turns off
Wine's crash window, which otherwise held the install for 10 minutes until a click.

## 7. Not executed

* **A real Windows 10 / 11 PC.** Wine was used here. The pull request's Windows CI jobs install and verify the upgrade
  on Windows runners.
* **Microsoft Edge and Google Chrome.** Playwright's Chromium on Linux was used.
* **saucedemo.com.** The network refused it (HTTP 403). The AI Agent flows ran on the local SauceDemo stand-in and the
  stand-in shop in the full suite.
* **A live local Qwen.** Failure classification ran with the rules only; AI test suggestions were not run.
* **Your own APIs and OpenAPI documents**, OAuth 2.0 password grant, client certificate against a server that demands
  one, cookie sign-in against a real login, proxies, and a live IPv6 request (KNOWN_LIMITATIONS.md).

## 8. Found by CI after the first delivery: SkyGuard did not start (python-multipart)

The first 23.12.0 package installed and passed Setup's checks, but the application then stopped at start with
*"Form data requires python-multipart to be installed"*. This was seen by the pull request's Linux CI jobs and on a
person's PC. Cause and fix: ROOT_CAUSE_ANALYSIS.md (last row). What was run after the fix:

* The API, comparison, agent-registry and page tests (**37 passed, 1 skipped**) and the browser and installer tests
  (**30 passed**), all **with python-multipart uninstalled**, as in the offline Windows runtime
  (`api-comparison-231200.xml`, `ui-installer-231200.xml`).
* *Application loads* now imports every API module: with the old upload code it fails with "pip install
  python-multipart"; with the fix it passes.
* **The person's situation, reproduced:** 23.11.2 upgraded with the first build, the application then fails to start
  with the same message; the fixed package applied over it ("Patched 23.12.0 -> 23.12.0"), the application starts,
  and the row added before the first upgrade is still there.
* **Setup repair under Wine:** an installed 23.12.0 with the old upload code (it fails under the Windows runtime with
  "pip install python-multipart"). The fixed Setup's Detect page says *"SkyGuard QA 23.12.0 is already installed.
  Setup can apply it again (repair) or verify it."* (`evidence/setup-repair-2-detect.png`); Install and Verify passed
  every check (OCR WARN under Wine) and the module now loads. Log: `test-results/setup-repair-over-broken-23.12.0-*.txt`.
* Upgrade 23.11.2 → 23.12.0 under Wine with the fixed build: every check passed (screenshots `evidence/setup-*.png`).
* The installer tests gained `test_setup_repairs_an_installed_23_12_0`. The Windows CI jobs also found one test
  that built a Python script with a Windows path inside a string (a syntax error on Windows only); the test now uses
  a forward-slash path.

## 9. Second round of fixes (reported on the first 23.12.0 package)

All of the following ran on 18 November 2026, with python-multipart uninstalled, as in the offline Windows runtime.

* **AI Agent, "Finish lab" refused with "The signed-in identity has not been verified".** Reproduced by storing the
  rule an earlier version learned from SauceDemo's help text ("Accepted usernames are: ..."); with the 23.11.2 code the
  run stops without placing the order; with the fix the checkout on the stand-in online store completes and the order is placed
  (`tests/test_ai_agent_231200.py`, 3 passed). The help text itself never becomes a rule any more, and a real
  "Logged in as ..." line still does. The SauceDemo stand-in now has SauceDemo's help text on its sign-in page.
* **AI Agent page and flows:** the agent suites, the AI Agent page in Chromium (the reported conversation up to the
  order placed, no JavaScript error), the Integration Comparison browser tests and the SauceDemo stand-in tests:
  **132 passed, 9 skipped** (`ai-agent-comparison-231200-r2.xml`). The skips are the same Windows-only and OCR-runtime
  tests as before.
* **Integration Comparison:** *Apply filters* with the reported button pattern works and no longer raises
  InvalidSelectorError; a table without an Apply button is logged as "filters as the values are typed". On a stand-in
  of the tabulizer.com demo whose Next button stays active on the last page, the capture is **complete** when the
  record count or the last page number confirms it (4 variants), and stays INCOMPLETE, with the reason, when the page
  shows neither (`test_integration_ocr_browser_23900.py`).
* **API, comparison schedule, agent and page tests:** 31 passed, 1 skipped (live IPv6)
  (`agent-api-comparison-231200-r2.xml`). **Installer and browser tests:** 31 passed
  (`ui-installer-231200-r2.xml`), apply / verify / rollback / apply again on all 14 releases.
* **Setup publisher:** the built Setup.exe carries Company "Muhammad Tufail", Copyright "Copyright (C) 2026 Muhammad
  Tufail" and Product "SkyGuard QA" (read from the file's version information). Under Wine the Welcome page shows
  "Publisher: Muhammad Tufail" (`evidence/setup-1-welcome.png`); the upgrade of 23.11.2 completed with every check
  passed except the usual OCR WARN under Wine: "Patched 23.11.2 -> 23.12.0"
  (`setup-publisher-build-23.11.2-to-23.12.0.txt`, screenshots `evidence/setup-*.png`).
* **Signing:** the optional signing step of the build was run with a throw-away self-signed test certificate
  (deleted afterwards, not used for the delivered file): the signature was written and `osslsigncode verify` read it
  back. **The delivered Setup.exe is not signed**, because no code-signing certificate was available, so SmartScreen
  still says *Unknown publisher* (KNOWN_LIMITATIONS.md).

* **Found by the Windows CI job (3.13):** `test_response_time_timeout_and_connection_failure` read a finished API run
  whose failures had no classification yet (KeyError 'classification'): the run was marked finished a moment before
  its failures were classified, which the page could also show. A run is now marked finished only after the
  classification and the Defect Review drafts are saved. API and comparison tests after the change: 27 passed,
  1 skipped.

Not verified: saucedemo.com and tabulizer.com themselves (the network refused both; stand-ins with the same
behaviour were used) and SmartScreen on a real Windows PC.

