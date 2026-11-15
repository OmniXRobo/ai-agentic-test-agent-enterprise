@echo off
rem SkyGuard QA 23.7.1 live flight-search check: searches trip.com twice with different values using the playback engine, verifies the results page, and saves a report ZIP on your Desktop. Nothing is booked.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0VERIFY_LIVE_SITE.ps1" %*
echo.
pause
