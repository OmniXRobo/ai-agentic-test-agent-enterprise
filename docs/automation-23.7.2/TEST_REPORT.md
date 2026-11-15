# SkyGuard QA 23.7.2: test report (actual runs)

**Run on:** 15 Nov 2026. **Environment:** Linux container, Python 3.13, Playwright with Chromium (headless). Every
result below comes from an actual run.

## What could not be tested here

- **saucedemo.com itself**: the build machine's network proxy blocks it (HTTP 403). The stand-in
  `tests/site/saucedemo/` reproduces its pages (sign-in, inventory, cart, checkout step one and two, confirmation)
  with the same element ids, `data-test` attributes and texts, and the same user `standard_user` / `secret_sauce`.
  **Please regenerate TC1532 and TC1523 on your PC** after installing (Script Inventory > Regenerate / Update), with
  the saucedemo.com credentials saved under Application Credentials.
- **Qwen2.5-coder 3B itself**: the AI server is replaced by a test double. TC1532 and TC1523 need no AI request at
  all (every step is read by the step reader); the double answers with the one-step mapping your model gave, to prove
  that answer can no longer become the whole script.
- **Windows itself**: the `.cmd` / `.ps1` wrappers were not run here. They call the same `install_patch.py`, which was
  run on real 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0 and 23.7.1 trees.

## Your two Test Cases, generated and played

| Test Case | Before (23.7.1) | 23.7.2 |
|---|---|---|
| TC1532 User completes an online purchase (6 steps) | 1 step (`open()`), PASSED | 6 steps titled as the Test Case: open, `login(...)`, products shown, add to cart (badge "1"), cart, checkout information, Finish, "Thank you for your order!". **Play: PASSED, 6 steps.** |
| TC1523 Customer can enter the required checkout information (8 steps) | 1 step (`open()`), PASSED | Precondition (sign in, add a product, cart, Checkout, checkout page check), then First Name, Last Name, Postal Code filled and checked; Address, City, State, Email, Phone written as NOT AUTOMATED with the reason. **Play: 3 steps PASSED + precondition, 5 NOT AUTOMATED.** |

## New tests (all passed)

| Test | What it proves |
|---|---|
| `test_every_step_of_both_test_cases_is_understood` | Every step of TC1532 and TC1523 and the TC1523 precondition are read into intents and checks without the AI. |
| `test_step_phrasings` | Common ways of writing steps (navigate, log in, enter 'X' in the Y field, select, check, click, add to cart, proceed to, complete) are read. |
| `test_sentences_with_several_actions_are_left_for_the_ai` | A sentence the patterns cannot read safely goes to the AI, one step at a time. |
| `test_a_one_step_answer_can_no_longer_become_the_whole_script` | Your model's answer (only "Navigate"): every other Test Case step is written as a flagged step, none disappears. |
| `test_tc1532_is_automated_from_login_to_the_order_confirmation_and_passes` | Full generation job + Play on the stand-in: 6 steps, Page Object `LoginPage.login`, no AI call, PASSED. |
| `test_tc1523_sets_up_its_precondition_and_flags_the_fields_saucedemo_does_not_have` | Precondition set up, 3 fields filled and checked, 5 flagged; Play reports PASSED / NOT_AUTOMATED. |
| `test_without_credentials_the_login_is_flagged_with_what_to_do` | With no saved credentials the log says to save them; nothing is invented. |
| `tests/test_cumulative_patch_23720.py` (6 tests) | The 23.7.2 package: contents, no network and no administrator rights, wrong version refused, changed file refused, automatic rollback, and apply / verify / rollback / apply again on **23.3.35 to 23.7.1**. |

Updated test: `test_knowledge.py::test_agentic_chain_reads_requirements_discovers_and_generates_scripts` now expects
the steps to be walked in the browser (the AI mapping is only asked for steps the walk leaves open).

## Full regression suite

`pytest tests/`: **799 passed, 0 failed, 65 skipped** in 31 minutes. The skipped tests are Windows-only tests,
optional tools not installed here, and the tests of earlier packages that this package replaces.
