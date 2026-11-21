# Test report: SkyGuard QA 23.13.3

Every result below comes from a run that actually happened on 21 November 2026. The JUnit files are in
`test-results/`. The 23.13.2 report is in `previous-23.13.2/TEST_REPORT.md`.

Environment: Linux container (Python 3.13, pytest), NSIS 3 for Setup.exe, and GitHub's Windows runner for the
Windows-only steps (on the pull request).

## 1. New in 23.13.3

`tests/test_arabic_page_231303.py`: **18 passed**.

| Test | What it proves |
|---|---|
| `test_spoken_open_page_is_translated_literally` (5 spellings) | إفتاحة صفحة, افتاحة صفحة, افتح صفحة, افتا صفا and إفتاحه صفحه all become "open page", even with a local AI that would answer "Open the booking page"; the model is not asked. |
| `test_longer_arabic_still_goes_to_the_local_ai_with_a_literal_prompt` | Longer sentences go to the local AI; its prompt forbids adding a page name and no longer gives "booking page" as an example. |
| `test_page_names_are_what_a_person_would_say` | The pages learned on the reported site give "Book flights, Manage booking": no addresses, no title tails, no duplicates, no sign-in page. |
| `test_open_page_asks_a_clean_question_with_buttons` | "open page" asks "Which page of Emirates (Test) do you mean? I know: Book flights, Manage booking." with three buttons, and each button opens its page. |
| `test_the_question_is_spoken_in_arabic_for_an_arabic_request` | The question is spoken in Arabic for an Arabic request. |
| `test_page_and_extension_show_the_choices_as_buttons` | The AI Agent page and the side panel render the buttons and send them as commands. |
| `test_the_page_question_is_asked_in_arabic_without_the_local_ai` | "Which page?" is asked in Arabic for an Arabic request (also without the local AI) and in English for English. |
| `test_every_task_step_has_an_arabic_line` | Preview ready, done, partly done, failed, stopped, cancelled and waiting each have an Arabic sentence. |
| `test_task_steps_and_button_answers_are_spoken_in_the_request_language` | `/api/ops/simple/say` answers in English or Arabic and refuses other kinds; a button under an Arabic question is answered in Arabic. |
| `test_page_and_extension_speak_task_steps_and_show_arabic` | The page and the side panel speak the task's steps and show the Arabic answer; the companion has `/v1/voice/line`. |
| `test_changing_addresses_are_the_same_page` | abc.com/12345 and abc.com/by123 are the same page; session folders, long tokens and `;jsessionid` too; ordinary words stay. |
| `test_a_learned_page_opens_with_the_changing_part_of_today` | abc.com/12345/booking learned, abc.com/by123/home open: abc.com/by123/booking; not rebuildable: start page; another site is never mixed in. |
| `test_connecting_the_new_address_finds_the_learned_application` | Connect & Learn with the new address, with or without www., finds the learned application. |
| `test_tasks_and_the_extension_use_the_address_of_today` | Task steps, the page's navigation and the extension rebuild learned addresses. |

## 2. Regression

`test_arabic_page_231303.py`, `test_spoken_replies_231302.py`, `test_arabic_voice_231300.py`, `test_extension_130.py`,
`test_extension_static.py`, `test_missing_info_231102.py`, `test_ai_agent_repair_231101.py`, `test_release_231300.py`,
`test_companion_security.py`, `test_voice.py`, `test_extension_120.py`, `test_ai_agent_231200.py`,
`test_agent_simple_api_231000.py`, `test_autopilot_231000.py`, `test_ops_agent_core.py`,
`test_passive_learning_23700.py`, `test_autolearn_patch_23700.py`, `test_adaptive_components_23600.py`:
**302 passed, 11 skipped** (`arabic_page_and_regression.xml`). One
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

The real Emirates site, a real website with changing addresses, and a real microphone and speaker: the tests use the
page names from the screenshot and example addresses.
