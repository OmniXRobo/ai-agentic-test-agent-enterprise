@echo off
rem Installs AI Agentic Test Agent Enterprise for the current user.
rem No administrator rights and no Internet are needed. Default folder: %LOCALAPPDATA%\Programs\AIAgenticTestAgent
rem Another folder: INSTALL_OFFLINE.cmd --target "D:\Tools\AIAgenticTestAgent"
setlocal
rem Resolve this folder once: %~dp0 can point elsewhere after "cd" when the script was started by a relative path.
set "HERE=%~dp0"
if not exist "%HERE%payload\runtime\python\python.exe" (
  echo The package is incomplete: payload\runtime\python is missing. Copy the whole folder again.
  if not defined QA_NO_PAUSE pause
  exit /b 1
)
cd /d "%HERE%payload"
set "PYTHONHOME="
set "PYTHONPATH="
"%HERE%payload\runtime\python\python.exe" -E -s -m qa_platform.offline.install install --source "%HERE%payload" %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
