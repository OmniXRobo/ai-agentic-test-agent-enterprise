# Test report: SkyGuard QA 23.13.2

Every result below comes from a run that actually happened on 21 November 2026. The JUnit files are in
`test-results/`. The 23.13.1 report is in `previous-23.13.1/TEST_REPORT.md`.

Environment: Linux container (Python 3.13, pytest), NSIS 3 for Setup.exe, and GitHub's Windows runner (Windows
PowerShell 5.1) for the Windows-only steps (on the pull request).

## 1. New in 23.13.2

`tests/test_spoken_replies_231302.py`: **6 passed**.

| Test | What it proves |
|---|---|
| `test_english_request_gets_an_english_spoken_reply_without_the_address` | An English request gets an English line to speak; web addresses are not read aloud; the line is at most 500 characters. |
| `test_arabic_request_gets_arabic_from_the_local_ai_or_a_short_arabic_line` | An Arabic request gets the reply in Arabic: translated by the local AI when it runs, otherwise a short Arabic sentence for the reply type (preview, page opened, sign-in, error ...). |
| `test_ai_agent_api_returns_the_line_to_speak` | `/api/ops/simple/command` returns `speak` and `language` (en for English, ar for Arabic) beside the English text. |
| `test_companion_renders_arabic_male_and_female` | The extension's companion renders an Arabic reply with Piper (text on stdin), male and female (Kareem at a higher pitch), and returns the WAV. |
| `test_companion_without_an_arabic_voice_says_how_to_install_it` | No Arabic voice: 409 `VOICE_ARABIC_MISSING` naming `INSTALL_ARABIC_VOICE.cmd`; an English voice never reads Arabic. |
| `test_pages_speak_the_reply` | The AI Agent page and the side panel speak a voice request's reply; the options page has the spoken reply and voice settings. |

## 2. Regression

`test_spoken_replies_231302.py`, `test_arabic_voice_231300.py`, `test_extension_130.py`, `test_extension_static.py`,
`test_companion_security.py`, `test_agent_simple_api.py`, `test_release_231300.py`, `test_voice.py`:
**109 passed, 2 skipped** (`spoken_and_regression.xml`).

## 3. Installer

`tests/test_cumulative_patch_231302.py`: **32 passed** (`cumulative_patch_231302.xml`). Apply, verify, rollback and
apply again on real trees of all 17 earlier releases (23.3.35 to 23.13.1), the new verify check "Spoken AI Agent
replies in English and Arabic (23.13.2)", repair over 23.13.2, detect, refusal of other versions, damaged files, the
OCR runtime, protected folders, no network.

## 4. Windows (GitHub Actions)

Setup.exe over 23.3.35, 23.11.1, 23.12.0, 23.13.0 and 23.13.1, verify, rollback, APPLY_PATCH.ps1, and the real
Arabic voice download and render. The result is on the pull request's checks.

## 5. Not tested

Sound from a real speaker on the office PC, and the extension inside a real Edge or Chrome with a microphone. The
side panel's speech code is checked statically and the companion's Arabic render is tested with a stand-in Piper.
