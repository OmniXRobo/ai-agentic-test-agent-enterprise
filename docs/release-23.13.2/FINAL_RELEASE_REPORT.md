# Final release report: SkyGuard QA 23.13.2 (cumulative from 23.3.35)

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` | One cumulative, offline upgrade to 23.13.2 for any release from 23.3.35 to 23.13.1. It holds `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe`, `Manual_Install` (`APPLY_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results and `SHA256SUMS.txt`. |
| `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` | Extension 1.4.2 with its companion: spoken replies in the side panel. |

## What was asked and what is in this release

| # | Asked | Status |
|---|---|---|
| 1 | AI Agent page: an English voice request is only written, not spoken | **Done.** The reply is spoken in English with the Windows voice. |
| 2 | AI Agent page: an Arabic voice request is written in English and then spoken in Arabic | **Done.** The command is shown in English, the reply is spoken in Arabic with the offline Arabic voice (male or female as chosen in Voice settings). |
| 3 | The same in the browser extension | **Done.** Extension 1.4.2: the side panel speaks English replies with the PC's voice and Arabic replies through the companion's offline Arabic voice. Options > Spoken replies: on/off and Male/Female. |

## Tests

6 new tests, 109 regression tests (2 skipped), 32 installer tests (17 earlier releases), all passed.

## After installing

1. Run Setup (or `Manual_Install\APPLY_PATCH.cmd`), press Ctrl+F5 in the browser.
2. If the Arabic voice is not installed yet: `GET_ARABIC_VOICE.ps1` on a PC with Internet, then
   `INSTALL_ARABIC_VOICE.cmd` on the office PC (see README_FIRST.txt). It is used by both SkyGuard QA and the
   extension.
3. Extension: install the 1.4.2 zip as in README_FIRST_EXTENSION.txt. If the extension's companion was installed
   before the Arabic voice, run `INSTALL_ARABIC_VOICE.cmd` once more so it is copied to the companion too.
