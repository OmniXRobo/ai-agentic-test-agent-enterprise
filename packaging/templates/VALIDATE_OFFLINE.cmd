@echo off
rem Checks this copy without Internet and without administrator rights.
rem Add --quick to skip starting JMeter and Chromium.
setlocal
cd /d "%~dp0"
if not exist "%~dp0runtime\python\python.exe" (
  echo This folder has no bundled Python ^(runtime\python^). It is not an offline package.
  if not defined QA_NO_PAUSE pause
  exit /b 1
)
set "PYTHONHOME="
set "PYTHONPATH="
"%~dp0runtime\python\python.exe" -E -s -m qa_platform.offline.validate %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
