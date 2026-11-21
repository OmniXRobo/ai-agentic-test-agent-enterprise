@echo off
rem Added after 23.13.0: installs a Faster-Whisper speech model (for Arabic: "small") offline. See INSTALL_SPEECH_MODEL.ps1.
rem Usage: INSTALL_SPEECH_MODEL.cmd ["<faster-whisper-small.zip or folder>"] [-Name small]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_SPEECH_MODEL.ps1" %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
