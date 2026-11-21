@echo off
rem 23.13.0: HTTPS with a friendly name for SkyGuard QA (Settings > Users & Network > HTTPS). Works offline.
rem Run on the PC where SkyGuard QA runs, as administrator (the Settings button asks Windows for permission).
rem   1. adds the SkyGuard QA Local CA (made by SkyGuard QA on this PC) to "Trusted Root Certification Authorities",
rem      so Microsoft Edge trusts https://<name>/
rem   2. maps the name to this PC in C:\Windows\System32\drivers\etc\hosts  (127.0.0.1 <name>)
rem   3. allows incoming HTTPS on the domain and private networks (Windows Firewall rule "SkyGuard QA HTTPS")
rem Usage: ENABLE_HTTPS.cmd <name> <port> "<path of SkyGuard_QA_Local_CA.crt>"
setlocal EnableExtensions
set "NAME=%~1"
set "PORT=%~2"
set "CA=%~3"
if not defined NAME set "NAME=skyguard-qa"
if not defined PORT set "PORT=443"
if not defined CA set "CA=%LOCALAPPDATA%\SkyGuardQA\data\tls\SkyGuard_QA_Local_CA.crt"
echo %NAME%| findstr /r /x "[A-Za-z0-9][A-Za-z0-9.-]*" >nul || (echo The name "%NAME%" is not valid. & pause & exit /b 1)
echo %PORT%| findstr /r /x "[0-9][0-9]*" >nul || (echo The port "%PORT%" is not valid. & pause & exit /b 1)

net session >nul 2>nul
if errorlevel 1 (
  echo Asking Windows for administrator permission...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%NAME%','%PORT%','\"%CA%\"' -Verb RunAs"
  exit /b 0
)
if not exist "%CA%" (
  echo The certificate "%CA%" was not found. Turn HTTPS on in SkyGuard QA first.
  pause
  exit /b 1
)
certutil -addstore -f Root "%CA%" >nul
if errorlevel 1 (echo Windows did not accept the certificate. & pause & exit /b 1)
echo [1/3] SkyGuard QA Local CA trusted on this PC.

set "HOSTS=%SystemRoot%\System32\drivers\etc\hosts"
findstr /r /i /c:"[ 	]%NAME%$" "%HOSTS%" >nul 2>nul
if errorlevel 1 (
  >>"%HOSTS%" echo 127.0.0.1 %NAME%
  echo [2/3] %NAME% now points to this PC.
) else echo [2/3] %NAME% is already in the hosts file.
ipconfig /flushdns >nul

netsh advfirewall firewall delete rule name="SkyGuard QA HTTPS" >nul 2>nul
netsh advfirewall firewall add rule name="SkyGuard QA HTTPS" dir=in action=allow protocol=TCP localport=%PORT% profile=domain,private >nul
echo [3/3] Incoming HTTPS (port %PORT%) allowed on office networks.
echo.
if "%PORT%"=="443" (echo Done. Restart SkyGuard QA, then open https://%NAME%/) else echo Done. Restart SkyGuard QA, then open https://%NAME%:%PORT%/
echo Colleagues run TRUST_SKYGUARD_QA.cmd on their PCs once (see Settings ^> Users ^& Network ^> HTTPS).
pause
endlocal
