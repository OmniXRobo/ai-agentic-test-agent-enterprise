# Security report: SkyGuard QA 23.11.2 and extension 1.3.1

Scope: everything 23.11.2 changes since 23.11.1 (commit `8e8df0d`). For 23.11.1 and earlier, see
`previous-23.11.1/SECURITY_REPORT.md` and the reports under `previous-*`.

## 1. Tools run (17 November 2026)

| Check | Tool | Result |
|---|---|---|
| Python static analysis of the changed Python files (`autopilot.py`, `secrets_guard.py`, `sessions.py`, `executor.py`, `authz.py`, `install_patch.py`) | `bandit 1.9.4 -ll` | 3 findings, all on lines **not changed** in 23.11.2 (`executor.py:102`, `:109` and `install_patch.py:181`: SQL built from the code's own table and column names, reviewed in earlier releases). **No finding on a changed line.** |
| Secrets in every changed line | Regular-expression scan for private keys, cloud and GitHub tokens, JWTs, API keys and password assignments | **No secrets.** |
| New package dependencies | `requirements*.txt` and the extension's offline runtime list | **None.** |

## 2. What changed for security, and the tests that show it

| Change | Why it is safe | Test |
|---|---|---|
| Answers are read and kept with the pending question | An answer passes the secret check first, like any message, so a password or code typed as an answer is withheld and never kept. Values are only text for the task's own fields; a list field accepts only one of its options. The model is not involved. | `test_answers_in_the_persons_own_form`, `test_a_list_field_takes_only_one_of_its_options`, `test_h_passwords_in_chat_are_refused` (23.11.1, still passing) |
| "Zip/Postal Code: 54000" is not a secret | Only a "code" preceded by an ordinary word (postal, zip, product, order, ...) is exempt. A code on its own, or after OTP, driver, PIN, verification, is still stopped, in Python and in the page's and the extension's JavaScript. | `test_partial_answers_are_kept_and_only_the_rest_is_asked`; the installer check *Missing information follow-up (23.11.2)* tests "the code is 482913" is still stopped; the existing secret tests |
| A sign-in confirmed by the application replaces a displayed identity in a narrow case | Only when the agent itself submitted the saved account's credentials and the application accepted them; only when no identity rule was learned (a displayed different user still stops the run as a mismatch); only for operations no role restricts; never in production mode. The account check (the stage's own account), the approved workflow, the confirmation of this exact preview and the record checks are unchanged. The run log says which kind of identity was used. | `test_a_confirmed_sign_in_is_enough_only_for_unrestricted_operations`, `test_f_wrong_role_is_refused` and the other role tests of 23.11.1 |
| Suggestions no longer repeat answers | A value the person typed ("muhammad", "54000") is not offered back as a suggestion. | `test_answers_are_not_offered_as_suggestions` |

## 3. Extension and companion 1.3.1

* Only the shared secret check (`lib/secret-check.js`, identical to SkyGuard's `web/js/secret-check.js`) and the agent
  core changed. Permissions, routes, pairing and the local channel are unchanged from 1.3.0.
