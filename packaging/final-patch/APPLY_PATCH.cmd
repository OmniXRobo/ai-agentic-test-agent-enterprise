@echo off
rem SkyGuard QA final consolidated upgrade patch 23.4.0 (for 23.3.35). No administrator rights needed. Works offline.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
