@echo off
rem 23.13.0: installs the offline Arabic voice (Piper) for spoken replies. See INSTALL_ARABIC_VOICE.ps1.
rem Usage: INSTALL_ARABIC_VOICE.cmd ["<skyguard-arabic-voice.zip or folder>"]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_ARABIC_VOICE.ps1" %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
