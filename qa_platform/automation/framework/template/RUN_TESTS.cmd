@echo off
rem Runs the tests of this workspace with pytest. Uses the Python of the QA platform when this
rem folder is its data workspace, otherwise "py -3". No absolute paths are stored.
setlocal
cd /d "%~dp0"
set "PY="
for %%D in ("..\..\runtime\python\python.exe" "..\..\app\runtime\python\python.exe" "..\..\.venv\Scripts\python.exe") do if not defined PY if exist "%%~fD" set "PY=%%~fD"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( echo Python 3 was not found. Install Python 3, then: py -3 -m pip install pytest playwright & exit /b 1 )
if not exist execution-results mkdir execution-results
%PY% -m pytest --junitxml=execution-results\report.xml %*
endlocal
