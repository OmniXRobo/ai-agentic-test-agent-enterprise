# Test report: SkyGuard QA 23.13.0 and extension 1.4.1

Every result below comes from a run that actually happened on 20 November 2026. The JUnit files and logs are in
`test-results/`. Nothing below is claimed from reading code alone.

Environment: Linux container (Python 3.13, pytest, Playwright with Chromium in new-headless mode), NSIS 3 for
Setup.exe. A real Windows PC, Microsoft Edge, Google Chrome, a TFS server and a running local Qwen were not
available (section 6).

## 1. New features of 23.13.0

`tests/test_release_231300.py`: **15 passed** (`new-features-231300.xml`).

| Test | What it proves |
|---|---|
| `test_edge_is_used_when_chromium_is_missing` | With Edge chosen in Preferences, a request for "chromium" uses Edge; when the included Chromium is not installed, Edge is used and the switch is logged. |
| `test_exact_launch_never_switches` | A browser that is installed but fails is reported, never replaced. |
| `test_dynamic_addresses_match_and_static_ones_do_not` | Addresses with changing IDs, dates and tokens match their pattern; different pages do not. |
| `test_same_site_accepts_subdomains_but_not_other_sites` | `www.` / `booking.` of the same domain are the same application; another domain, IPs and look-alikes are not. |
| `test_public_sites_need_no_account` | A public application runs a task without asking for an account. |
| `test_arabic_commands_are_translated_offline` | Arabic and UAE Arabic commands become the English command (word list, no AI running). |
| `test_assignees_come_from_tfs_and_reach_the_defect` | The "Assign to" list comes from the TFS teams and WIQL (groups left out), reaches `System.AssignedTo`; *Approve and send now* sends once and the same defect is not sent twice. |
| `test_each_account_has_its_own_tfs_project_and_pat` | An invalid PAT stops the account; each person's TFS data uses their own project and PAT; caches are separate; deleting the user deletes the PAT. |
| `test_welcome_email_carries_the_address_and_the_manual` | Address, user name, password and the manual PDF go to the user's email; the account must change its password; without email set up the account is still made and the reason is shown; an invalid email is refused. |
| `test_https_certificates_are_made_offline_and_trusted_by_name` | The CA and server certificate made with the standard library pass a verified TLS handshake by name. |
| `test_https_settings_api_and_the_friendly_name_is_accepted` | The HTTPS settings API, name validation, port check, and the Host check accepting the configured name. |
| `test_module_reports_have_facts_and_a_pdf_without_ai` | Module facts and a PDF without the AI running. |
| `test_dashboard_insights_feed_the_new_charts` | Defects by module, API results and healing figures for the new charts. |
| `test_api_testing_trusts_the_system_store_and_its_own_ca` | System store, public bundle and the application's CA are all trusted; verification stays on. |
| `test_arabic_reply_uses_an_arabic_voice_of_the_same_gender` | A male voice stays male, also for Arabic replies. |

Also: `tests/test_automation.py` **new** `test_healed_locator_is_kept_so_the_next_run_does_not_heal_again` (passed),
`tests/test_extension_130.py` (1.4.1, passed).

## 2. Pages in a real browser

`tests/test_ui_231300.py`: **4 passed, no JavaScript errors** (screenshots in `evidence/231300-*.png`):
the Dashboard (charts above the key figures, AI analysis last, at least 10 chart cards), the report bar on API Testing,
Analytics, Load Tests, Guided Browser and Integration Comparison (and not on Users), the *Create TFS defect* form,
the Users form (email, TFS project and PAT required, HTTPS card) and the Guided Browser tabs (all 11, Credentials
tab, version history below the script). The user manual was built from the running application: 15 screenshots
(`evidence/*.jpg`) in `web/manual/SkyGuard_QA_User_Manual.pdf`.

## 3. Full suite on Linux

`tests/` with 4 workers: **1043 passed, 1 failed, 261 skipped** (`full-suite-linux.txt`). Skips are Windows-only,
network and optional-runtime tests, as in earlier releases.

The failure was real: `test_ui_231101.py::test_checkbox_stays_ticked_and_confirm_executes`. Confirming a task asked
for in the AI Agent chat started a **second** browser for it, although the task was already waiting in the chat's
own window (new in 23.13.0, "one browser"). The second browser was empty, so the record was "not found" and nothing
was executed. Fixed in `api/agent_simple.py` and `api/opsagent.py` (a task already hosted by the chat browser is not
started again). Then: `test_ui_231101.py` 5 passed; the AI Agent, autopilot, missing-information, Ops Agent API and
end-to-end, 23.11.2 UI and the 23.13.0 tests **125 passed** (two order-dependent tests that fail when run alone, as
in 23.12.0, passed when their files ran: 52 passed).

## 4. Installer (`install_patch.py`) on every real release

`tests/test_cumulative_patch_231300.py` with the offline OCR wheels (`installer.xml`): apply, verify, roll back and
apply again from **15 of 15 releases**, 23.3.35 to 23.12.0; repair over 23.13.0; detect; damaged files and wheels
refused; failure after copying rolls back; OCR runtime staged, swapped and rolled back; protected folder never forced
(side by side works); no network and no administrator needed. **28 passed, 2 failed** on the first run: the patch
was built before this report existed (`test_patch_contents` looks for it), and the test used 23.13.0 as an
"unknown" version although it is now this release; it now uses 23.14.0. Both were run again: section 4a.

### 4a. Run again

`test_patch_contents` and `test_wrong_version_is_refused`: **2 passed** (`installer-rerun.xml`). Installer total:
**30 of 30 passed**.

## 5. Security tools

bandit 1.9.4 and pip-audit 2.10.1: SECURITY_REPORT.md section 1.

## 6. Not executed

* A real Windows 10 / 11 PC, Microsoft Edge, Google Chrome, Windows voices, Windows Credential Manager,
  `ENABLE_HTTPS.cmd` / `TRUST_SKYGUARD_QA.cmd` (certutil, hosts file, firewall).
* A real TFS server (a fake Azure DevOps API stands in), a real Outlook account, a running local Qwen, Arabic
  speech recognition (no multilingual model here).
* Setup.exe under Wine was not repeated for this release; the installer logic it runs is the same
  `install_patch.py` tested in section 4.
