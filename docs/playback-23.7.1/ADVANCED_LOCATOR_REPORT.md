# SkyGuard Guided Browser Automation: advanced locator fix (report against your specification of 15 Nov 2026)

**Package:** `SkyGuard_Guided_Browser_Automation_Advanced_Locator_Fix.zip` (application version 23.7.1, cumulative).
**Where results come from.** trip.com cannot be opened from the build machine (its network proxy answers HTTP 403).
Every result in this report is marked with where it was produced:

- **[your PC, trip.com]**: from your own runs on trip.com, read from the run records your site diagnostic sent back
  (RUN-20261114-151722, -164528, -164836) and the screenshots of 14 Nov, 18:45.
- **[fixture]**: from tests on this machine against a local copy of trip.com's flight box built from your recordings
  (`tests/site/tripcom/flights.html`), and against small generic test pages.

No result here was simulated. The real-site check of this package is the new **`VERIFY_LIVE_SITE.cmd`** (section 6): it
runs on your PC against trip.com and writes `REPORT.md` with every step's result. Until you have run it, this package
is **not yet verified on trip.com**.

## 1. Root causes found (in the order they were found)

| Control | Root cause | Evidence | Fixed in |
|---|---|---|---|
| All | Your PC had 23.7.0, which was built on 23.6.0 and lacked every 23.6.1 playback fix; the 23.6.1 installer refused 23.7.0. | your screenshot "found 23.7.0" [your PC] | 23.7.1 cumulative installer |
| From (typed, no suggestion clicked) | After typing, the suggestion choice read the list too early (the "Popular cities" panel, not the results) and gave up. | replay of SCR-9D41509DEA [fixture] | `components.commit_autocomplete` waits for the answering list, retypes key by key |
| From / To rows | Rows built from parts ("DXB / Dubai International Airport / Dubai, UAE") never matched the typed phrase as a whole. | [fixture] | `findOption` phrase-across-parts rule |
| From (clicked DXB) | "was clicked but the field does not show it": the input matches `role=combobox[name=...]` only while focused, and shows only "Dubai" after the choice; the check read nothing. | RUN-20261114-105553 [your PC] | the field is pinned to the element; the choice is checked in the field's box (code or a distinctive word) |
| From (Manila, popular list) | "Neither the list field nor an open list with 'Manila' was found": the city was clicked in the list that opens on focus, right after "Click Flights"; the recorded locator matched nothing; nothing was opened or typed. | RUN-20261114-151722 [your PC] | `Runner._choice_field`: field by name, label, placeholder, box, self-healing; then open and type |
| Passengers | The + was recorded as a click on the whole "Adults 12+ years old 1" row. | replay [fixture] | `_counter_row_click`, `set_count` |
| Start date (1) | "shows the month of 23 November 2026 but not that day": the step stopped on the first reading. | RUN-20261114-164528 and -164836 [your PC]; in both runs every other step passed by itself | re-read while drawing; find the day by its recorded label |
| Start date (2) | "days read: 16, 18": the calendar was closed; the Depart and Return boxes (16 and 18 Nov, named in full) were read as calendar days, so the calendar was never opened. | screenshots of 14 Nov, 18:45 [your PC] | a day must show its day number; open the date field when no real month is shown |
| From (typed twice) | Step 8 paused with "Several suggestions match 'dubai'" although step 12 of the same script chooses DXB. | screenshots of 14 Nov, 18:38 [your PC] | `_later_sets_field` |
| Return date | No failure of its own was seen on trip.com: it passed in both runs of 14 Nov afternoon [your PC]. It uses the same calendar code, so causes (1) and (2) applied to it too. | | same fixes |
| Search | Passed in every run that reached it [your PC]. A Search click was reported as passed because it was clicked. | | the run now waits for the page to answer (section 3.6) |

None of the failures came from coordinates (none are recorded or replayed), frames, or the browser being different
between recording and playback.

## 2. What the engine does now, against your specification

Your specification asks for many capabilities; most were already in SkyGuard and were extended, a few are new, and a
few are not done. Nothing below is claimed beyond what the tests listed show.

### 2.1 Multi-strategy object identification (spec 3, 7)

Order used by Play for a recorded element (`Runner._locate`, `Runner._quick`):

1. The recorded selector (Playwright CSS, text or role selector; shadow DOM is pierced by Playwright CSS).
2. The recorded alternates (built by the recorder from id, name, test id, role and name, text, stable classes,
   relative structure).
3. **Stable attributes** recorded with the element: test id (not a row position like "0"), id (not generated: no long
   number runs or colons), `aria-label`, placeholder. Each only when it matches one element.
4. A locator correction a person approved earlier (AURA memory).
5. **Self-healing** (`healing.rank`): scores every candidate on the page by tag, role, accessible name, text, label,
   placeholder, test id, id, classes, parent, ancestors, siblings, nearby text and form; a candidate is used only
   when it is confident, clearly ahead of the next, matches exactly one element and is visible; close calls go to a
   person ("needs confirmation"). A validated replacement is saved with its evidence and applied to the script only
   when a person applies it; the earlier locator stays in the alternates and the script's version history.
6. **New: user-facing locators** (`Runner._semantic_candidates`), when self-healing finds no confident replacement:
   `get_by_role(role, name)`, `get_by_label`, `get_by_title`, `get_by_alt_text`, `get_by_text` (short texts without
   numbers). Each only when it matches exactly one element. They come after self-healing so that a confident healing
   still records a fix that can be applied to the script.

For list fields (From / To) a further search by meaning runs first: the field's name as a combobox, as its own
`aria-label` or its box's, its label, its placeholder, the input inside a recorded box.

Not done: relative XPath and absolute XPath generation, and "above / below / left of / right of" bounding-box
locators. Structural context (parent, ancestors, siblings, nearby text, form) is used for scoring in self-healing, not
as separate locators.

### 2.2 Autocomplete handler (spec 4): `components.choose_option`, `commit_autocomplete`, `findOption`

Locate and pin the field; read the list that answers it (by `aria-controls`, `aria-activedescendant`, the nearest
pop-up, or rows drawn next to the field on a layer above the page, so lists with no roles and generated classes work);
if the option is not there, clear and type the recorded text key by key (then the start of the option, then the whole
option); match by exact text, first line, label, prefix, the typed phrase across the row's parts, then word similarity;
**several matches stop the run and offer them as choices, never a guess**; click with Playwright's normal click; verify
the field, its box (airport code or a distinctive word) or that the list closed. The recorder stores the field, the
typed text, the list kind and the chosen row (`semantic.typed`, `list`, `option_selector`).

### 2.3 Calendar and date range (spec 5): `components.pick_date`, `findDay`, `dayCells`

Dates are stored as rules (`TODAY+9d`) or ISO dates, never as day numbers. A day is identified by its full date: from
its own label or date attribute, or from its number plus the month heading of its own panel (two visible months are
read separately; adjacent-month days are ignored; greyed and past days are refused). The calendar is opened from the
date field for the right role (start / end) when needed, moved month by month with its own next / previous control,
and the choice is verified (the day shows as chosen, or a date field shows the date). If the day is not read, it is
looked up by its recorded label wording rebuilt for the new date. A failure names the days that were read.

### 2.4 Semantic recording (spec 6)

Already in the recorder since 23.6.x: "Choose 'DXB ...' (typed 'dubai')", "Choose the start date Current system date
+ 9 days", "Set Adults to 2", each with the element's fingerprint (text, accessible name, role, tag, input type, label,
placeholder, name, test id, classes, parent, ancestors, siblings, nearby text, frame, form) and alternates. Steps can
be edited in the step editor (Action list includes `choose_option`, `pick_date`, `set_count`, `dismiss`).

### 2.5 State-aware waiting (spec 9)

Waits are on conditions: element visible, list answering the typed text, page quiet (`settle`: no DOM changes for a
short time, bounded), calendar drawn, counter changed after each press, and now Search answered. Fixed sleeps remain
only as short polling intervals inside bounded loops.

### 2.6 Post-action verification (spec 7, 10)

Every list choice, date and counter is verified after the action. **New:** a click on Search / Find flights passes only
when the page answers within 30 s: a new address, a new tab, or new content drawn in place; otherwise the step fails
with that message (`Runner._search_answered`).

### 2.7 Failure recovery (spec 13)

In a visible run started by you, a step that cannot be done confidently pauses with the browser open: do it yourself and
continue, retry, skip, pick one of the offered choices, or stop. **New:** at every pause or failure the page is
captured for diagnosis (section 2.8).

### 2.8 Object inspection and diagnostics (spec 3E)

**New:** every failed or paused step saves `diag-step-NN.json` (what the component engine read: focused field and its
box, open lists and their rows, the rows next to the field, the option it would choose, calendar months and days read,
date fields, counters, plus the recorded fingerprint), `diag-step-NN.aria.yml` (accessibility tree) and
`diag-step-NN.html` (the page without scripts or event handlers; password fields blank). On the run page, a failed
step has **"What the automation saw"**, which shows this as a table, with links to the full files. The site diagnostic
(`DIAGNOSE_SITE.cmd`) adds these files from your latest runs to its ZIP. Captured pages are served only as text
downloads, never as pages of SkyGuard.

### 2.9 AI assistance (spec 3D, 8)

Qwen (local) is not used to choose or click elements. Self-healing is deterministic and every candidate is validated
on the live page. No passwords, OTPs or payment data are sent anywhere. **Not done:** OCR / computer-vision
recognition (no local OCR engine ships with SkyGuard, and adding one would add packages); screenshots are used as
evidence only.

## 3. Tests

### 3.1 On your PC against trip.com

From your run records (sent in the site diagnostic ZIP):

| Run | Script | Result | Steps done by hand |
|---|---|---|---|
| RUN-20261114-151722 (23.7.1 first build) | SCR-7500BE61AF | FAILED at Choose 'Manila' | fixed since |
| RUN-20261114-164528 | SCR-ADE216A761 | PASSED: From (Dubai, all airports), To (New Delhi), end date, Adults 2, Search played by themselves | start date (fixed since) |
| RUN-20261114-164836 | SCR-ADE216A761 | PASSED, same | start date and one banner click |
| 14 Nov 18:45 | 21-step script | FAILED at the start date ("days read: 16, 18") | step 8 paused (fixed since) |

**This package on trip.com:** not run yet. Run `VERIFY_LIVE_SITE.cmd` and play your script; send back the
`SkyGuard_LiveCheck_<date>.zip`.

### 3.2 On this machine [fixture]

See `TEST_REPORT.md` for every test. The ones for this package:

| Test | What it proves |
|---|---|
| `test_two_searches_with_different_values_pass_on_the_stand_in` | The live check passes end to end twice with different values (Dubai > New Delhi, 2 adults; London > Manila, 1 adult), including the results check. |
| `test_a_failing_step_is_reported_with_evidence_and_stops_that_search` | An unknown city fails that search at From with the engine view, accessibility tree and HTML saved; later steps do not run. |
| `test_failed_step_saves_component_diagnostics` | Play saves the diagnostics of a failed step and lists them in the run's evidence. |
| `test_changed_selector_is_found_by_role_and_name`, `test_semantic_locators_built_from_the_fingerprint` | A changed selector is still found; role and name, label, title and alt text each find exactly one element on a changed page; texts with numbers are never used. |
| `test_search_counts_only_when_the_page_answers` | Search passes when the page answers; a Search that does nothing fails after the wait. |
| `test_closed_calendar_date_boxes_are_not_read_as_days` | The "days read: 16, 18" failure; fails without the fix. |
| `test_calendar_day_the_reading_misses_is_found_by_its_recorded_label` | The "but not that day" failure; fails without the fix. |
| `test_typing_repeated_later_in_the_recording_does_not_pause` | The "Several suggestions match 'dubai'" pause; fails without the fix. |
| `test_captured_pages_are_served_as_text_downloads`, `test_captured_html_has_no_event_handlers` | Diagnostics cannot run as pages in SkyGuard. |

Earlier tests in the same files cover: no unique id, focus-only roles, rows without roles and with generated classes,
several matching airports (reported as choices), two visible months, adjacent-month and greyed days, prices in day
labels, counters by label, a recorded row click and the popular list.

Not tested: duplicate ids, page refresh in the middle of a run, a browser restart between runs (each run starts a new
browser, so every run is one), slow networks beyond the stand-in's delays.

## 4. Known limitations

- Verified on trip.com only through your runs; this package is not yet verified there.
- English labels: dates in labels are read in English month names and ISO forms; other languages fall back to the
  month heading plus day number.
- No OCR or image recognition.
- Fields inside cross-origin iframes use only the recorded locators (no self-healing there).
- The live check finds From / To by English words (from, departure, to, arrival, ...) in the field's own description.
