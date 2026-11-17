# Installing SkyGuard QA 23.11.1 (AI Agent repair)

**SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch.zip** upgrades an installed SkyGuard QA to 23.11.1 in one step. The zip
has the name the release request gave it; the version it installs is 23.11.1, because the repository already held
23.11.0 (built on 23.10.0) and that work is kept (see SOURCE_ARCHITECTURE_AUDIT.md).
It works from any release between 23.3.35 and 23.11.0:

> 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0

Running it again over 23.11.1 repairs the installation. Setup refuses any other version (older than 23.3.35, newer, or
not a release) and changes nothing.

What you need:

* No administrator rights. Run Setup as the Windows user who installed SkyGuard QA.
* No internet. Everything is in the zip.
* Windows 10 version 1909 or later, or Windows 11. The scripts use Windows PowerShell 5.1, which comes with Windows.
* About 700 MB of free disk space. The OCR runtime is about 270 MB once unpacked, and the previous one is kept for
  rollback. Setup checks this before it starts.

## 1. Check and extract the zip

1. In a command prompt, run `certutil -hashfile SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch.zip SHA256`. The value
   must match the one in `SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch.zip.sha256`.
2. Extract the whole zip (right-click, *Extract All*) to a folder of your own, for example `Downloads`.
3. Close SkyGuard QA. If it is still running, Setup offers to close it.

## 2. Run Setup

Double-click **SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe**.

> Setup.exe is not code-signed (no company certificate was available to the build). Windows SmartScreen may show
> *Windows protected your PC*. Choose *More info*, then *Run anyway*. If your company blocks unsigned programs, use
> the manual install in section 5, which does the same thing without an .exe.

| Page | What happens |
|---|---|
| **Welcome** | Explains the upgrade. |
| **Detect** | Setup finds the installation (by default `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`; use *Browse* for another folder). With the application's own Python, it reads the installed version, the data folder, whether the application folder can be written and whether SkyGuard QA is running. *Next* is available only for a supported version. For any other version, Setup says why and changes nothing. |
| **Backup** | Shows where the backup goes (`patch-backups\<from>-to-23.11.1-<time>`): every file that is replaced, the database and the agent's knowledge database (consistent SQLite copies), and `settings.json`. If SkyGuard QA is running, it is closed after you agree. If the application folder is protected, Setup installs a patched copy beside it (section 4). |
| **Install** | Checks the SHA-256 of every file and every OCR wheel, checks free space and permissions, backs up, copies, installs the offline OCR runtime (unpacked into a staging folder and swapped in when complete), migrates, and verifies. Every message is shown and written to `logs\setup-23.11.1-<time>.txt`. **If any step fails, the previous files, the previous OCR runtime and the database copy are restored automatically.** |
| **Verify** | Runs the verification again and shows every check: version, application loads, Operations Agent, component engine, step reader, Integration Comparison 23.8.0 and 23.9.0, AI Agent and guided recording (23.10.0), Python editor, Smart Wait, Defect Review (23.11.0), **AI Agent repair (23.11.1)**, maximized browser policy, offline OCR engine probe, passive learning engine, knowledge database (with the `conversation_state` table), migrations (20), review and Python tables, integrity, comparison tables, no data lost, patch files. |
| **Finish** | Starts SkyGuard QA (optional) and opens the log (optional). In the browser, press Ctrl+F5 once. |

Setup also copies `install_patch.py`, `ROLLBACK_PATCH.cmd` and `VERIFY_PATCH.cmd` to
`<installation>\patch-tools\23.11.1`, so you can verify or roll back later without the zip.

### Unattended (IT, scripts)

```
SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe /S /APP="C:\Users\me\AppData\Local\Programs\AIAgenticTestAgent"
```

Options: `/SIDEBYSIDE` (always install a patched copy beside the original), `/NOSTART` (do not start SkyGuard QA
afterwards), `/KEEPRUNNING` (stop with exit code 4 instead of closing a running SkyGuard QA).

| Exit code | Meaning |
|---|---|
| 0 | Upgraded and verified |
| 1 | Failed; the previous version was restored (see the log in `<installation>\logs`) |
| 2 | No installation in that folder |
| 3 | Version not supported; nothing changed |
| 4 | SkyGuard QA is running and `/KEEPRUNNING` was given |

## 3. After the upgrade

* What the AI Agent learned before the upgrade is kept and used. Open **AI Agent > Applications & Knowledge** and
  press **Test learned knowledge** for each application: it checks that every learned task and page is found again
  by a command. Nothing is executed.
* In **AI Agent > AI Chat & Voice**, the suggestions above the box come from what was learned. "Navigate to ..."
  opens the page. An operation shows one short preview with **Confirm & Execute | Edit | Cancel**.
* Browser extension users: run `UPDATE_EXTENSION.cmd` from the second zip
  (`SkyGuard_SGQA-23.10.0_Edge_Chrome_Extension.zip`, extension 1.3.0), then press *Reload* on the SkyGuard card in
  `edge://extensions` or `chrome://extensions`.

## 4. A protected application folder

If SkyGuard QA was installed for all users, for example in a machine-wide programs folder, a standard user cannot
change it. Setup does not try to get around Windows permissions. It installs a patched copy in
`%LOCALAPPDATA%\SkyGuardQA\app-23.11.1`, writes `START_SKYGUARD_23.11.1.cmd` there, and leaves the original
untouched. The copy uses the same data folder. You can also ask the person who installed SkyGuard QA to run Setup.

## 5. Manual install (without Setup.exe)

The folder `Manual_Install` holds the same upgrade with the scripts used for earlier patches:

* `APPLY_PATCH.cmd`: upgrade. Options (PowerShell): `-InstallDir "<folder>"`, `-NoStart`,
  `-SideBySide [-SideBySideDir "<folder>"]`.
* `VERIFY_PATCH.cmd`: verify.
* `ROLLBACK_PATCH.cmd [-RestoreDatabase]`: roll back.

These scripts run the same `install_patch.py` as Setup, so they make the same checks and the same backup and restore
it automatically on failure.

## 6. Roll back

Run `ROLLBACK_PATCH.cmd` from `<installation>\patch-tools\23.11.1` (or from `Manual_Install`). It puts back the
previous application files, removes the files the upgrade added, and puts back the previous OCR runtime. The databases
are kept as they are: 23.10.0 and 23.11.0 only add tables and columns that earlier versions ignore, and 23.11.1 adds none. `-RestoreDatabase` puts back the copy
taken before the upgrade instead; the current database is kept beside it under a new name.

To undo the extension update, run `UPDATE_EXTENSION.cmd -Rollback`.

## 7. If something goes wrong

* **Setup says the version is not supported.** Read the message. A version older than 23.3.35 needs the earlier
  patches first. Nothing was changed.
* **"The application's Python was not found."** The installation is incomplete. Reinstall SkyGuard QA from the offline
  package. Your data folder is kept.
* **Setup ends with *The upgrade did not complete*.** Nothing was lost; the previous version was restored. Open the
  log (Finish page, or `<installation>\logs\setup-23.11.1-*.txt`) and send it to your SkyGuard contact.
* **The OCR check shows WARN.** The bundled OCR engine could not start on this PC, but Windows OCR can. The upgrade is
  kept, and screenshots are read with Windows OCR.
