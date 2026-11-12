@echo off
rem Checks an installation patched to 23.4.0: version, database migrations and integrity, patch files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -VerifyOnly %*
echo.
pause
