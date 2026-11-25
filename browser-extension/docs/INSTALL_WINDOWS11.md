# Installing on Windows 11 (current user, no administrator rights)

## Requirements

* Windows 11 64-bit (build 22000 or later). Windows 10 64-bit is not supported but the installer only warns.
* Google Chrome 116 or later, or Microsoft Edge 116 or later (both have the side panel).
* About 600 MB free in `%LOCALAPPDATA%` (Python, llama-server, Faster-Whisper), plus about 1.6 GB if you install the
  AI and speech models.
* No administrator rights, no internet connection, no SkyGuard QA.

## 1. Extract and install the companion

1. Right-click the zip > **Extract All...** into a folder you can write to.
   Optional: compare the zip with its `.zip.sha256` (`Get-FileHash -Algorithm SHA256 <zip>` in PowerShell).
2. Double-click **INSTALL.cmd**. If Windows SmartScreen asks, choose *More info* > *Run anyway* (the scripts are not
   signed). If your organisation blocks PowerShell scripts entirely, ask IT (see ENTERPRISE_DEPLOYMENT.md).

The installer prints every step:

1. checks Windows and the package (every file against `SHA256_CHECKSUM.txt`),
2. stops a running companion and backs up an earlier version's program files (your data is never touched),
3. unpacks the bundled Python 3.13.17 and llama-server b10549 (CPU), installs Faster-Whisper from the bundled wheels
   (offline, hash-checked),
4. copies the companion and the extension to `%LOCALAPPDATA%\SkyGuardAgent`,
5. adds a sign-in entry for your user only (`HKCU\...\Run\SkyGuardAgent`) and Start menu shortcuts in
   *SkyGuard Agent*,
6. starts the companion, checks it answers on `http://127.0.0.1:8790`, runs the prerequisite check, reads your
   browser policy (read only) and warns if it blocks Load unpacked,
7. copies the AI and speech models when they are found on this PC (a SkyGuard QA installation), and writes a one-time
   set-up ticket for the extension (`extension\auto-setup.json`, valid 7 days, single use),
8. opens your browser's extensions page and copies the extension folder path to the clipboard.

Options: `INSTALL.cmd -Port 8795` (another port), `-NoAutostart` (no sign-in entry), `-NoStart`.

If any step fails, the previous program files are put back and the window says why.

## 2. Optional: AI and speech models from another folder

```
powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\SkyGuardAgent\Install-Models.ps1"
```

The installer already ran this once. Run it yourself only when the models are on a USB drive or another folder.
It looks for the Qwen GGUF and the Faster-Whisper small.en model in a SkyGuard QA installation on this PC, or in a
folder you name with `-From <folder>` (for example a USB drive), checks each file's SHA-256 against the pinned values
and copies them to `%LOCALAPPDATA%\SkyGuardAgent\models`. Nothing is downloaded. See OFFLINE_DEPENDENCIES.md for the
file names and checksums. Restart the companion afterwards (sign out and in, or the Start menu shortcut).

## 3. Load the extension

The installer has opened the extensions page. Turn on **Developer mode**, press **Load unpacked**, paste the folder
path (it is on the clipboard: `%LOCALAPPDATA%\SkyGuardAgent\extension`) and press *Select Folder*. Edge and Chrome
only allow this by hand; SkyGuard does not get around it.

The extension's ID must be `fmiiaecpdgbhhhbbkkfhnjfkbmllladb`. The companion only answers that ID.

## 4. Set-up is automatic

Nothing to type. Within a few seconds of loading, the extension reads its one-time ticket, pairs with the companion
and signs you in as the owner of this PC (the first admin, created for your Windows user). Options shows
*connected* and your name.

Only if the ticket was used or is older than 7 days: Options > *Pair with the companion* and type the code from
Start menu > *SkyGuard Agent - Pairing code*, or run INSTALL.cmd again for a fresh ticket. Other people on this PC
get their own companion users (Options > Companion users) with the role admin, operator or viewer.

## 5. Use it

Click the toolbar icon to open the side panel. See USER_GUIDE.md.

## Check it later

Start menu > *SkyGuard Agent - Check prerequisites* runs the same read-only check as the installer.
