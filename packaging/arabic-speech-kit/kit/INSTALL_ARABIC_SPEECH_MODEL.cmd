@echo off
rem SkyGuard QA Arabic speech kit, step 2 (on the SkyGuard QA PC; offline, no administrator rights).
rem Installs faster-whisper-small.zip (or -medium) and sets speech recognition to "English and Arabic (detected)".
rem Close SkyGuard QA first. See INSTALL_ARABIC_SPEECH_MODEL.ps1 for the options (-Source, -AppDir, -Language ar).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_ARABIC_SPEECH_MODEL.ps1" %*
set "RC=%ERRORLEVEL%"
if not defined QA_NO_PAUSE pause
exit /b %RC%
