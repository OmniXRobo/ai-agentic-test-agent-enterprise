# SkyGuard QA 23.6.0 cumulative patch: installation and rollback

**Package:** `SkyGuard_Adaptive_Browser_Learning_BugFix_Final_Patch.zip`. Check it against
`SkyGuard_Adaptive_Browser_Learning_BugFix_Final_Patch.zip.sha256`.

**Installs on:** SkyGuard QA **23.3.35, 23.4.0 or 23.5.0**. It also applies again over 23.6.0. One package covers
23.4.0, 23.5.0 and 23.6.0.

**Requirements:**
- No administrator rights. Run it as the Windows user who installed SkyGuard QA. Everything is written inside the
  installation folder (by default `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`).
- No internet. Nothing is downloaded, and no new Python packages are needed.
- About 300 MB free disk space plus the size of your database (the installer checks this).

## 1. Check the download

PowerShell, in the download folder:

```powershell
Get-FileHash .\SkyGuard_Adaptive_Browser_Learning_BugFix_Final_Patch.zip -Algorithm SHA256
Get-Content .\SkyGuard_Adaptive_Browser_Learning_BugFix_Final_Patch.zip.sha256
```

The two values must be the same. Then extract the zip (right-click, **Extract All**).

## 2. Apply

Double-click **`APPLY_PATCH.cmd`** in the extracted folder. It runs `APPLY_PATCH.ps1`, which:

1. finds the installation (or use `-InstallDir "D:\Apps\AIAgenticTestAgent"`);
2. stops SkyGuard QA if it is running (only processes started from that folder);
3. runs `install_patch.py` with SkyGuard's own Python:
   - checks every file against `PATCH_MANIFEST.json` (SHA-256);
   - checks disk space and write access;
   - backs up the files it replaces, the database, the agent knowledge database and `settings.json` to
     `patch-backups\<old>-to-23.6.0-<time>`;
   - copies the files;
   - migrates the main database to 18 migrations (from 23.3.35 this adds the 23.4.0 migrations) and creates or migrates
     `data\opsagent\knowledge.sqlite3`;
   - verifies: version, component engine, Operations Agent, migrations, `PRAGMA integrity_check`, and no table lost
     rows;
4. **if any step fails**, puts back the previous files and database automatically and says so;
5. starts SkyGuard QA and checks `/api/health`.

Afterwards, press **Ctrl+F5** once in the browser. A log is written to `logs\patch-23.6.0-<time>.txt`.

## 3. Verify at any time

Double-click **`VERIFY_PATCH.cmd`**. Every line should say PASS.

## 4. Roll back

Double-click **`ROLLBACK_PATCH.cmd`**. This puts back the version you had before (23.3.35, 23.4.0 or 23.5.0) from the
latest backup, and removes the files the patch added.

Your databases are kept as they are, because older versions read them unchanged. From 23.3.35 the 23.4.0 tables and
columns stay; 23.3.35 does not use them.

For the exact database copy taken before the upgrade, run `ROLLBACK_PATCH.cmd -RestoreDatabase`. The current
database is kept next to it, renamed.

## 5. After installing

1. **Recorded scripts made before 23.6.0:** open the script and press **Semantic upgrade**. It lists the calendar,
   option, pop-up and hover steps it will turn into date, option and close steps. Saving creates a new version, so the
   old one stays in the version list. Recording again also works; new recordings get these steps automatically.
2. **Recorded dates** become relative rules (for example "today + 7 days") so they do not fall in the past. A recorded
   date that is already in the past is reported, not changed silently.
3. **Visible runs** started with Run in the script editor pause with the browser open when a step cannot be decided
   (two matching options, a missing date). Answer in the yellow box: choose, Continue, Retry, Skip or Stop.
4. **Payment and booking clicks** ("Pay now", "Book now", "Complete booking", …) are not made in a visible run unless
   you press "Allow this once". For unattended or scheduled runs, set `playback.guard_commits_unattended = true` in
   Settings to block them there too.
