@echo off
rem Checks this copy without Internet and without administrator rights.
rem Add --quick to skip starting JMeter and Chromium.
setlocal
rem Resolve this folder once: %~dp0 can point elsewhere after "cd" when the script was started by a relative path.
set "HERE=%~dp0"
cd /d "%HERE%"
if not exist "%HERE%runtime\python\python.exe" (
  echo This folder has no bundled Python ^(runtime\python^). It is not an offline package.
  if not defined QA_NO_PAUSE pause
  exit /b 1
)
set "PYTHONHOME="
set "PYTHONPATH="
"%HERE%runtime\python\python.exe" -E -s -m qa_platform.offline.validate %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
