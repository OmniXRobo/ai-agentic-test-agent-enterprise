# Test report: SkyGuard QA 23.13.1

Every result below comes from a run that actually happened on 21 November 2026. The JUnit files are in
`test-results/`. The 23.13.0 report is in `previous-23.13.0/TEST_REPORT.md`.

Environment: Linux container (Python 3.13, pytest), NSIS 3 for Setup.exe, and GitHub's Windows runner (Windows
PowerShell 5.1) for the Windows-only steps (pull request #60).

## 1. New in 23.13.1

`tests/test_arabic_voice_231300.py`: **16 passed**.

| Test | What it proves |
|---|---|
| `test_arabic_reply_is_spoken_by_the_offline_arabic_voice` | On a PC with English voices only, an Arabic reply is rendered by Piper; the text goes on stdin, never on the command line; speed maps to `--length_scale`; the render is cached. |
| `test_without_an_arabic_voice_arabic_is_never_read_by_an_english_voice` | No Arabic voice: 409 `VOICE_ARABIC_MISSING` with the install hint, not an English voice reading Arabic. |
| `test_a_windows_arabic_voice_is_preferred_when_installed` | A Windows Arabic voice (Naayf) is used before Piper. |
| `test_female_arabic_reply_uses_a_female_voice_or_kareem_at_a_higher_pitch` | Female follows the radio voice (Zira) or the setting; without a female model it is Kareem with a 1.32× higher pitch at the same speed; an installed female model is used as it is. |
| `test_windows_female_arabic_voice_and_onecore_voices` | Arabic OneCore voices (Hoda, Naayf) are listed; English OneCore voices do not change the English list; Female picks Hoda, Male picks Naayf. |
| `test_arabic_voice_gender_setting_is_saved` | The setting accepts "", male, female; anything else is refused (422). |
| `test_the_wake_name_in_arabic_is_recognised_in_any_spelling` (5 cases) | كابدان / كابتن / يا كابتن / كبتن / كابتين are taken as "Captain", including the reported "كابدان افتا صفا". |
| `test_ordinary_arabic_words_are_not_taken_for_the_wake_name` | Ordinary words are not taken for the name. |
| `test_misheard_command_words_still_translate` | "افتا صفا" → "open page". |
| `test_arabic_command_with_misheard_wake_name_reaches_the_command` | Through the API: the misheard name is removed, the command runs, the reply is spoken in Arabic. |
| `test_installed_multilingual_model_is_used_for_arabic` | `small.ar` is chosen for Arabic, `small.en` for English. |
| `test_dashboard_plays_arabic_replies_with_the_arabic_voice` | The dashboard code paths and the three helper scripts are present; the Piper SHA-256 is pinned. |

## 2. Voice and release regression

`test_arabic_voice_231300.py`, `test_release_231300.py`, `test_voice.py`, `test_voice_backend.py`,
`test_voice_security.py`, `test_faster_whisper.py`, `test_voice_guided.py`, `test_voice_automation.py`,
`test_voice_plans.py`, `test_voice_design.py`: **147 passed, 1 skipped** (`voice_and_release.xml`). One existing
assertion was updated: the radio-voices answer now also carries the Arabic voice status.

## 3. Installer

`tests/test_cumulative_patch_231301.py`: **31 passed** (`cumulative_patch_231301.xml`). Apply, verify, rollback and
apply again on real trees of all 16 earlier releases (23.3.35 to 23.13.0), the new verify check "Arabic voice replies
and speech model helpers (23.13.1)", repair over 23.13.1, detect, refusal of other versions, damaged files, the
OCR runtime, protected folders, no network.

## 4. Windows (GitHub Actions, pull request #60)

* `INSTALL_SPEECH_MODEL.cmd`: installs a model zip offline; a zip without a model is refused and changes nothing.
* `GET_ARABIC_VOICE.ps1` → `INSTALL_ARABIC_VOICE.cmd` → SkyGuard QA renders a male and a female Arabic reply with
  the real Piper and Kareem voice on a Windows machine that has English voices only.
* Setup.exe over 23.3.35, 23.11.1, 23.12.0 and 23.13.0, verify, rollback, APPLY_PATCH.ps1.

The result of these runs is on the pull request's checks.

## 5. Not tested

A real Windows Arabic voice (Hoda / Naayf) was not available; the OneCore path is tested with stand-ins
(KNOWN_LIMITATIONS.md). Arabic speech recognition with a microphone was not tested here (the user confirmed it works
with `small.ar` on the office PC).
