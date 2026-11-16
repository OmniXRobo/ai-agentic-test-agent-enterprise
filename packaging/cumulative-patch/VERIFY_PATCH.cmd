@echo off
rem Checks an installation patched to 23.9.0: version, Integration Comparison with filters and meaning, maximized browser policy, offline OCR engine probe, component engine, passive learning engine, both databases (migrations, integrity), agent core, patch files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -VerifyOnly %*
echo.
pause
