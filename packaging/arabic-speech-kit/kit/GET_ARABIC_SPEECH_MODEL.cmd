@echo off
rem SkyGuard QA Arabic speech kit, step 1 (on a PC WITH Internet; no Python needed).
rem Downloads the multilingual Faster-Whisper speech model that understands Arabic (UAE and other) AND English,
rem checks every file against the SHA-256 Hugging Face publishes, and makes faster-whisper-<model>.zip in this folder.
rem
rem Usage:  GET_ARABIC_SPEECH_MODEL.cmd           small  (about 465 MB, recommended: fast on any office PC)
rem         GET_ARABIC_SPEECH_MODEL.cmd medium    medium (about 1.5 GB, better Arabic, about 3 times slower)
setlocal
set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=small"
if /i not "%MODEL%"=="small" if /i not "%MODEL%"=="medium" (
  echo Use "small" ^(recommended^) or "medium". English-only models ^(small.en ...^) do not understand Arabic.
  if not defined QA_NO_PAUSE pause
  exit /b 2
)
rem Started from PowerShell 7, Windows PowerShell would inherit its module path and miss Get-FileHash and the
rem other built-in cmdlets: let it use its own.
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0GET_FASTER_WHISPER_MODEL.ps1" -Model %MODEL%
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo NEXT: copy faster-whisper-%MODEL%.zip AND this whole folder to the SkyGuard QA PC ^(for example into Downloads^),
  echo       close SkyGuard QA there and double-click INSTALL_ARABIC_SPEECH_MODEL.cmd.
) else (
  echo The download did not finish ^(exit code %RC%^). Run GET_ARABIC_SPEECH_MODEL.cmd again: it resumes where it stopped.
)
if not defined QA_NO_PAUSE pause
exit /b %RC%
