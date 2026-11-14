# SkyGuard QA 23.7.1 cumulative patch: installation and rollback

**Package:** `SkyGuard_Trip_Playback_Fix_Cumulative_Patch_23.7.1.zip`. Check it against
`SkyGuard_Trip_Playback_Fix_Cumulative_Patch_23.7.1.zip.sha256`.

**Installs on:** SkyGuard QA **23.7.0** (your version), and also **23.3.35, 23.4.0, 23.5.0, 23.6.0 or 23.6.1**. It also applies again over 23.7.1. One package
covers 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0 and 23.7.1. The same zip updates the SkyGuard browser extension and its companion to
**1.1.0** (optional, see step 5).

**Requirements:**
- No administrator rights. Run it as the Windows user who installed SkyGuard QA. Everything is written inside the
  installation folder (by default `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`) and, for the extension, inside
  `%LOCALAPPDATA%\SkyGuardAgent`.
- No internet. Nothing is downloaded, and no new Python packages are needed.
- About 300 MB free disk space plus the size of your database (the installer checks this).
- The local AI model is optional for learning. If you use one, see *previous-23.7.0/DESIGN_AND_LLM_DECISION.md* for the recommended
  size.

## 1. Check the download

PowerShell, in the download folder:

```powershell
Get-FileHash .\SkyGuard_Trip_Playback_Fix_Cumulative_Patch_23.7.1.zip -Algorithm SHA256
Get-Content .\SkyGuard_Trip_Playback_Fix_Cumulative_Patch_23.7.1.zip.sha256
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
     `patch-backups\<old>-to-23.7.1-<time>`;
   - copies the files;
   - keeps the main database at 18 migrations (from 23.3.35 this adds the 23.4.0 migrations; 23.5.0 to 23.7.1 add
     none) and creates or migrates `data\opsagent\knowledge.sqlite3` to schema 2 (two new tables: the observation
     journal and the learned application model; existing knowledge is kept);
   - verifies: version, Operations Agent, component engine, passive learning engine, both databases (migrations,
     `PRAGMA integrity_check`), and that no table lost rows;
4. **if any step fails**, puts back the previous files and database automatically and says so;
5. starts SkyGuard QA and checks `/api/health`.

Afterwards, press **Ctrl+F5** once in the browser. A log is written to `logs\patch-23.7.1-<time>.txt`.

## 3. Verify at any time

Double-click **`VERIFY_PATCH.cmd`**. Every line should say PASS.

## 4. Roll back

Double-click **`ROLLBACK_PATCH.cmd`**. This puts back the version you had before (23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1 or 23.7.0)
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

See **previous-23.7.0/USER_GUIDE.md**. In short: Operations Agent > Learning > **Learn while I browse**, then use the application
normally; or tick the extension option above and just work in your browser.

## 7. After installing: play your Trip.com recording again

1. Open `http://127.0.0.1:8765/api/health` and check that it shows `"version": "23.7.1"`.
2. Open your script (for example SCR-9D41509DEA) in Guided Browser Automation. You do not have to record it again:
   - the departure city you typed without clicking a suggestion is now chosen from the list
     ("DXB Dubai International Airport" for "dubai international airport");
   - the click on the suggestion `[data-testid="0"]` is played as "type, then choose this airport";
   - the two calendar days are chosen by their date rule (TODAY+8d, TODAY+14d), whatever price the day shows today;
   - the click on the whole "Adults 12+ years old 1" row is played as Adults **+** (set to 2), because Trip.com's
     "-" is disabled at 1 adult.
3. Optional: click **Semantic upgrade**, then **Save**. This writes the suggestion clicks as "Choose ... (typed ...)"
   steps. The step editor now keeps these steps when you save.
4. To set a number of passengers yourself, add a step with Action **set_count** and Value **Adults=2** (or
   Children=1, Infants=1). It needs the passengers pop-up to be open, so put it after the click on
   "1 adult, Economy".
5. New recordings made with 23.7.1 record the + and - as "Set Adults to 2" directly.

## 8. If a step still fails on Trip.com: run the site diagnostic

Trip.com cannot be opened from our build machine, so this tool records what the real page looks like on your PC.

1. Double-click `DIAGNOSE_SITE.cmd` in the extracted patch folder (after `APPLY_PATCH.cmd`). Microsoft Edge opens on
   trip.com flights. To use another city or browser: `DIAGNOSE_SITE.ps1 -City "Dubai" -Browser chrome`.
2. The window asks you for five steps, one at a time. Do each step in the browser, then press **Enter** in the window:
   1. close any banner so the flight search box is visible;
   2. click once into the departure field (do not type);
   3. type the city (Manila) and wait for the airport list;
   4. choose the city and an arrival city, then open the departure calendar;
   5. choose two dates, then open the passengers list.
   Type `s` to skip a step or `q` to finish early.
3. When it says **Done**, send back the file `SkyGuard_SiteDiagnostic_<date>.zip` from your Desktop.

It saves screenshots, the accessibility tree, the page HTML (without scripts or passwords), what the automation's
component engine finds at each step, and your newest Trip.com scripts with their last run results. It never clicks or
types by itself and never books. Cookies and saved sign-ins are not collected.
