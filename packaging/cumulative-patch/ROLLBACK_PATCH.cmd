@echo off
rem Puts back the version you had before the 23.7.1 cumulative patch, from the backup it made. Your data is kept.
rem Add -RestoreDatabase to also put back the database copy taken before the upgrade (the current one is kept, renamed).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -Rollback %*
echo.
pause
