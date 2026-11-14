# SkyGuard QA 23.7.0 cumulative patch: installation and rollback

**Package:** `SkyGuard_Autonomous_Application_Learning_Final_Patch.zip`. Check it against
`SkyGuard_Autonomous_Application_Learning_Final_Patch.zip.sha256`.

**Installs on:** SkyGuard QA **23.3.35, 23.4.0, 23.5.0 or 23.6.0**. It also applies again over 23.7.0. One package
covers 23.4.0, 23.5.0, 23.6.0 and 23.7.0. The same zip updates the SkyGuard browser extension and its companion to
**1.1.0** (optional, see step 5).

**Requirements:**
- No administrator rights. Run it as the Windows user who installed SkyGuard QA. Everything is written inside the
  installation folder (by default `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`) and, for the extension, inside
  `%LOCALAPPDATA%\SkyGuardAgent`.
- No internet. Nothing is downloaded, and no new Python packages are needed.
- About 300 MB free disk space plus the size of your database (the installer checks this).
- The local AI model is optional for learning. If you use one, see *DESIGN_AND_LLM_DECISION.md* for the recommended
  size.

## 1. Check the download

PowerShell, in the download folder:

```powershell
Get-FileHash .\SkyGuard_Autonomous_Application_Learning_Final_Patch.zip -Algorithm SHA256
Get-Content .\SkyGuard_Autonomous_Application_Learning_Final_Patch.zip.sha256
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
     `patch-backups\<old>-to-23.7.0-<time>`;
   - copies the files;
   - keeps the main database at 18 migrations (from 23.3.35 this adds the 23.4.0 migrations; 23.5.0 to 23.7.0 add
     none) and creates or migrates `data\opsagent\knowledge.sqlite3` to schema 2 (two new tables: the observation
     journal and the learned application model; existing knowledge is kept);
   - verifies: version, Operations Agent, component engine, passive learning engine, both databases (migrations,
     `PRAGMA integrity_check`), and that no table lost rows;
4. **if any step fails**, puts back the previous files and database automatically and says so;
5. starts SkyGuard QA and checks `/api/health`.

Afterwards, press **Ctrl+F5** once in the browser. A log is written to `logs\patch-23.7.0-<time>.txt`.

## 3. Verify at any time

Double-click **`VERIFY_PATCH.cmd`**. Every line should say PASS.

## 4. Roll back

Double-click **`ROLLBACK_PATCH.cmd`**. This puts back the version you had before (23.3.35, 23.4.0, 23.5.0 or 23.6.0)
from the latest backup, and removes the files the patch added.

Your databases are kept as they are. The knowledge database keeps its two 23.7.0 tables; 23.5.0 and 23.6.0 open it
and ignore them (tested). From 23.3.35 the 23.4.0 tables and columns stay; 23.3.35 does not use them.

For the exact database copy taken before the upgrade, run `ROLLBACK_PATCH.cmd -RestoreDatabase`. The current
database is kept next to it, renamed.

## 5. Update the browser extension (only if you use it)

If the SkyGuard browser extension's companion is installed for your Windows user (`%LOCALAPPDATA%\SkyGuardAgent`),
`APPLY_PATCH` reminds you at the end. Then:

1. Double-click **`UPDATE_EXTENSION.cmd`**. It checks the files (SHA-256), stops the companion, backs up `app\` and
   `extension\` to `backups\update-1.1.0-<time>`, copies the 1.1.0 files, checks they load, and starts the companion.
   Your `data\` folder (accounts, knowledge, vault) is not touched. On any failure it puts the backup back.
2. Open `chrome://extensions` (or `edge://extensions`) and press the **reload** button on the SkyGuard card.
3. In the extension's **Options**, card **Learning**, tick **Learn automatically while I browse my registered
   applications**.

To undo: `UPDATE_EXTENSION.cmd -Rollback`, then reload the extension.

## 6. Turn learning on

See **USER_GUIDE.md**. In short: Operations Agent > Learning > **Learn while I browse**, then use the application
normally; or tick the extension option above and just work in your browser.
