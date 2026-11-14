# SkyGuard QA 23.6.1: root causes and fixes (Guided Browser Automation on Trip.com)

**Reported:** a Trip.com recording played in Guided Browser Automation stops at step 9, *Element not found: New Delhi -
All airports India (selector `[data-testid="0"]`)*, and self-healing finds no confident match (best candidate
`[data-testid="search_city_to0_wrapper"]`, score 0.39 under 0.62). The date picker cells and the passenger + / -
buttons (adding a child) did not play back either.

## How it was reproduced

trip.com is still blocked by the build machine's network proxy (HTTP 403). This time the test page was not invented:
`tests/site/tripcom/` is a stand-in built from **Trip.com's real markup**, taken from public sources:

- a crawl of the trip.com home page (April 2026) published as a test fixture in a public GitHub repository: the search
  box tabs, `#searchBoxCon`, `#destinationInput`, the `c-calendar` hotel calendar (`ul > li[role=button] > div.tipWrapper
  > span.day`, a `"Apr 2026"` heading, icon-only `span.c-calendar-icon-prev.is-disable` / `span.c-calendar-icon-next`),
  "Rooms and guests" with generated CSS-module class names;
- public Trip.com scrapers that drive the real site: `input[data-testid="search_city_from0"]` inside
  `div.fuzzy-input[data-testid="search_city_from0_wrapper"]`, the suggestion box
  `div.m-flight-poi-search-B.nh_poi-container[data-testid="search_result_box"]` with `li[role=listitem]`,
  `div.poi-result__title.title--new.title--able` rows and `li.poi-result__airport[data-value]` airport rows,
  `input[data-testid="search_date_depart0"]`, `div.nh_sp-btn2[data-testid="search_btn"]`;
- your recording (from the 23.6.0 report): flight calendar days are `div[role=checkbox]` named
  "Sunday, November 15, 2026, Select the start date you want, The price is AED 1.65k".

The stand-in also copies the behaviour that matters: suggestions are fetched from the typed text after a short delay,
the popular-cities panel shown on focus uses the same index-numbered `data-testid` values ("0", "1", ...), the list
closes when the field loses focus, the field's value is written by the chosen suggestion, and the + / - controls are
icons with no text and no label.

Recording the same flow with 23.6.0 on the stand-in gave **the same failure as your screenshot**: the suggestion click
was recorded *before* the typing (`click #m-flight-poi-list > li`, then `fill New Delhi`), and playback stopped at the
click with self-healing below the threshold.

## Defect 1: the city suggestion is not found

**Root causes**

1. **The typing was recorded after the suggestion click.** The recorder took the typed text from the field's `change`
   event. Trip.com's field fires `change` only when it loses focus, which happens *after* the suggestion is clicked. The
   recording therefore said "click New Delhi", then "type New Delhi". At playback the list is not open at step 9 (nothing
   has been typed yet), so the element cannot exist.
2. **The suggestion list was not recognised as a list.** 23.6.0 recognised options by role (`option`, `menuitem`) or by
   class words such as "option", "suggest", "dropdown". Trip.com's rows have no role and only generated class names
   (`poi-result__title`, CSS-module hashes), so the click stayed a plain click on `[data-testid="0"]`. That test id is
   only the row's position in whatever list is open (the popular-cities panel has a `"0"` too), so it never identifies
   the city.
3. **Self-healing had nothing to find.** With no list open, the closest text on the page was the field's own wrapper
   (score 0.39), which correctly stayed under the 0.62 threshold.

**Fix**

- **Recorder** (`js/recorder.js`):
  - The text being typed is recorded the moment the person presses anything else (mouse down), so the typing always
    comes before the suggestion it produced. The later `change` event is not recorded again, and the value the chosen
    suggestion writes into the field is not recorded as typing.
  - A click on a row that **appeared after typing into a field** (or after focusing it, for the popular-cities panel),
    drawn next to that field, is recorded as **Choose "New Delhi - All airports India" in the arrival field (typed
    "New Delhi")**, with the row's own locator kept only as a last resort. This needs no role and no class name.
  - A click on a row of a list that **appeared after another click** (custom dropdowns such as the child-age list) is
    recorded as **Choose "5 years old"** in the control that opened it.
  - The row text joins the text pieces with spaces ("New Delhi - All airports" + "India"); `innerText` ran them together
    ("airportsIndia").
- **Component engine** (`js/components.js`, `components.py`):
  - Lists without role or known class are read from **what is drawn next to the field** (points sampled below and above
    it), only on a layer above the page (an absolute or fixed pop-up), so page content under the search box is never
    taken for a suggestion.
  - The clicked row is where the pointer cursor starts, and one row of a repeated list, not its inner span and not the
    whole list.
  - Matching: exact text, first line, unique prefix, whole words, then same words in another order or punctuation.
  - When no open list has the option, the field is cleared and the text is **typed again key by key** (what was typed
    when recording, then the start of the option, "New Delhi"), and the list is awaited. Lists that are replaced while
    loading (popular cities, then results) are read again before the click, and once more if the row vanishes.
  - The choice is verified: the field shows the option, or its new value is made of the option's words (Trip.com
    writes "New Delhi").
- **Recordings made with 23.6.0** (`semantic.suggestion_context`, `runner.py`): a click whose text starts with what a
  neighbouring step typed into a text field (only hovers in between) is played as "type, then choose this suggestion",
  and the typing step after it is marked as already done. **Semantic upgrade** writes this into the script as a new
  version (the typing moves in front of the choice).

## Defect 2: calendar days

**Root causes**

1. **Hotel calendar (`c-calendar`)**: the month's day grid and each week row start with the text "1", so they were
   taken for the day cell of the 1st. A click on any day was recorded as the 1st of the month ("TODAY+17d" for both
   check-in and check-out in the reproduction).
2. **Greyed past days** are marked `is-disable` on the `li` two levels above the clicked `span.day`. 23.6.0 looked for
   "disabled" on the element and its parent only.
3. **The recorded cell** of a flight calendar (its label holds a price that changes daily) was looked for for 4 s, then
   taken as the "date field" when found, which made verification wait on a cell that the calendar had already redrawn.

**Fix**: a day cell holds exactly one day (a container with several day numbers is not a cell); disabled state is read
up to three levels above the clicked text, including `is-disable`; the li around `span.day` gives its date; a recorded
cell is looked for briefly and is never used as the date field (the calendar handler finds the day by its full date).

## Defect 3: passenger / guest + and - buttons

**Root causes**

1. The + and - are icons (`<button><i class="fi fi-ic_plus"></i></button>` or `<span><i class="u-icon ..."></i></span>`)
   with no text and no label, and the six of them look alike. They were recorded as positional selectors
   (`#portal > div > div:nth-of-type(2) > div:nth-of-type(2) > button:nth-of-type(2)`) or by class, so a small layout
   change, or a counter that does not start at the same number, pressed the wrong button or the right one the wrong
   number of times.

**Fix**

- **Counters are recognised** wherever a number sits between two small controls: the - before it, the + after it. Each
  counter is named by the text of its row ("Children", with "2-11 years").
- **Recording:** a press of + or - is recorded as **Set Children to 1** (read after the press). Two presses on the same
  counter become one step, "Set Children to 2".
- **Playback:** the counter is found by its label, never by position; + or - is pressed until it shows the number,
  checking after every press. It stops with a clear message when the application's limit is reached (the + is
  disabled), when pressing does not change the number, or when no counter with that label is open (the message lists
  the counters that are there).
- **Child age:** the age list that appears after adding a child is recorded and played as a choice (see defect 1).

## Not changed

- No site-specific code: nothing in the engine refers to Trip.com; the rules read structure, position, text and state.
- No coordinates are recorded or replayed; every click is Playwright's trusted click on the element found.
- Offline, no new packages, no administrator rights; the local Qwen model and the existing Playwright are reused.
