# Installing the 23.9.0 patch

**SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch.zip** is one cumulative patch (23.4.0 to 23.9.0). It
installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2 and 23.8.0. Applying it again over 23.9.0
is allowed. You do not need administrator rights or network access.

## Before you start

1. Check the zip: `certutil -hashfile SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch.zip SHA256` must print
   the value in `SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch.zip.sha256`.
2. Extract the whole zip to a folder of your own, for example `Downloads`.
3. Close SkyGuard QA. The patch stops it if it is still running.
4. Allow about 650 MB of free space. The OCR runtime is about 270 MB once unpacked, and the previous one is kept for
   rollback.

## Apply

Double-click **APPLY_PATCH.cmd** in the extracted folder. It runs `install_patch.py` with the application's own Python
and prints every step:

1. checks the version, the SHA-256 of every file and of every OCR wheel, the disk space and that the folders can be
   written;
2. backs up the files it replaces, the database (an SQLite online backup), the agent's knowledge database and
   settings.json, under `patch-backups\<from>-to-23.9.0-<time>`;
3. copies the new files;
4. installs the offline OCR runtime into `runtime\ocr\site-packages`, or into
   `%LOCALAPPDATA%\SkyGuardQA\ocr\site-packages` when the application folder cannot be written. It unpacks into a
   staging folder first and swaps it in only when it is complete;
5. migrates (23.9.0 adds no database migration) and verifies: version, comparison workspace with filters and meaning,
   maximized browser policy, the **OCR engine probe** (it reads a small test image), database integrity and that no
   data was lost. If the bundled OCR engine cannot run on this PC but Windows OCR can, the check shows **WARN** with
   the reason, the patch is kept, and screenshots are read with Windows OCR;
6. on any failure, puts back the files, the previous OCR runtime and the database copy automatically.

Then it starts SkyGuard QA and checks `/api/health`. Press Ctrl+F5 once in the browser.

Options (PowerShell): `-InstallDir "<folder>"`, `-NoStart`, `-VerifyOnly`, `-Rollback [-RestoreDatabase]`,
`-SideBySide [-SideBySideDir "<folder>"]`.

## A protected application folder

If SkyGuard QA was installed for all users, for example in a machine-wide programs folder, a standard user cannot
change it. The patch does not try to work around Windows permissions. It stops with a clear message, and nothing is
changed. You can either:

* ask the person who installed SkyGuard QA to apply the patch, or
* run `APPLY_PATCH.cmd -SideBySide`. The patch copies the application into `%LOCALAPPDATA%\SkyGuardQA\app-23.9.0`
  (or `-SideBySideDir`), patches the copy and writes `START_SKYGUARD_23.9.0.cmd` there. The copy uses the same data
  folder. The original installation is not changed.

## Verify later

Run **VERIFY_PATCH.cmd**. Every check prints PASS or FAIL. Then, in SkyGuard QA, open
*Integration Comparison* > *Analyze*, or call `GET /api/comparison/workspace/ocr/diagnostics`, to see which OCR engine
is used and why the others are not.

## Rollback

Run **ROLLBACK_PATCH.cmd**. It restores the previous application files, removes the files the patch added and puts
back the previous OCR runtime (or removes the one the patch installed). The database is kept, because 23.9.0 does not
change its schema. `-RestoreDatabase` puts back the copy taken before the upgrade, and the current database is kept
next to it, renamed.

## OCR engines and settings

The order is set in Preferences > Comparison: `ocr_engine` = `auto` (bundled RapidOCR, then Windows OCR, then
Tesseract), `rapidocr`, `windows` or `tesseract`. You can also set:

* `ocr_runtime_path`: another folder that holds the OCR runtime;
* `tesseract_path` and `tessdata_path`: a portable Tesseract, if you use one;
* `ocr_timeout_seconds` (default 180);
* `data_timeout_ms` (default 15000): how long a live page may take to show its data.

## Browser windows

Every visible browser that SkyGuard QA opens (recording, playback, guided automation, discovery, live capture, the
Ops agent, performance recording) opens maximized, and so do its popups and new tabs. Headless runs are never
maximized. For a fixed size instead, set Preferences > Playback > `window_mode` = `fixed` with `viewport_width` /
`viewport_height`. `default` leaves the window as the browser opens it.
