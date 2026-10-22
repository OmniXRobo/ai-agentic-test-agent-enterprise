@echo off
rem Builds the offline Windows package on this PC. Needs Internet once and Python 3.10 or newer (python.org).
rem The result is dist_offline\AIAgenticTestAgent-Offline-<version>\ next to this file; copy that folder to any PC.
setlocal
set "HERE=%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found. Install it from https://www.python.org/downloads/ and run this again.
  if not defined QA_NO_PAUSE pause
  exit /b 1
)
%PY% "%HERE%packaging\build_offline_package.py" --zip %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (echo. & echo Done. Open the dist_offline folder and run INSTALL_OFFLINE.cmd inside the package folder.)
if not defined QA_NO_PAUSE pause
exit /b %RC%
