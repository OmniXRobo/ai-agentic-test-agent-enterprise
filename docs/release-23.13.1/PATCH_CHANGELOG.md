# Changelog: SkyGuard QA 23.13.1

Cumulative on 23.13.0 (commit `e0f9ac4`, PR #59). One upgrade for every release from 23.3.35 to 23.13.0. Everything
works offline. The browser extension stays at 1.4.1 (unchanged). The database stays at 22 migrations.

The 23.13.0 changes are in `previous-23.13.0/PATCH_CHANGELOG.md`.

## 1. Arabic spoken replies, offline, male or female

* An Arabic reply is always spoken by an Arabic voice. An English voice (David, Zira, Mark) no longer reads Arabic
  text. With no Arabic voice installed, the reply is shown on screen and not spoken.
* New setting **Voice settings > Voice reply > Arabic reply voice**: *Like the radio voice* (default), *Male* or
  *Female*.
  * Male: a Windows male Arabic voice (Naayf) if installed, otherwise the offline Piper voice **Kareem**
    (`ar_JO-kareem-medium`).
  * Female: a Windows female Arabic voice (Hoda) if installed, otherwise a female Piper model if one is installed,
    otherwise Kareem at a higher pitch. Piper publishes no female Arabic voice.
* Arabic voices that Windows 10 / 11 installs from Settings (language pack "OneCore" voices, which the older
  System.Speech engine cannot see) are now found and used.
* Both reply styles work: *Pilot radio* (with the radio filter) and *Plain voice*.
* Voice settings show which voice speaks Arabic now, plus the male and the female voice, and have a
  **Test Arabic Voice** button. *Check voice setup* has an "Arabic spoken replies" line.
* Without the local AI, an Arabic command is still answered in Arabic: a short status line is spoken ("تم",
  "ما فهمت الأمر ..."), and the details stay on screen.

## 2. Installing the Arabic voice on an offline PC

* `GET_ARABIC_VOICE.ps1` (on any Windows PC with Internet) downloads Piper 2023.11.14-2 for Windows (checked against
  a pinned SHA-256) and the Kareem voice (checked against the SHA-256 Hugging Face publishes). It makes
  `skyguard-arabic-voice.zip` (about 85 MB). `-Voice` accepts more than one Arabic Piper model.
* `INSTALL_ARABIC_VOICE.cmd` (in the SkyGuard QA folder, double-click) finds the zip in Downloads, on the Desktop or
  next to the script and installs it to `runtime\piper`. It works offline and per user, through a staging folder, and
  ends with a test word. No restart is needed.

## 3. Arabic wake name and misheard commands

* The agent's name said in Arabic is recognised however the recognizer writes it. Names are compared by their
  consonants, so for *Captain*: كابتن, كابدان, كبتن, كابتين, and "يا كابتن". This works in the runtime and in the
  dashboard's wake listening. It works for any configured name (صقر = Saqr, هامي = Hami).
* Common misheard command words are understood: افتا / افتاح → open, صفا / صفحه → page, and others. The reported
  phrase "كابدان افتا صفا" now runs like "كابتن افتح صفحة".

## 4. Speech model helper (the 23.13.0 Voice hint referred to it)

* `INSTALL_SPEECH_MODEL.cmd` / `.ps1` and `GET_FASTER_WHISPER_MODEL.ps1` are now in the application folder.
  `INSTALL_SPEECH_MODEL.cmd` installs `faster-whisper-<model>.zip` (or the folder) offline, to
  `runtime\faster-whisper\models\<name>`. It prefers a multilingual model.
* An installed multilingual model with another folder name (for example `small.ar`) is used for Arabic and
  "English and Arabic" automatically. Before, only the standard names (small, base ...) were recognised.
