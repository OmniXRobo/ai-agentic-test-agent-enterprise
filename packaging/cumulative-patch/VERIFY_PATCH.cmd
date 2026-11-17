@echo off
rem Checks an installation upgraded to 23.10.0: version, AI Agent and guided recording, Integration Comparison with filters and meaning, maximized browser policy, offline OCR engine probe, component engine, passive learning engine, both databases (migrations, integrity), agent core, patch files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" -VerifyOnly %*
echo.
pause
