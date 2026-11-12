# Installing SkyGuard QA 23.4.0 (final consolidated upgrade patch)

The patch upgrades an installed **SkyGuard QA 23.3.35** to **23.4.0**. It works offline and needs no administrator
rights. Your data, settings, saved logins and TFS connection are kept.

## Before you start

1. Make sure the installed version is 23.3.35 (Settings > About, or the version at the bottom of the menu).
2. Close SkyGuard QA (the patch also stops it if it is still running).
3. Copy `QA_Enterprise_AI_Final_Upgrade_Patch.zip` to the PC.

## Check the zip (optional)

```powershell
Get-FileHash .\QA_Enterprise_AI_Final_Upgrade_Patch.zip -Algorithm SHA256
```

Compare it with the SHA-256 given with the delivery. After extracting, `SHA256_CHECKSUM.txt` lists the SHA-256 of
every file in the patch, and the installer checks every application file against `PATCH_MANIFEST.json` itself.

## Install

```powershell
cd $env:USERPROFILE\Downloads
Unblock-File .\QA_Enterprise_AI_Final_Upgrade_Patch.zip
Expand-Archive .\QA_Enterprise_AI_Final_Upgrade_Patch.zip -DestinationPath . -Force
.\QA_Enterprise_AI_Final_Upgrade_Patch\APPLY_PATCH.cmd
```

If SkyGuard QA is installed somewhere other than `%LOCALAPPDATA%\Programs\AIAgenticTestAgent`:

```powershell
powershell -ExecutionPolicy Bypass -File .\QA_Enterprise_AI_Final_Upgrade_Patch\APPLY_PATCH.ps1 -InstallDir "D:\Apps\AIAgenticTestAgent"
```

The patch prints each step: version check, file check, disk space and permissions, backup (to
`<install>\patch-backups\23.3.35-to-23.4.0-<time>`), copy, database migration, verification. If any step fails, the
previous files and database are put back automatically and the message says why. It then starts SkyGuard QA and
checks `/api/health`. Press **Ctrl+F5** once in the browser.

A log is written to `<install>\logs\patch-23.4.0-<time>.txt`.

## Verify later

```powershell
.\QA_Enterprise_AI_Final_Upgrade_Patch\VERIFY_PATCH.cmd
```

It checks the version, that the application loads, the database migrations and integrity, and that every patch file
is present.

## Roll back

```powershell
.\QA_Enterprise_AI_Final_Upgrade_Patch\ROLLBACK_PATCH.cmd
```

This puts back the 23.3.35 files from the newest backup and removes the files the patch added. The database is kept
as it is (23.3.35 ignores the new tables), so nothing created after the upgrade is lost. To also put back the database
copy taken before the upgrade, add `-RestoreDatabase`. The current database is then kept next to it, renamed, not
deleted.

## After installing

* **Settings > TFS Integration**: press Discover Project Metadata, then Validate Mandatory Fields for Test Case and
  Bug, and save defaults for any field marked as needing a value. Test Work Item Creation checks it with TFS without
  saving anything.
* **Settings > Browser Manager**: choose the browser and the monitor, then press Check browsers.
* **Settings > AURA Agent Memory**: choose the memory folder (for example `D:\QAEnterprise\AURA_Memory`).
* **Application Data Comparison Agent > Applications**: add the applications to compare. Allowed hosts are taken from
  each application's URL; add others in the project settings.
* For screenshots and RDP windows, check that Windows OCR has a language installed, or set the Tesseract path under
  Preferences.
