# Test report: SkyGuard QA 23.15.0

Run on Linux (Python 3.13, Chromium), 2026-11-25, on the 23.15.0 tree.

## Full regression

`pytest tests/` (the extension end-to-end test excluded: it needs a desktop browser with the extension loaded):

| Result | Count |
|---|---|
| Passed | 1,200 |
| Failed | 3, then 0 (see below) |
| Skipped | 441 (installer tests of other versions, Windows-only tests, JMeter) |

The three failures were tests that described the 23.14.x behaviour this release changes on purpose, not regressions:

* `test_extension_130.py::test_versions_are_130` expected extension 1.4.3; the extension, its companion and the agent
  core are 1.5.0 now. Updated.
* `test_ui_231101.py::test_missing_information_is_asked_not_previewed` and
  `test_ui_231102.py::test_the_reported_conversation_in_the_page` expected the agent to **ask** for the vehicle, the
  first name, last name and ZIP. With 23.15.0 those values come from what was typed while teaching (the fix you asked
  for), so nothing is asked. The tests still cover the question flow, with remembering turned off for them; the
  filled case is covered by `test_ai_agent_memory_231500.py`. Both pass.

## New tests

* `tests/test_tfs_defect_fix_231500.py`: 5 passed.
  * the exact request the page sends (severity and priority as numbers) creates the draft; labels are accepted; a
    level outside 1 to 4 gets a message naming the field
  * the body is readable (numbered steps with their result, Expected and Actual, stopped run), not escaped JSON
  * both screenshots are listed and attached; no local path in the answer
  * "send now" creates the bug in the Azure DevOps simulator with both attachments
  * review, approve, publish pushes the draft to TFS with its attachments
  * performance runs, passed and failed: a defect with `qa-report.pdf` and `metrics.json`, created in TFS
* `tests/test_ai_agent_memory_231500.py`: 20 passed.
  * confirmation words in English and Arabic, and the sentences that must not count as a confirmation
  * values typed while teaching fill what a request leaves unsaid; the person's own last values win next time
  * "create 10 trips, one per day, drivers: ..." gives 10 records, 10 different dates in order, the drivers in turn
  * usual requests remembered with their count
  * the password and the authorization code typed while teaching go to the vault, masked, absent from the knowledge
    database file and from what the page is shown; "forget" removes the code; a one-time code is never captured
* `tests/test_arabic_voice_231500.py`: 14 passed (misheard Arabic "إفتاء الإشارات" opens Notifications, near page
  names, words close to nothing left alone, the word list used when the model's English leads nowhere).
* `tests/test_handsfree_and_name_231500.py`: 10 passed (Listen always in the page and the extension, imports, the
  recorder ending on a pause, the new name everywhere people see it, the page opened by voice under either name).
* `tests/test_cumulative_patch_231500.py`: see the installer section.

## Installer (offline)

`tests/test_cumulative_patch_231500.py`: **42 passed**, on real trees of every release from 23.3.35 to 23.14.1 (git
archive of each release commit, with a database and, from 23.5.0, the agent's knowledge database): apply, verify
(no FAIL, the new 23.15.0 check passing, knowledge schema 3 with the three new tables and earlier knowledge kept),
rollback, apply again; repair over 23.15.0; automatic restore after a failure; refusal of a wrong version and of a
changed patch file; the offline OCR runtime; the side-by-side install for a protected folder; the extension 1.5.0
update files; the installed Arabic voice and speech model kept and never downloaded; no network and no administrator
rights needed. The first run had 23 failures, all in the test's own expectations (knowledge schema 2, and two
"upgraded from" lines copied from the previous test); the installer output had no FAIL. The expectations were
corrected and the whole file passed.

## Not executed here

* Real TFS / Azure DevOps (a simulator was used), the microphone and real Arabic speech, the Windows wrappers
  (APPLY_PATCH.cmd / .ps1) and Setup.exe on Windows. See `KNOWN_LIMITATIONS.md`.
