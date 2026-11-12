@echo off
rem Removes the SkyGuard companion for the current user (data is kept unless you add -RemoveData).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Uninstall-SkyGuardAgent.ps1" %*
echo.
pause
