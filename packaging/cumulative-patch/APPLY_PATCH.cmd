@echo off
rem SkyGuard QA 23.9.0 cumulative patch: Integration Comparison, offline OCR and browser final patch (23.4.0 to 23.9.0), for 23.3.35 to 23.8.0. Add -SideBySide when the application folder is protected. No administrator rights needed. Works offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
