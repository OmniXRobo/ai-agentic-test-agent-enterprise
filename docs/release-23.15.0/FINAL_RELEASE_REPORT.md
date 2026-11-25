# Final release report: SkyGuard QA 23.15.0

**Deliverable:** `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip`: the Setup wizard
(`SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`), `Manual_Install\` (the same upgrade without the .exe), the
release documents and the test results. Upgrades 23.3.35 to 23.14.1 (21 releases) to 23.15.0 offline, without
administrator rights, with backup, automatic restore on failure and rollback. Browser extension 1.5.0 (same ID).

## What it fixes and adds

* Create TFS Defect from Vulnerability Scanning, Execution History (failed and stopped runs) and Performance Results
  (passed or failed): no more "Request validation failed"; readable body; screenshots and report files attached;
  the bug really created in TFS ("Approve and send now", or Defect Review).
* The AI Agent is the **Workflow Operator**: one preview then "yes"; no stop at WAITING; values from teaching and
  from your last run; many records from one request; usual requests remembered; the password and authorization code
  typed while teaching kept in the Windows Credential Manager, masked; one-time codes never kept.
* Voice: misspelled Arabic understood and answered in Arabic; "English and Arabic" detects Arabic; Listen always in
  the voice popup and the extension.
* Knowledge database schema 3; main database unchanged; no new dependency; nothing downloaded.

## Tested

* Full regression: 1,200 passed; 3 tests that described the old behaviour updated and passing; 441 skipped.
* 49 new feature tests; the installer on every earlier release (see TEST_REPORT.md).
* Security: bandit, no high finding; secrets proven absent from the knowledge database (SECURITY_REPORT.md).

## Needs checking on your PC

One defect on your real TFS, the microphone with Arabic speech, and teaching your transport application. See
`KNOWN_LIMITATIONS.md`.
