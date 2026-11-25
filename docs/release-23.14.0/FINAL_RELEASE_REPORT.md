# Final release report: SkyGuard QA 23.14.0

**Deliverable:** `SkyGuard_QA_Enterprise_Final_Cumulative_Security_Integration_Patch.zip` (one zip): the Setup wizard
(`SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`), `Manual_Install\` (the same upgrade without the .exe), the
release documents and the test results. Upgrades 23.3.35 to 23.13.3 (19 releases) to 23.14.0 offline, without
administrator rights, with backup, automatic restore on failure and rollback.

## What is in it

* Security Testing (8 screens), Red Hat Fuse Integration Testing (8 tabs), ActiveMQ Classic and Artemis testing (8 tabs).
* Analyze with AI, Create TFS Defect, Generate PDF Report, Email Developer, View Evidence and Retest on every result.
* Email / SMTP for the intranet with Test connection and send history; offline PDF report types.
* Dashboard figures; 19 new agents in Agent Workflow, five looks and Stop / Pause / Resume.
* Voice commands for the new screens in English and Arabic; voice tests. The working Arabic voice is kept and verified.
* Migration 23. No new dependency. Nothing downloads.

## Tested

* Full regression: **0 failures** (1,135 passed after one expected test update; 359 environment / old-version skips).
* New tests: 24 module tests (real ActiveMQ Classic 5.18.6 and Artemis 2.37.0), 17 browser tests, 7 Agent Workflow
  tests, 38 installer tests on all 19 earlier releases.

## Needs checking on the intranet

Real Red Hat Fuse (only a simulator and a route stand-in were used), real OWASP ZAP (fake API), the real SMTP server
(test transport), Microsoft Edge itself (Chromium was used), Windows Setup (CI on the pull request), the microphone and
Arabic transcription (no audio device here). See `KNOWN_LIMITATIONS.md` and `COMPATIBILITY_MATRIX.md`.
