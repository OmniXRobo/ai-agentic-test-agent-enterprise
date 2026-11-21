# Final release report: SkyGuard QA 23.13.0 (cumulative from 23.3.35) and extension 1.4.1

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | One cumulative, offline upgrade to 23.13.0 for any release from 23.3.35 to 23.12.0. It holds `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` (Welcome → Detect → Backup → Install → Verify → Finish), `Manual_Install` (`APPLY_PATCH.cmd`, `VERIFY_PATCH.cmd`, `ROLLBACK_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results, evidence screenshots and `SHA256SUMS.txt`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Extension and companion 1.4.1. `UPDATE_EXTENSION.cmd` updates an installed one; `INSTALL.cmd` is for new installs. |
| `SkyGuard_QA_User_Manual.pdf` | The user manual with screenshots (also inside the patch, and attached to each welcome email). |
| `.sha256` files | The SHA-256 of each zip. |

No internet is needed on the office PC. Your data is backed up first and kept; if any step fails, the previous version
is restored automatically.

## What was asked and what is in this release

| # | Asked | Status |
|---|---|---|
| 1 | Edge for AI-Driven, Discovery, Hybrid when preferred | **Done.** Preferences win; a browser that is not installed falls back to Edge. |
| 2 | Dashboard: charts first, AI report last, more charts, drill-down | **Done.** 3 new charts; every slice, bar and day opens the items behind it. |
| 3 | Arabic / UAE Arabic voice; male voice answered with a female voice | **Done**, with one condition: Arabic *speech* needs the multilingual Faster-Whisper model on the PC (KNOWN_LIMITATIONS.md). Typed Arabic works now. Gender bug fixed. |
| 4 | Defect Review "Assign to" from TFS, all defect types | **Done.** |
| 5 | Guided Browser: version history last, bigger Python area, tabs | **Done.** |
| 6 | API Testing Open button; offline TLS false FAIL | **Done.** Windows-trusted certificates are accepted; verification stays on. |
| 7 | HTTPS with a friendly name, offline | **Done.** Users > HTTPS; trust once per PC. |
| 8 | AI Agent: lifecycle dropdown, account on public sites, dynamic URLs, one browser, Arabic; the extension too | **Done.** One more bug found and fixed while testing: confirming a chat task opened a second, empty browser. |
| 9 | Users: mandatory personal PAT and project, email, welcome email with manual | **Done.** The emailed password must be changed at the first sign-in (security review). |
| 10 | Generate AI result / Create TFS defect / Send email (PDF) on the testing pages | **Done** on 15 pages. |
| 11 | Analytics AI + PDF email | **Done.** |
| 12 | Healing kept (Guided, Discovery, Hybrid, AI agent), dynamic URLs | **Done.** |
| 13 | Patch on the latest version, security check, offline | **Done.** Built on 23.12.0; SECURITY_REPORT.md. |

## Test results (TEST_REPORT.md)

* New features: 15 passed; pages in a real browser: 4 passed, no JavaScript errors.
* Full suite: 1043 passed, 1 failed (a real bug, fixed, and its tests run again and passed), 261 skipped.
* Installer: apply, verify, rollback and apply again from all 15 earlier releases (23.3.35 to 23.12.0), repair,
  detect, damaged files, OCR runtime and protected folders.

## Security (SECURITY_REPORT.md)

bandit: no injectable query; SHA-1 only as a certificate identifier. pip-audit: no known vulnerabilities. No new
dependency. Five findings fixed in review (forced password change after the welcome email, PowerShell quoting,
duplicate check kept for *Approve and send now*, checks on what a page under test can report, SHA-1 marking).
Residual risks are listed (local CA key on disk, hand-written certificate code, shared hosting domains, password in
email).

## Not verified here

A real Windows PC with Edge, a real TFS server and Outlook account, a running local Qwen, Arabic speech. Please run
one AI-Driven generation, one Discovery and one Hybrid run in Edge, create one user with a real PAT, and turn HTTPS on
once after installing.
