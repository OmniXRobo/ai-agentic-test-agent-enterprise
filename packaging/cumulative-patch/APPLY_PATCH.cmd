@echo off
rem SkyGuard QA 23.11.1 cumulative upgrade (23.4.0 to 23.11.1), for 23.3.35 to 23.11.0. Setup.exe does the same with a wizard. Add -SideBySide when the application folder is protected. No administrator rights needed. Works offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
