@echo off
rem Puts back SkyGuard QA 23.4.0 from the backup the 23.5.0 patch made. Your data is kept.
rem Add -RestoreDatabase to also put back the database copy taken before the upgrade (the current one is kept, renamed).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -Rollback %*
echo.
pause
