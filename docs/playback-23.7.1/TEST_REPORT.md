# SkyGuard QA 23.7.1: test report (actual runs)

**Run on:** 14 Nov 2026. **Environment:** Linux container, Python 3.13, Playwright with Chromium; visible-browser tests
under Xvfb. Every result below comes from an actual run.

## What could not be tested here

- **trip.com itself**: the build machine's network proxy blocks it (HTTP 403). The stand-in
  `tests/site/tripcom/flights.html` was built from what your recording SCR-9D41509DEA shows of Trip.com's current
  flight box (see `ROOT_CAUSE_REPORT.md`). Trip.com changes often, so **please run your script again on your PC**
  after installing.
- **Windows itself**: the `.cmd` / `.ps1` wrappers were not run here. They call the same `install_patch.py`, which was
  run on real 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1 and 23.7.0 trees. CI runs the test suite on Windows.
- No real booking or purchase was made.

## Your script, replayed as recorded

`tests/test_tripcom_playback_23610.py::test_tester_recording_of_14_november_plays_back` plays your steps 6 to 15
unchanged (the banner clicks 2 to 5 are left out; the stand-in has no banner):

| Step | Recorded | Before (23.6.1 code) | 23.7.1 |
|---|---|---|---|
| 7 | fill Departure "dubai international airport", no suggestion click | city put back (empty) | "DXB Dubai International Airport Dubai, United Arab Emirates" chosen |
| 10 | click `[data-testid="0"]` "DEL Indira Gandhi International Airport New Delhi, India" | played (type, then choose) | same, verified |
| 11, 12 | pick_date TODAY+8d, TODAY+14d (end label with a price) | played | same, verified |
| 14 | click "Adults 12+ years old 1" (positional li) | clicked the row, count stayed 1 | Adults + (the - is disabled at 1), Adults set to 2, verified |
| check | results "Flights Dubai to New Delhi · <dates> · 2 adult, 0 child, 0 infant · Economy" | FAILED | **PASSED** |

The same script also **passed in a visible browser** with the visual cursor and highlight, as you run it.

## New tests (all passed)

| Test | What it proves |
|---|---|
| `test_tester_recording_of_14_november_plays_back` | Your script plays end to end and the search shows Dubai, New Delhi, both dates and 2 adults. |
| `test_tester_recording_of_14_november_plays_in_a_visible_browser` | The same with Headless off, cursor and highlight. |
| `test_set_count_step_written_by_hand` | The new `set_count` action (`Adults=3`, `Children: 1`) sets the counters by label. |
| `test_ambiguous_older_counter_row_click_says_how_to_fix_it` | A row click where both + and - are possible stops with the exact fix (`set_count`, `Children=2`), never a guess. |
| `test_exported_python_sets_counters_and_chooses_typed_suggestion` | The downloadable pytest file plays `set_count` steps and recorded "Set Children to 1" steps. |
| `test_step_editor_keeps_semantic_actions_on_save` | The step editor lists `choose_option`, `set_count` and `dismiss`, and keeps an unknown action as it is. |
| `tests/test_cumulative_patch_23710.py` (11 tests) | The 23.7.1 package: contents, no network and no administrator rights, wrong version refused, changed file refused, automatic rollback after a failure, and apply / verify / rollback / apply again on **23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1 and 23.7.0**. |

## Full regression suite

`pytest tests/` on the merged 23.7.1 code: **759 passed, 0 failed, 52 skipped**. The skipped tests are Windows-only
tests, optional tools not installed here, and the 18 tests of the 23.6.1 and 23.7.0 packages, which this package
replaces (they build from the working tree, which is now 23.7.1). The tests added after that run (visible browser,
step editor) and the browser-extension test change passed on their own run: `test_tripcom_playback_23610.py` and
`test_extension_e2e.py`, **18 passed**.

## Patch installation (offline)

`evidence/offline_install_check.txt`: the zip and every file checked against SHA-256; apply and verify on 23.3.35,
23.4.0, 23.5.0, 23.6.0, 23.6.1 and **23.7.0** installations with networking disabled; start and health check
(`"version": "23.7.1"`); rollback to 23.3.35 and to 23.7.0, then apply again; and an installation by an ordinary
(non-administrator) user. Every check PASS.
