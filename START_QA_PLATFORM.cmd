@echo off
rem Starts the Enterprise QA Runtime and opens the dashboard. No administrator rights needed.
setlocal
cd /d "%~dp0"

set "PY="
if exist "%~dp0runtime\python\python.exe" set "PY=%~dp0runtime\python\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python was not found. Run SETUP_DEV.cmd, or use the offline package which includes Python.
  pause
  exit /b 1
)

if exist "%~dp0runtime\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0runtime\ms-playwright"

for /f "usebackq delims=" %%P in (`%PY% -c "import json,pathlib;p=pathlib.Path(r'%~dp0data\settings.json');print(json.loads(p.read_text())['server']['port'] if p.exists() else 8765)" 2^>nul`) do set "PORT=%%P"
if not defined PORT set "PORT=8765"

start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%/"
%PY% -m qa_platform
endlocal
