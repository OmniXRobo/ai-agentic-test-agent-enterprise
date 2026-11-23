# Final release report: SkyGuard QA 23.14.1

**Deliverable:** `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip`: the Setup wizard
(`SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`), `Manual_Install\` (the same upgrade without the .exe), the
release documents and the test results. Upgrades 23.3.35 to 23.14.0 (20 releases) to 23.14.1 offline, without
administrator rights, with backup, automatic restore on failure and rollback. The browser extension is unchanged
(1.4.3); no extension update is needed.

## What it fixes

* TFS slow on every TFS page for accounts with their own PAT and project: connections are reused, the PAT and project
  are read once, duplicate queries are merged, project reads and counts run in parallel.
* Dashboard stuck on "Loading...": it shows at once and waits at most 8 s for TFS; slow TFS figures fill in later.
* Connection Test: same six checks, quicker.
* Background work (test design, test data, scripts, voice, reports, defects, agents) uses the signed-in person's PAT
  and project, not the administrator's.
* All 23.14.0 features kept. No migration, no new dependency. The Arabic voice and speech model are kept (verified).

## Tested

* Full regression: 1,182 passed; 1 failure in a new installer test (an example version that became this release),
  fixed and passing; 402 skipped.
* 15 new TFS speed tests; 40 installer tests on all 20 earlier releases including 23.14.0.

## Needs checking on your PC

The speed with your real Azure DevOps organization. See `KNOWN_LIMITATIONS.md`.
