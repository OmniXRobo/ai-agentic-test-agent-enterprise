# Known limitations: 23.13.0

Stated plainly so that nobody has to discover them.

## Where it was tested

* **Linux build machine, Chromium.** Every automated test ran on Linux with Python 3.13 and Playwright's Chromium.
  **Microsoft Edge was not available here.** The Edge preference and the fallback order were tested with stand-in
  browsers (which browser is asked for, which is tried when one is not installed, and that an installed but failing
  browser is reported, not replaced). Please run one AI-Driven generation, one Discovery and one Hybrid run on the
  office PC after installing.
* **Windows-only steps** (Windows voices, Windows Credential Manager, the Windows certificate store, `certutil`, the
  hosts file and the firewall in `ENABLE_HTTPS.cmd`) were checked by reading and by tests with stand-ins, not run on
  Windows here. Setup under Wine is in TEST_REPORT.md.
* **TFS:** a fake Azure DevOps server in the tests (`tests/fake_ado.py`) stands in for TFS. Your TFS server, its
  teams endpoint and real PATs were not available.
* **Local AI:** the local Qwen model was not running. Arabic translation was tested with the built-in word list; AI
  results, module reports and the Analytics analysis were tested for their facts, PDF and email, not for the quality
  of real AI text.
* **Outlook email:** sending was tested with a stand-in mail service.

## Voice

* **Arabic speech recognition needs a multilingual model.** The English-only Vosk / `small.en` models cannot hear
  Arabic. For Arabic and UAE Arabic, put the multilingual Faster-Whisper model (`small` or larger) in the speech
  model folder: on a PC with internet run `GET_FASTER_WHISPER_MODEL.ps1 -Model small` (the tools folder of the
  earlier patches) and copy the folder to the office PC. Typed Arabic works without it.
* **Arabic replies need an Arabic voice.** Office PCs usually have only English Windows voices (David, Zira, Mark),
  and the Windows Arabic language pack cannot be downloaded offline. The next patch adds an offline Arabic voice:
  run `GET_ARABIC_VOICE.ps1` on a PC with Internet (Piper text-to-speech, pinned SHA-256, and the male voice
  `ar_JO-kareem-medium`, checked against the SHA-256 Hugging Face publishes), copy `skyguard-arabic-voice.zip` to
  the office PC and double-click `INSTALL_ARABIC_VOICE.cmd` in the SkyGuard QA folder. Arabic replies then use it
  automatically; a Windows Arabic voice is preferred when IT has installed one. The voice model could not be put in
  the patch itself because the build machine cannot reach Hugging Face. Without any Arabic voice, Arabic replies are
  shown but not spoken (an English voice no longer reads Arabic text).
* The Arabic voice is Jordanian (Levantine) Arabic; it is understandable for UAE users but is not a Gulf accent.
* **Female Arabic voice:** Piper publishes only one Arabic voice (Kareem, male). With *Arabic reply voice: Female*,
  SkyGuard QA uses a Windows female Arabic voice (Hoda) if IT has installed the Arabic language pack with speech;
  otherwise it speaks Kareem at a higher pitch, which sounds lighter but is not a natural female voice.
* Without the local AI, the reply details stay in English on screen and a short Arabic status line is spoken.
* UAE dialect words not known to the local AI or the word list may be translated loosely; the *Understood as* line
  shows what was understood before anything runs.

## HTTPS

* The certificate is from SkyGuard's own local CA. Each PC that opens the HTTPS address must trust it once (*Trust on
  this PC* here, `TRUST_SKYGUARD_QA.cmd` on colleagues' PCs), and the name must resolve (hosts file or office DNS).
  Without that, Edge shows a certificate warning or cannot find the name.
* Port 443 may already be used (IIS, another program). Choose another port and use `https://name:port/`.
* A change of the HTTPS setting takes effect after SkyGuard QA is restarted.
* Residual risks of a local CA: SECURITY_REPORT.md section 4.

## Users and TFS

* Existing users keep the shared TFS connection until an administrator adds their own PAT and project.
* When TFS cannot be reached while a user is created, the PAT cannot be checked; it is saved and checked at its first
  use (the response says *not checked*).
* "Assign to" lists the project's team members and people who already have work items in it; a person in neither
  can still be typed into TFS later.

## Healing

* Only a healed locator whose step then passed is written into the script. To go back to proposals only, set
  `"auto_apply_healing": false` under `playback` in `settings.json` (there is no page switch for it yet).
* Dynamic URL matching recognises numbers, long hex / IDs, dates and tokens in a path or query. A site that changes
  meaningful words in its address (not IDs) still needs `expect_url` to be edited.

## AI Agent

* "Same site" uses a built-in list of second-level domains (`co`, `com`, `gov`, ...), not the full public suffix
  list (offline). Shared hosting such as `github.io` should be registered with the exact address.

## Not done in this release

* `INSTALL_SPEECH_MODEL.cmd` was referred to by the Voice settings hint but was **not in the 23.13.0 package**. Until the
  next patch, copy the model folder by hand to `runtime\faster-whisper\models\small`. The next patch adds
  `INSTALL_SPEECH_MODEL.cmd` and `GET_FASTER_WHISPER_MODEL.ps1` to the application folder.
* Setup.exe is still not code-signed (SmartScreen says *Unknown publisher*).
