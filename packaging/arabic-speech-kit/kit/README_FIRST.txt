SkyGuard QA - Arabic speech kit (add-on for 23.14.1; also works on 23.13.1 and later)
=====================================================================================

WHAT THIS IS
  Lets SkyGuard QA UNDERSTAND spoken Arabic (UAE and other) as well as English, offline.
  The speech model installed today (small.en) understands English only. This kit adds the multilingual model
  "small" (or "medium") and sets Voice settings to "English and Arabic (detected)": say a command in English or in
  Arabic and SkyGuard QA works out which language it is.
  Speaking replies in Arabic is a different file (skyguard-arabic-voice.zip, INSTALL_ARABIC_VOICE.cmd); keep it.
  The application itself is not changed (it stays 23.14.1) and the English model small.en is kept.

ALREADY HAVE faster-whisper-small.zip? (for example the one shared with this kit) Skip step 1.

STEP 1 - on a PC WITH Internet (about 465 MB, 5 to 15 minutes)
  1. Unzip this kit.
  2. Double-click  GET_ARABIC_SPEECH_MODEL.cmd
     It downloads Systran/faster-whisper-small from Hugging Face, checks every file against the published SHA-256
     and makes  faster-whisper-small.zip  in this folder.
     Better Arabic, slower (about 1.5 GB):  GET_ARABIC_SPEECH_MODEL.cmd medium
     If you run the PowerShell script yourself, always use -ExecutionPolicy Bypass:
       powershell -ExecutionPolicy Bypass -File .\GET_FASTER_WHISPER_MODEL.ps1 -Model small
     (NOT the default small.en: that is the English-only model you already have.)

STEP 2 - on the SkyGuard QA PC (offline, no administrator rights, 2 minutes)
  1. Copy this whole folder and faster-whisper-small.zip to the PC (for example both into Downloads).
     Do not unzip faster-whisper-small.zip.
  2. Close SkyGuard QA.
  3. Double-click  INSTALL_ARABIC_SPEECH_MODEL.cmd
     It finds the SkyGuard QA folder and the zip by itself, refuses an English-only model, checks every file
     against the SHA-256 recorded at download, installs the model and sets Voice settings (a copy of settings.json
     is kept first).
  4. Start SkyGuard QA, press Ctrl+F5 once, open Voice settings and press "Test Arabic transcription".
     Then try: "كابتن، افتح خطط الاختبار" or "Captain, open Test Plans".

  Other folder or file:
    INSTALL_ARABIC_SPEECH_MODEL.cmd "D:\usb\faster-whisper-small.zip"
    INSTALL_ARABIC_SPEECH_MODEL.cmd -AppDir "%LOCALAPPDATA%\Programs\AIAgenticTestAgent\app"
  Arabic only (English commands are then not understood):
    INSTALL_ARABIC_SPEECH_MODEL.cmd -Language ar

TO UNDO
  Voice settings > Language = English (the English model small.en is used again). The model folder is
  <SkyGuard QA>\runtime\faster-whisper\models\small; delete it to free the space.

IN THIS FOLDER
  GET_ARABIC_SPEECH_MODEL.cmd        step 1 (PC with Internet)
  GET_FASTER_WHISPER_MODEL.ps1       the download script it runs (the same one as in the SkyGuard QA folder)
  INSTALL_ARABIC_SPEECH_MODEL.cmd    step 2 (SkyGuard QA PC)
  INSTALL_ARABIC_SPEECH_MODEL.ps1    what step 2 does, with its options
  INSTALL_SPEECH_MODEL.ps1           the model installer (used when the SkyGuard QA folder has none)
  set_speech_language.py             changes the voice part of settings.json (run with SkyGuard QA's own Python)
  SHA256SUMS.txt                     SHA-256 of every file here
