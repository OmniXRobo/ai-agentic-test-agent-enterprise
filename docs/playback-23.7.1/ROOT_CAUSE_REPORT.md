# SkyGuard QA 23.7.1: root causes (your Trip.com re-test of 14 Nov 2026)

**Reported:** playing script SCR-9D41509DEA (Flights, Dubai to New Delhi, two dates, 2 passengers, Search) still did
not choose the airport in the drop-down list for From and To, did not choose the dates and did not change the
passengers. Manual choice and self-healing got a run through, but the next run stopped again (step 10,
`[data-testid="0"]`, best candidate 0.27).

## Root cause 0: the fixes were not on your PC

Your PC runs **23.7.0** (Operations Agent learning). 23.7.0 was built on 23.6.0, before the 23.6.1 playback fixes, so
your installation had none of them. The Python test on your script page proves it: it still had the 23.6.0 helper
`choose(page, locator, text, timeout)`. 23.6.1 has `choose(..., typed=None)`. Applying the 23.6.1 package then failed
because its installer only accepted 23.3.35 to 23.6.0 (your screenshot: "found 23.7.0").

**Fix:** 23.7.1 contains 23.7.0 and 23.6.1 and installs on 23.7.0.

## Root causes found by replaying your script on 23.6.1 code

trip.com cannot be reached from the build machine (HTTP 403), so `tests/site/tripcom/flights.html` was built from what
your recording shows of Trip.com's current flight box: `input[role=combobox]` named "Departure airport or city" in a
`div[role=group]` with the same name; result rows `div[role=button][data-testid="0"]` under
`#m-flight-poi-list > div > ul > li` reading "DEL Indira Gandhi International Airport New Delhi, India"; the calendar
opening by itself after the arrival city, with days named "Sunday, November 22, 2026, Select the start date you want"
and "Saturday, November 28, 2026, The price is AED 1.36k, Select the end date you want"; the passengers button
"1 adult, Economy" opening `ul > li` rows "Adults 12+ years old [-] 1 [+]" with its own Search button
`[data-testid="passengernum_cabin_search"]`. Your 15 steps were then replayed exactly as recorded.

1. **Departure (step 7).** You typed "dubai international airport" and clicked the arrival box without clicking a
   suggestion. Trip.com then puts back the last chosen city. 23.6.0 tried to choose the suggestion after typing, but
   it read the list too early: the "Popular cities" panel was still open, it had no match, so it gave up before the
   results list arrived.
2. **Matching rows built from parts.** Trip.com's row is three parts side by side: "DXB", "Dubai International
   Airport", "Dubai, United Arab Emirates". The row's first line was only "DXB", and the full-text test compared whole
   texts, so "dubai international airport" matched nothing.
3. **Passengers (step 14).** The recorder of 23.6.0 / 23.7.0 kept the press of the icon-only + as a click on the whole
   Adults row. Clicking a row does nothing, so the count stayed at 1 and the run still "passed".
4. **Dates (steps 11 and 12).** With 23.6.1 code these already played: the day is found by its full date from the rule
   (TODAY+8d, TODAY+14d), and the price in the recorded label is ignored. On 23.7.0 they failed because the 23.6.1
   calendar fixes were missing (root cause 0).
5. **Arrival suggestion (step 10).** With 23.6.1 code this already played: the click follows the typing of
   "del indira gandhi international airport", so it is played as "type, then choose the row with this text".

## Fixes

- **Choosing after typing** (`components.commit_autocomplete`): waits for the list that answers the typed text (up to
  4 s, and only while the page is still loading), then chooses the row that holds the typed words; if a list is open
  but the site ignored the filled text, the text is typed key by key and the list is read again. Ambiguous matches are
  reported as choices.
- **Matching** (`findOption` in `js/components.js`): a new rule matches the typed words, as a phrase, in the row's text
  joined from all its parts. It runs after the exact, first-line, label and prefix rules and before word similarity.
- **Counter row clicks from older recordings** (`Runner._counter_row_click`): the text "Adults 12+ years old 1" gives
  the counter (Adults) and the number shown when it was pressed (1). If at that number only + is enabled, the step is
  "set Adults to 2"; only -, "set to 0"; both, the run stops with: change the step to `set_count` with Value
  `Adults=2`, or record it again. It never applies when the recorded element is the + or - itself.
- **set_count step** (`dsl`, `runner`, step editor, `pytest_export`): Value `label=number`.
- **Step editor**: `choose_option`, `set_count` and `dismiss` are in the Action list, and an action that is not in the
  list is shown as it is, so Save never changes it.

## Not changed

- No site-specific code: nothing refers to Trip.com; the rules read text, structure and state.
- No coordinates are recorded or replayed; no page scripts are run; offline; no new packages; no administrator rights.
