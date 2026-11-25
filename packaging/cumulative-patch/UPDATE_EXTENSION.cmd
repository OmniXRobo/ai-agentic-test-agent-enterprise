@echo off
rem Updates the SkyGuard browser extension and companion to 1.4.0 (sets itself up: no pairing code, no sign-in). Current user only, no administrator rights, offline.
rem Add -Rollback to put back the previous version.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0UPDATE_EXTENSION.ps1" %*
echo.
pause
