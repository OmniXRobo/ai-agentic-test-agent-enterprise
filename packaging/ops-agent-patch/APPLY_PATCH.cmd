@echo off
rem SkyGuard QA 23.5.0 cumulative patch (AI Operations Agent), for 23.4.0. No administrator rights needed. Works offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
