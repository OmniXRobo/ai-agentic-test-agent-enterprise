@echo off
rem Checks an installation patched to 23.8.0: version, Integration Comparison Workspace, component engine, passive learning engine, both databases (migrations, integrity), agent core, patch files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -VerifyOnly %*
echo.
pause
