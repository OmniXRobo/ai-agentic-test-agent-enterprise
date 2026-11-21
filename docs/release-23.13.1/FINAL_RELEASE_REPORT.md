# Final release report: SkyGuard QA 23.13.1 (cumulative from 23.3.35)

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | One cumulative, offline upgrade to 23.13.1 for any release from 23.3.35 to 23.13.0. It holds `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`, `Manual_Install` (`APPLY_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results and `SHA256SUMS.txt`. |
| `GET_ARABIC_VOICE.ps1` | Also inside the patch (application folder). Run once on a PC with Internet to make `skyguard-arabic-voice.zip`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Unchanged from 23.13.0 (extension 1.4.1). |

## What was asked and what is in this release

| # | Asked | Status |
|---|---|---|
| 1 | `INSTALL_SPEECH_MODEL.cmd` named by the Voice hint was missing | **Done.** In the application folder with `GET_FASTER_WHISPER_MODEL.ps1`; `small.ar` is used for Arabic automatically. |
| 2 | Arabic voice reply, offline (PC has David, Zira, Mark only) | **Done.** Offline Piper voice Kareem installed with `GET_ARABIC_VOICE.ps1` + `INSTALL_ARABIC_VOICE.cmd`; Windows Arabic voices (also OneCore) are used when installed; an English voice never reads Arabic. |
| 3 | Arabic reply in a male or female voice, as chosen | **Done**, with one limit: there is no natural offline female Arabic Piper voice. Female uses Windows Hoda if installed, otherwise Kareem at a higher pitch. |
| 4 | "كابدان افتا صفا" should work like "كابتن افتح صفحة" | **Done.** The wake name is matched in any Arabic spelling; misheard command words are understood. |

## Tests

16 new tests, 147 voice and release tests, 31 installer tests (16 earlier releases), all passed. Windows CI runs the
real Arabic voice download, install and render.

## After installing

1. Run Setup (or `Manual_Install\APPLY_PATCH.cmd`), press Ctrl+F5 in the browser.
2. On a PC with Internet: `powershell -ExecutionPolicy Bypass -File GET_ARABIC_VOICE.ps1` (from the SkyGuard QA
   folder or the patch's `app` folder). Copy `skyguard-arabic-voice.zip` to the office PC's Downloads.
3. Double-click `INSTALL_ARABIC_VOICE.cmd` in the SkyGuard QA folder.
4. Voice settings > Voice reply > *Arabic reply voice*: Male or Female, Save, then *Test Arabic Voice*.
