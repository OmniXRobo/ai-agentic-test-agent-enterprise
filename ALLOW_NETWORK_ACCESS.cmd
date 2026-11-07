@echo off
rem 23.3.34: lets colleagues on the office network open SkyGuard QA (Settings > Users > Network access).
rem Adds ONE Windows Firewall rule: incoming TCP on the SkyGuard QA port (8765 by default), for the domain and private
rem (office) networks only, never public Wi-Fi. Run it once as administrator; your IT team can add the same rule instead.
rem Remove the rule again with:  ALLOW_NETWORK_ACCESS.cmd remove
setlocal
set "PORT=%~1"
if /i "%~1"=="remove" set "PORT="
if not defined PORT set "PORT=8765"
if /i "%~1"=="remove" set "MODE=remove"

net session >nul 2>nul
if errorlevel 1 (
  echo Asking Windows for administrator permission...
  if "%~1"=="" (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
  exit /b 0
)

netsh advfirewall firewall delete rule name="SkyGuard QA" >nul 2>nul
if "%MODE%"=="remove" (
  echo The SkyGuard QA firewall rule was removed. Colleagues can no longer open it from their PCs.
  pause
  exit /b 0
)
netsh advfirewall firewall add rule name="SkyGuard QA" dir=in action=allow protocol=TCP localport=%PORT% profile=domain,private
if errorlevel 1 (
  echo The rule could not be added. Ask your IT team to allow incoming TCP %PORT% on this PC.
  pause
  exit /b 1
)
echo.
echo Done. Colleagues can now open http://%COMPUTERNAME%:%PORT%/ and sign in.
pause
endlocal
