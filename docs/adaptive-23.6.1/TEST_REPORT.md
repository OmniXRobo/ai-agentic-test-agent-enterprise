# SkyGuard QA 23.6.1: test report (actual runs)

**Run on:** 14 Nov 2026.
**Environment:** Linux container, Python 3.13, Playwright 1.63 with Chromium. Visible-browser tests ran under Xvfb.

Every result below comes from an actual run.

## What could not be tested here

- **trip.com itself.** The build environment's network proxy still blocks it (HTTP 403). This time the stand-in page
  (`tests/site/tripcom/`) was built from Trip.com's **real markup** (a public crawl of its home page and public
  scrapers that drive the real site; see `ROOT_CAUSE_REPORT.md`), and it reproduced your exact failure with 23.6.0
  before the fix. Trip.com changes its pages often, so **please run your recording again on your PC** after installing.
- **Windows itself:** the `.cmd` / `.ps1` wrappers were not run (no Windows here). They call the same
  `install_patch.py`, which was run on real 23.3.35, 23.4.0, 23.5.0 and 23.6.0 trees.
- **No real booking or purchase** was made.

## Reproduction before the fix (23.6.0 code)

Recording the Trip.com flight search on the stand-in (type "New Delhi", click "New Delhi - All airports India", two
calendar days, Children +, Adults +, cabin, Done, Search) gave:

```
click   #m-flight-poi-list > li          <- the suggestion, recorded first
fill    [data-testid="search_city_to0"]  New Delhi   <- the typing, recorded after it
hover   #m-flight-poi-list > li
...
click   #portal > div > div:nth-of-type(2) > div:nth-of-type(2) > button:nth-of-type(2)   <- Children +, by position
```

Playback: `FAILED at step 3: Element not found: New Delhi - All airports India ... self-healing found no confident
match` (best candidate 0.21), the same failure as the screenshot (step 9, 0.39).

## After the fix: the same recording

```
fill           [data-testid="search_city_to0"]  New Delhi
choose_option  [data-testid="search_city_to0"]  New Delhi - All airports India   (typed "New Delhi")
pick_date      start  TODAY+20d
pick_date      end    TODAY+26d
click          Set Children to 1
click          Set Adults to 2
select         Cabin class = Business
```

Playback: **PASSED**, and the results heading shows "Flights Dubai to New Delhi · <dates> · 2 adult, 1 child, 0 infant ·
Business".

## New tests: `tests/test_tripcom_playback_23610.py` (all passed)

| Test | What it proves |
|---|---|
| `test_record_and_play_trip_flight_search` | Real key presses and clicks recorded on the Trip.com stand-in become typing, "Choose ... (typed ...)", two date rules, "Set Children to 1", "Set Adults to 2"; playback passes and the search shows the chosen city, dates and passengers. |
| `test_record_and_play_trip_hotel_search` | Hotels: destination suggestion, the `c-calendar` (next arrow, number-only days), Rooms + and Children + made of plain spans, the child-age list; playback passes with the right dates (this found the "every day is the 1st" calendar bug). |
| `test_suggestion_context_of_an_older_recording` | A 23.6.0 recording with the suggestion click before the typing is recognised; the Flights tab and Search are not. |
| `test_older_trip_recording_plays_back` | **Your failing run**: `[data-testid="0"]` "New Delhi - All airports India" before "fill New Delhi", calendar days with yesterday's price in their label, a hover on the list. Passes; the typing step after the choice is reported as already done. |
| `test_older_trip_recording_plays_in_a_visible_browser_with_the_cursor` | The same, Headless off, with the visual cursor and highlight, as you run it. |
| `test_recording_without_the_typed_text_plays_back` | A recording where the typing was never captured (click the arrival box, then the suggestion): the suggestion's leading words are typed and the row chosen by text. The popular-cities panel's own `[data-testid="0"]` (Dubai) is not clicked by mistake. |
| `test_semantic_upgrade_rewrites_an_older_suggestion_click` / `test_upgraded_recording_plays_back` | Semantic upgrade turns the click into "Choose ... (typed ...)", moves the typing in front of it, and the upgraded script plays. |
| `test_counters_are_set_by_label_not_position` | Adults / Children / Infants are found by label; set from any starting value; the application's limit (Infants + disabled at 4) and an unknown counter give clear errors listing the counters. |
| `test_suggestion_rows_are_not_page_content` | With no list open nothing is matched; an airport row ("Indira Gandhi International Airport DEL") is chosen after typing "Delhi". |
| `test_hotel_calendar_skips_greyed_days_and_uses_the_arrow` | Yesterday (greyed, `is-disable` two levels up) is refused; a date three months ahead is reached with the icon-only arrow and chosen. |

## Full regression suite

`pytest tests/` (755 tests collected), run over the changed code:

| Result | Count |
|---|---|
| Passed | 723 |
| Skipped | 31 (Windows-only, or optional tools not installed) |
| Failed | 1: `test_adaptive_components_23600::test_older_recording_of_hotel_and_flight_search_plays_back`. A click on a whole search form whose text starts with the next step's typed text was taken for a suggestion. Fixed (a click on a form, or text that holds several rows, is not a suggestion) and re-run: see below. |

**Re-run on the final code** of every automation, recorder, calendar, healing, voice-automation and installer test file
(18 files, including the cumulative-patch installer tests on 23.3.35, 23.4.0, 23.5.0 and 23.6.0):
**146 passed, 0 failed**. The visible-browser test was added after that re-run and passed on its own run.

## Patch installation (offline)

See `evidence/offline_install_check.txt`: the zip and every file checked against SHA-256, then apply and verify on
23.3.35, 23.4.0, 23.5.0 and 23.6.0 installations with networking disabled, start and health check, rollback and apply
again, and an installation by an ordinary (non-administrator) user.
