# Installing SkyGuard QA 23.5.0 (Adaptive AI Operations Agent)

This is a cumulative patch for **SkyGuard QA 23.4.0** (the final patch of PR #41 with PR #42). It works offline, needs
no administrator rights and keeps all your data. It has to be installed by the Windows user who installed SkyGuard.

## Before you start

* SkyGuard QA 23.4.0 is installed (Settings > About shows 23.4.0). For an older version, install the 23.4.0 final
  patch first.
* At least 400 MB free on the drive that holds SkyGuard (the installer computes the exact figure and stops if there is
  not enough).
* Close SkyGuard. The patch stops it if it is still running and checks that the port no longer answers.
* No internet connection is needed. No new Python package is needed: the agent core uses the standard library and
  the Playwright, AI and speech components SkyGuard already has.

## Install

1. Extract `SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch.zip` into a new folder.
2. Optional: check the zip against `SkyGuard_..._Final_Patch.zip.sha256` (`Get-FileHash -Algorithm SHA256 <zip>`).
3. Double-click **APPLY_PATCH.cmd**. If SkyGuard is not in `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`, run:
   `powershell -ExecutionPolicy Bypass -File .\APPLY_PATCH.ps1 -InstallDir "D:\Apps\AIAgenticTestAgent"`

What it does, step by step (every step is printed):

1. finds the installation and checks it is 23.4.0 (or 23.5.0, to apply again),
2. checks every file of the patch against `PATCH_MANIFEST.json` (SHA-256); a changed or missing file stops it,
3. checks free space, that the application and data folders can be written, and that SkyGuard is stopped,
4. backs up every file it replaces, the database (SQLite online backup), the agent's knowledge database if present,
   and settings.json, to `patch-backups\23.4.0-to-23.5.0-<time>\`,
5. copies the new files (each to a temporary name, then renamed),
6. creates the agent's knowledge database `data\opsagent\knowledge.sqlite3` (schema 1). SkyGuard's main database
   is not changed (still 18 migrations),
7. verifies: version, the application loads, the agent loads, both databases pass `PRAGMA integrity_check`, all
   migrations recorded, no table lost rows, all patch files present,
8. if any step fails, restores the previous files and database automatically and says what failed,
9. starts SkyGuard and waits for `/api/health`.

## Check an installation

**VERIFY_PATCH.cmd** runs the checks of step 7 at any time and prints PASS or FAIL for each.

## Roll back

**ROLLBACK_PATCH.cmd** puts back the 23.4.0 files from the latest backup and removes the files the patch added.
The databases are kept: 23.4.0 does not use the knowledge database, and the main database was not changed, so
nothing is lost. `ROLLBACK_PATCH.cmd -RestoreDatabase` also puts back the database copy taken before the upgrade
(the current one is kept next to it, renamed). Applying the patch again after a rollback works and keeps the
knowledge learned before.

## After installing

1. Sign in to SkyGuard as usual. The left menu has **AI Operations Agent**.
2. AI: the agent uses SkyGuard's On-Premise Qwen (Preferences > AI). If AI is off, requests are still understood by
   the built-in parser for the patterns it knows, and nothing that needs AI is offered.
3. Voice: uses the speech recognizer configured on the Voice page.
4. Saved application passwords use Windows Credential Manager, as SkyGuard's saved logins do.

## Files

| In the zip | What |
| --- | --- |
| `app\` | the application files changed or added since 23.4.0 (qa_platform, web, skyguard_agent) |
| `PATCH_MANIFEST.json` | SHA-256 of every file in `app\` |
| `APPLY_PATCH.cmd/.ps1`, `VERIFY_PATCH.cmd`, `ROLLBACK_PATCH.cmd`, `install_patch.py` | installer |
| `tests\` | the agent's automated tests (run from a source checkout) |
| `*.md` | this guide, user guide, changelog, compatibility, security audit, test reports, release notes |
| `SHA256_CHECKSUM.txt` | SHA-256 of every file in the package |

## Troubleshooting

| Message | What to do |
| --- | --- |
| `This patch installs on 23.4.0 (found ...)` | Install the 23.4.0 final patch first. |
| `The patch folder is damaged or changed` | Extract the zip again into an empty folder. |
| `SkyGuard QA is still running` | Close it (tray icon > Exit), then run the patch again. |
| `The folder ... cannot be written` | Run as the Windows user who installed SkyGuard. Administrator rights do not help and are not needed. |
| `PATCH FAILED: ... the previous version was restored` | Nothing was lost. Send `patch-backups\...\` and the window text to support. |
| The agent page says *The AI Operations Agent did not start* | See `data\logs\qa_platform.log`, line `AI Operations Agent did not start`. SkyGuard itself keeps working. |
