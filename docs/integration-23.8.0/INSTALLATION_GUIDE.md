# SkyGuard QA 23.8.0 cumulative patch: installation and rollback

**Package:** `SkyGuard_Integration_Comparison_Workspace_Cumulative_Patch_23.8.0.zip`. Check it against
`SkyGuard_Integration_Comparison_Workspace_Cumulative_Patch_23.8.0.zip.sha256`.

**Installs on:** SkyGuard QA **23.7.2** or **23.7.1**, and also 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1 or 23.7.0. It
also applies again over 23.8.0. One package covers 23.4.0 to 23.8.0. The browser extension update (1.1.0) is in the
same zip, unchanged from 23.7.2 (see *previous-23.7.2/INSTALLATION_GUIDE.md*, section 5).

**Requirements:** no administrator rights (run it as the Windows user who installed SkyGuard QA), no internet, no new
Python packages, about 300 MB free disk space plus the size of your database (the installer checks this).

## 1. Check the download

PowerShell, in the download folder:

```powershell
Get-FileHash .\SkyGuard_Integration_Comparison_Workspace_Cumulative_Patch_23.8.0.zip -Algorithm SHA256
Get-Content .\SkyGuard_Integration_Comparison_Workspace_Cumulative_Patch_23.8.0.zip.sha256
```

The two values must be the same. Then extract the zip (right-click, **Extract All**).

## 2. Apply

Double-click **`APPLY_PATCH.cmd`** in the extracted folder. It:

1. finds the installation (or use `APPLY_PATCH.ps1 -InstallDir "D:\Apps\AIAgenticTestAgent"`) and stops SkyGuard QA;
2. checks every file against `PATCH_MANIFEST.json` (SHA-256), disk space and write access;
3. backs up the files it replaces, the database, the agent knowledge database and `settings.json` to
   `patch-backups\<old>-to-23.8.0-<time>`;
4. copies the files;
5. migrates the main database to **19 migrations** (migration 19 adds four new tables for the Integration Comparison
   Workspace; nothing existing is changed);
6. verifies: version, Operations Agent, component engine, step reader, **Integration Comparison (23.8.0)**, passive
   learning engine, both databases (migrations, integrity, comparison tables) and that no table lost rows;
7. **if any step fails**, puts back the previous files and database automatically and says so;
8. starts SkyGuard QA and checks `/api/health`.

Afterwards press **Ctrl+F5** once in the browser. The left menu now shows one **Integration Comparison** entry
instead of the separate comparison pages.

## 3. Verify at any time

Double-click **`VERIFY_PATCH.cmd`**. Every line should say PASS.

## 4. Roll back

Double-click **`ROLLBACK_PATCH.cmd`**. This puts back the version you had before from the latest backup and removes
the files the patch added. The databases are kept: the four new `cmp_ws_*` tables stay and older versions ignore
them. For the exact database copy taken before the upgrade, run `ROLLBACK_PATCH.cmd -RestoreDatabase` (the current
database is kept next to it, renamed).

## 5. First use

See **USER_GUIDE.md** in this folder.
