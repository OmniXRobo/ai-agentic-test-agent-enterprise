@echo off
rem Updates the SkyGuard browser extension companion to 1.1.0 (learn while you browse). Current user only, no administrator rights, offline.
rem Add -Rollback to put back the previous version.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0UPDATE_EXTENSION.ps1" %*
echo.
pause
