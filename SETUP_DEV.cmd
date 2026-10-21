@echo off
rem Creates a per-user virtual environment and installs dependencies. No administrator rights needed.
setlocal
cd /d "%~dp0"
where py >nul 2>nul || (echo Python 3.10+ is required. Install it for "just me" from python.org. & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv || exit /b 1
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || exit /b 1
rem Playwright browsers go to the per-user profile (%LOCALAPPDATA%\ms-playwright). The offline package bundles them instead.
".venv\Scripts\python.exe" -m playwright install chromium || exit /b 1
echo.
echo Setup complete. Start the platform with START_QA_PLATFORM.cmd
endlocal
