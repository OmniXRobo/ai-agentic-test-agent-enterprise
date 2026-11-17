# Final release report: SkyGuard QA 23.11.0

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | The cumulative upgrade from 23.3.35, or any later release up to 23.10.0, to 23.11.0. Contents: `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` (wizard: Welcome → Detect → Backup → Install → Verify → Finish), `Manual_Install` (the same upgrade without the .exe), `README_FIRST.txt`, the documents, the SBOM, test results and `SHA256SUMS.txt`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Extension and companion 1.2.0, with the fixed companion installer (the "Could not find a part of the path" error). |
| `.sha256` files | SHA-256 of each zip. |

No administrator rights or internet are needed. Your data is backed up first and never deleted. A failed step
restores the previous version automatically, and `ROLLBACK_PATCH.cmd` undoes the upgrade later.

## The request, item by item

| # | Requirement | Status |
|---|---|---|
| 1 | Editable Python test with toolbar, editor features, versions; saved version runs and downloads; steps and TFS links intact; regenerate warns; authorized users only | **Done and tested** (browser and API) |
| 2 | Global Smart Wait: settings, same policy in runner and exported Python, bulk insert/remove/update without duplicates, "+ Wait" with 8 types, one shared service for recorder, runner and Python, busy-page detection, timeout reporting, no sleeps-only, no monkey-patching | **Done and tested.** Limits in KNOWN_LIMITATIONS.md (WebSocket, custom loaders, recorder 2.5-second window) |
| 3 | Integration Comparison → TFS "Unexpected Server Error" | **Root cause found and fixed** (`exc.code` vs `error_code`). Uses TFS Preferences, full evidence, confirmed mismatches only. Tested against a stand-in TFS |
| 4 | AI-Driven failures → Draft, Needs Review, notified, run stays Failed | **Done and tested** |
| 5 | Guided failures (recorded, edited Python, data-driven, bulk, scheduled) → drafts with full evidence, no duplicates | **Done and tested** |
| 6 | Classification into 6 classes with confidence and evidence, no invented values, recommendations only with approval, reruns keep the original | **Done.** Rules tested; the local-AI path tested with a stub (no live Qwen here) |
| 7 | One Defect Review UI with 8 statuses and the listed actions; permissions; never publish without approval | **Done and tested**, including in a browser |
| 8 | Shared TFS publishing: validation, real ID, URL and time, correlation ID, retry, lost-response reconciliation, success only after confirmation | **Done and tested** against a stand-in TFS that drops a response after creating the item |
| 9 | Evidence: screenshots, redaction, link to an existing defect | **Done and tested** |
| 10 | Security and offline | See SECURITY_REPORT.md. The Python editor's validation is a guard, not a sandbox, so edit rights are limited to Admin and Tester |
| 11 | Installer: one Setup.exe in one zip, wizard, no admin, rollback | **Done.** Windows CI over 23.3.35, 23.9.0 and 23.10.0; Wine walkthrough over 23.10.0 |
| 12 | Real testing, honest reporting | TEST_REPORT.md. Two flaky timing tests were found and fixed; the runs are reported as they happened |
| 13 | Deliverables | This folder |

## Not verified

* Windows 11 itself (Windows Server 2025 runners and Wine were used).
* A real TFS server (a stand-in was used). **Before rolling out, send one approved draft to your TFS** and check the
  bug, its attachments and its tag.
* A live local Qwen.
* SSO, MFA and CAPTCHA applications.

## Behaviour change to know about

The "automatic" defect mode no longer creates TFS bugs by itself. It prepares a draft, and someone with the approver
role approves it and presses **Send to TFS**.
