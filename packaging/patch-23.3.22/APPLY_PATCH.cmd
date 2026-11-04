@echo off
rem Applies the QA Enterprise AI patch 23.3.22 (includes 23.3.21, 23.3.20, 23.3.19, 23.3.18, 23.3.17, 23.3.16, 23.3.15, 23.3.14, 23.3.13, 23.3.12, 23.3.11, 23.3.10, 23.3.9, 23.3.8 and 23.3.7-r5). No administrator rights needed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0APPLY_PATCH.ps1" %*
echo.
pause
