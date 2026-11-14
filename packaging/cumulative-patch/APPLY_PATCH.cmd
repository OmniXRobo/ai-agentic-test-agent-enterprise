@echo off
rem SkyGuard QA 23.6.1 cumulative patch (23.4.0 + 23.5.0 + 23.6.0 + 23.6.1), for 23.3.35, 23.4.0, 23.5.0 or 23.6.0. No administrator rights needed. Works offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
