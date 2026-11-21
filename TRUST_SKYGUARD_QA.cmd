@echo off
rem 23.13.0: run once on a colleague's PC so that Microsoft Edge opens https://<name>/ of the SkyGuard QA server.
rem Copy this file and SkyGuard_QA_Local_CA.crt (Settings > Users & Network > HTTPS > Download) to the colleague's PC.
rem Usage: TRUST_SKYGUARD_QA.cmd <server IP address> [name] ["<path of SkyGuard_QA_Local_CA.crt>"]
rem   - adds the SkyGuard QA Local CA to "Trusted Root Certification Authorities" (public certificate only, no key)
rem   - maps the name to the server in the hosts file (skip it when IT added the name to the office DNS)
setlocal EnableExtensions
set "IP=%~1"
set "NAME=%~2"
set "CA=%~3"
if not defined NAME set "NAME=skyguard-qa"
if not defined CA set "CA=%~dp0SkyGuard_QA_Local_CA.crt"
if not defined IP (
  echo Usage: TRUST_SKYGUARD_QA.cmd ^<server IP address^> [name]
  echo Example: TRUST_SKYGUARD_QA.cmd 10.20.30.40 skyguard-qa
  pause
  exit /b 1
)
echo %IP%| findstr /r /x "[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*" >nul || (echo "%IP%" is not an IPv4 address. & pause & exit /b 1)
echo %NAME%| findstr /r /x "[A-Za-z0-9][A-Za-z0-9.-]*" >nul || (echo The name "%NAME%" is not valid. & pause & exit /b 1)

net session >nul 2>nul
if errorlevel 1 (
  echo Asking Windows for administrator permission...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%IP%','%NAME%','\"%CA%\"' -Verb RunAs"
  exit /b 0
)
if not exist "%CA%" (
  echo "%CA%" was not found. Put SkyGuard_QA_Local_CA.crt next to this file.
  pause
  exit /b 1
)
certutil -addstore -f Root "%CA%" >nul
if errorlevel 1 (echo Windows did not accept the certificate. & pause & exit /b 1)
set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
findstr /r /i /c:"[ 	]%NAME%$" "%HOSTS%" >nul 2>nul
if errorlevel 1 (>>"%HOSTS%" echo %IP% %NAME%) else echo %NAME% is already in the hosts file; check that it points to %IP%.
ipconfig /flushdns >nul
echo.
echo Done. Open https://%NAME%/ in Microsoft Edge and sign in.
pause
endlocal
