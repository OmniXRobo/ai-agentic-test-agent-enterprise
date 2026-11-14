# Changelog: SkyGuard QA 23.7.1 (cumulative: 23.4.0 + 23.5.0 + 23.6.0 + 23.6.1 + 23.7.0 + 23.7.1)

One package installs on **23.7.0** (your version) and also on 23.3.35, 23.4.0, 23.5.0, 23.6.0 or 23.6.1. It brings the
installation to 23.7.1. You do not need to apply any earlier patch first. The 23.7.0 notes (Operations Agent learns by
watching) are under `previous-23.7.0/`, and the 23.6.1 notes (Trip.com suggestion lists, calendars, counters) are under
`previous-23.6.1/`.

## Why 23.7.1

On 14 Nov 2026 your PC had 23.7.0. 23.7.0 was built on 23.6.0, so it did not have the 23.6.1 playback fixes, and the
23.6.1 installer refused to run on it ("This patch installs on 23.3.35, 23.4.0, 23.5.0 or 23.6.0 (found 23.7.0)").
23.7.1 joins the two: all of 23.7.0, all of 23.6.1, and the fixes below found by replaying your script SCR-9D41509DEA.

## Fixed (Guided Browser Automation)

- **Departure city typed but never chosen** (your step 7, "dubai international airport", then a click on the arrival
  box): Trip.com puts back the last chosen city when the field loses focus. Playback now waits for the results list
  (not the "Popular cities" panel shown on focus) and chooses the row that holds the typed words,
  "DXB Dubai International Airport Dubai, United Arab Emirates". When the site ignores the filled text, it is typed key
  by key. Several matching rows are reported as choices, never guessed.
- **Rows laid out in parts** ("DXB | Dubai International Airport | Dubai, United Arab Emirates"): the typed words are
  matched across the parts of the row, so "dubai international airport" and "del indira gandhi international airport"
  find their airport and "Al Maktoum International Airport, Dubai" is not taken by mistake.
- **Passengers + recorded as a click on the whole row** (your step 14, "Click Adults 12+ years old 1",
  `... > ul > li:nth-of-type(1)`): the row text names the counter and the number it showed (1). When only one of + and
  - could have been pressed at that number (Trip.com's - is disabled at 1 adult), the step plays as "set Adults to 2"
  and is verified. When both could have been pressed, the run stops and says exactly how to write the step.
- **Step editor:** steps written by Semantic upgrade (`choose_option`) and `dismiss` steps were not in the Action list,
  so pressing Save turned them into `goto`. They are kept now.

## Added

- **`set_count` action** for the step editor: Value `Adults=2` (also `Children: 1`, `Infants to 1`). It presses the
  counter's + or - by its label until it shows the number, checking after every press, and reports the application's
  limit. The exported pytest file plays it too, and also plays recorded "Set Children to 1" steps.
- Trip.com flights stand-in `tests/site/tripcom/flights.html`, modelled on your recording (combobox fields, suggestion
  rows `div[role=button][data-testid="0"]`, a field that reverts without a choice, calendar day labels with and without
  price, the passengers list with its own Search button), and a test that replays your 15 steps.

## Included from 23.6.1 (your PC did not have these)

- The typed text is recorded before the suggestion it produced; suggestion rows with no role and generated class names
  are recorded and played as "Choose ... (typed ...)"; recordings made before 23.6.1 play as "type, then choose".
- Calendars: the hotel `c-calendar` day is read correctly (not the 1st), greyed past days are refused, and a recorded
  day whose label holds a price is never waited on.
- Counters: + and - are recorded as "Set Children to 1" and played by label.

## Included from 23.7.0

- The Operations Agent learns applications by watching (see `previous-23.7.0/`), browser extension and companion 1.1.0.

## Packaging

- Version 23.7.1. Installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1 or 23.7.0, offline, without administrator
  rights, with backup, automatic rollback on failure, verify and rollback. File list `PATCH_FILES_23.7.1.json`.
- No change to the main database (still 18 migrations); the agent knowledge database stays at schema 2 (23.7.0).
- `opentelemetry-api` in the offline wheel set is 1.45.2 (CVE-2026-126360 fixed); no other package changes.
