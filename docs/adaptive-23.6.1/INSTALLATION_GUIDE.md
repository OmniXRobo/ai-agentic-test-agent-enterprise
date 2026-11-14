# SkyGuard QA 23.6.1 cumulative patch: installation and rollback

**Package:** `SkyGuard_Guided_Playback_Components_Fix_Patch_23.6.1.zip`. Check it against
`SkyGuard_Guided_Playback_Components_Fix_Patch_23.6.1.zip.sha256`.

**Installs on:** SkyGuard QA **23.3.35, 23.4.0, 23.5.0 or 23.6.0**. It also applies again over 23.6.1. One package
covers 23.4.0, 23.5.0, 23.6.0 and 23.6.1.

**Requirements:**
- No administrator rights. Run it as the Windows user who installed SkyGuard QA. Everything is written inside the
  installation folder (by default `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`).
- No internet. Nothing is downloaded, and no new Python packages are needed.
- About 300 MB free disk space plus the size of your database (the installer checks this).

## 1. Check the download

PowerShell, in the download folder:

```powershell
Get-FileHash .\SkyGuard_Guided_Playback_Components_Fix_Patch_23.6.1.zip -Algorithm SHA256
Get-Content .\SkyGuard_Guided_Playback_Components_Fix_Patch_23.6.1.zip.sha256
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
     `patch-backups\<old>-to-23.6.1-<time>`;
   - copies the files;
   - migrates the main database to 18 migrations (from 23.3.35 this adds the 23.4.0 migrations) and creates or migrates
     `data\opsagent\knowledge.sqlite3`;
   - verifies: version, component engine, Operations Agent, migrations, `PRAGMA integrity_check`, and no table lost
     rows;
4. **if any step fails**, puts back the previous files and database automatically and says so;
5. starts SkyGuard QA and checks `/api/health`.

Afterwards, press **Ctrl+F5** once in the browser. A log is written to `logs\patch-23.6.1-<time>.txt`.

## 3. Verify at any time

Double-click **`VERIFY_PATCH.cmd`**. Every line should say PASS.

## 4. Roll back

Double-click **`ROLLBACK_PATCH.cmd`**. This puts back the version you had before (23.3.35, 23.4.0, 23.5.0 or 23.6.0) from the
latest backup, and removes the files the patch added.

Your databases are kept as they are, because older versions read them unchanged. From 23.3.35 the 23.4.0 tables and
columns stay; 23.3.35 does not use them.

For the exact database copy taken before the upgrade, run `ROLLBACK_PATCH.cmd -RestoreDatabase`. The current
database is kept next to it, renamed.

## 5. After installing

1. **Your Trip.com recording made with 23.6.0 plays as it is.** A click on a city suggestion that was stored before the
   typed text (step 9 in your run, `[data-testid="0"]`) is now played as "type *New Delhi* into the field, then choose
   *New Delhi - All airports India*", and the typing step after it is marked as already done. Nothing to change.
2. **Optional: Semantic upgrade.** Open the script and press **Semantic upgrade** to write this into the script (the
   typing moves in front of the suggestion, and the click becomes a "Choose ... (typed ...)" step). Saving creates a
   new version; the old one stays in the version list.
3. **Passenger, room and guest + / - buttons recorded with 23.6.0** are played as recorded (one press per step). Record
   that part again (or the whole flow) to get the new "Set Children to 1" steps, which find the counter by its label and
   press + or - until it shows the number, whatever it showed when the run started.
4. **New recordings** get all of this automatically: typing is recorded before the suggestion it produced, suggestion
   rows and custom lists are recorded as "Choose ...", counters as "Set ... to N", calendar days as date rules.
5. Press **Ctrl+F5** once in the browser after the patch, so the new recorder script is loaded.
