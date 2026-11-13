@echo off
rem Checks an installation patched to 23.5.0: version, both databases (migrations, integrity), agent core, patch files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -VerifyOnly %*
echo.
pause
