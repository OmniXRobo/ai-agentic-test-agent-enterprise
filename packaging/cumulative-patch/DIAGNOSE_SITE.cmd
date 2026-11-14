@echo off
rem SkyGuard QA 23.7.1 site diagnostic: captures how trip.com's search box, city list, calendar and passengers look to the automation, into one ZIP on your Desktop to send back. No administrator rights; nothing is booked.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0DIAGNOSE_SITE.ps1" %*
echo.
pause
