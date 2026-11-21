# Test report: SkyGuard QA 23.13.3

Every result below comes from a run that actually happened on 21 November 2026. The JUnit files are in
`test-results/`. The 23.13.2 report is in `previous-23.13.2/TEST_REPORT.md`.

Environment: Linux container (Python 3.13, pytest), NSIS 3 for Setup.exe, and GitHub's Windows runner for the
Windows-only steps (on the pull request).

## 1. New in 23.13.3

`tests/test_arabic_page_231303.py`: **10 passed**.

| Test | What it proves |
|---|---|
| `test_spoken_open_page_is_translated_literally` (5 spellings) | إفتاحة صفحة, افتاحة صفحة, افتح صفحة, افتا صفا and إفتاحه صفحه all become "open page", even with a local AI that would answer "Open the booking page"; the model is not asked. |
| `test_longer_arabic_still_goes_to_the_local_ai_with_a_literal_prompt` | Longer sentences go to the local AI; its prompt forbids adding a page name and no longer gives "booking page" as an example. |
| `test_page_names_are_what_a_person_would_say` | The pages learned on the reported site give "Book flights, Manage booking": no addresses, no title tails, no duplicates, no sign-in page. |
| `test_open_page_asks_a_clean_question_with_buttons` | "open page" asks "Which page of Emirates (Test) do you mean? I know: Book flights, Manage booking." with three buttons, and each button opens its page. |
| `test_the_question_is_spoken_in_arabic_for_an_arabic_request` | The question is spoken in Arabic for an Arabic request. |
| `test_page_and_extension_show_the_choices_as_buttons` | The AI Agent page and the side panel render the buttons and send them as commands. |

## 2. Regression

`test_arabic_page_231303.py`, `test_spoken_replies_231302.py`, `test_arabic_voice_231300.py`, `test_extension_130.py`,
`test_extension_static.py`, `test_missing_info_231102.py`, `test_ai_agent_repair_231101.py`, `test_release_231300.py`,
`test_companion_security.py`, `test_voice.py`: **172 passed, 2 skipped** (`arabic_page_and_regression.xml`). One
existing assertion was updated: a short Arabic command is now translated by the word list ("open booking page"), and
the model path is checked with a longer sentence.

## 3. Installer

`tests/test_cumulative_patch_231303.py`: **34 passed** (`cumulative_patch_231303.xml`). Apply, verify, rollback and
apply again on real trees of all 18 earlier releases (23.3.35 to 23.13.2), the new verify check "Arabic commands
translated literally, page choices (23.13.3)", repair over 23.13.3, detect, refusal of other versions, damaged files,
the OCR runtime, protected folders, no network.

## 4. Windows (GitHub Actions)

Setup.exe over 23.3.35, 23.11.1, 23.12.0, 23.13.0, 23.13.1 and 23.13.2, verify, rollback, APPLY_PATCH.ps1. The result
is on the pull request's checks.

## 5. Not tested

The real Emirates site and a real microphone: the test uses the page names from the screenshot of the reported case.
