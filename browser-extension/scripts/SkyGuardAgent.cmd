@echo off
rem SkyGuard companion commands: serve | pair | check | status | version
setlocal
set "SG=%LOCALAPPDATA%\SkyGuardAgent"
if "%~1"=="" (set "CMD=status") else (set "CMD=%~1")
"%SG%\runtime\python\python.exe" -E -s "%SG%\app\companion_main.py" %CMD%
if /I "%CMD%"=="pair" pause
endlocal
