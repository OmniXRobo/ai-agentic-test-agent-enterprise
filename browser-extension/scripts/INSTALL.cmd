@echo off
rem SkyGuard Adaptive AI Browser Extension: installs the local companion for the current Windows user.
rem No administrator rights are needed. Works offline. See docs\INSTALL_WINDOWS11.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Install-SkyGuardAgent.ps1" %*
echo.
pause
