# SkyGuard QA 23.6.0: root causes and fixes

Each finding below was reproduced or confirmed in code before it was fixed. The live trip.com, makemytrip.com and
saucedemo.com sites could not be reached from the build environment (its proxy blocks them), so those flows were
reproduced with local test pages that behave the same way (see `TEST_REPORT.md`).

## Bug 1: AI Test Design "Generate" fails ("On-Premise AI server did not answer in time", DESIGN-20261113-073926)

**Root cause.** `DesignService.run` sent the whole user story to the local model in one request per stage. It called
`complete_json` without a timeout, so the 120 s *request* timeout applied rather than the 600 s *generation* timeout.
On a CPU-only Qwen model, a story with 11 acceptance criteria needs more than two minutes for the first stage, so the
run failed after exactly 2 min 0 s with the generic message. No dummy data was produced; the run just failed.

**Fix** (`qa_platform/design/service.py`):
- Every AI call uses the generation timeout (`ai.generation_timeout_seconds`, default 600 s).
- Every AI call sends the JSON schema to the server (`enforce_schema`), so the model cannot ramble.
- The requirements analysis is generated in parts: 3 criteria per call, or 2 when the model's context is 4096 tokens
  or less. You can set the number with `design.criteria_per_call`.
- Test cases are generated per group of requirements, with the case budget spread over the groups.
- Test data is generated for 4 test cases per call.
- Requirement ids (R1…) and test-case refs (TC1…) are renumbered so they stay unique across parts.
- A requirement id the model invents is dropped.
- Every error names the stage and the part, and says what to do. For example: "The On-Premise AI did not answer in
  time: the test case generation (part 2 of 3) did not finish within 600 s. … raise 'Generation timeout' …".
- Malformed AI output fails the run with "Stage: …"; it is never replaced with made-up content.
- Stages that finished are kept in the saved result.
- An empty story (no description, no criteria) fails at once with `STORY_EMPTY`, without an AI call.
- `user_story_id` must be a positive integer (HTTP 422 otherwise).

## Bug 2: "Open script" fails ("Invalid script id 'FWS-B211AC4E14'")

**Root cause.** On Execution History, the run page (`web/js/pages/history.js`) always linked "Open script" to the
guided recorder page (`#/guided-automation?script=…`). That page loads scripts from the recorded-script store, which
only accepts `SCR-XXXXXXXXXX` ids. AI-Driven framework scripts have `FWS-` ids and live in the Script Studio, so every
framework run produced "Invalid script id".

**Fix:**
- `FWS-` ids link to AI-Driven Automation, Script Inventory (`#/ai-automation?script=…`). The recorded-script pages also
  redirect an `FWS-` id there.
- The recorded-script API answers an `FWS-` id with `SCRIPT_ID_WRONG_PAGE`, which says where the script opens.
- When a script cannot be opened, the page shows the reason and the id, and says nothing was created or changed.
- Opening a framework script only reads it. Each problem is reported with its full path and the reason, and nothing is
  created or overwritten:
  - a missing file (`FILE MISSING`);
  - a file that is not `.py` (`FILE INVALID`);
  - a locked, permission-denied or binary file (`FILE UNREADABLE`).
- `FrameworkWorkspace.read` accepts UTF-8 with a BOM and Windows-1252 files. It also works with paths that contain
  spaces and non-ASCII characters. It refuses `..`, absolute paths and drive paths.

## Bug 3: guided playback cannot find components

### Trip.com recording (fails at step 6, "self-heal best score 0.24")

**Root causes, found in the recorded steps:**
1. **The destination suggestion click was recorded as a click on the whole search form.** The recorder took
   `closest('[role]')`, which was the big `#searchBoxCon` container.
2. **The text was recorded after the suggestion click.** So playback typed "India" and never chose a suggestion. The
   date picker therefore never opened.
3. **Calendar days are `<div role="checkbox">` cells with no input.** Each day was stored as a raw aria-label click,
   and the label includes a price ("…, The price is AED 1.65k") that changes every visit.
4. **The guest "+" clicks were lost.** They were recorded as clicks on the container.

### MakeMyTrip recording (fails at step 4, hover on `role=button "FromDXB…"`, score 0.18)

**Root cause.** The hover target's accessible name contains the value selected at recording time ("FromDXB, Dubai …").
The element is replaced by an input once it is opened. Every hover step was treated as fatal, so a hover over a
changed label stopped the whole run.

### Fix: a universal component layer (no site-specific code, no coordinates)

- **`qa_platform/automation/js/components.js`** runs in every page and frame of a run or recording.
  - It recognises: native and custom dropdowns (React Select, MUI, Ant Design, Bootstrap, Select2 and portal lists);
    autocompletes; calendars (one or two months, date-labelled cells or number-only cells); month navigation; and
    disabled or past days.
  - It finds controls by role, label, text and position relative to the field, not by id or class. So duplicate,
    generated or changing ids and classes do not matter.
- **`qa_platform/automation/components.py`** provides `pick_date`, `choose_option`, `commit_autocomplete` and
  `dismiss`.
  - They wait on DOM state (a MutationObserver "settle"), never on a fixed sleep.
  - They verify the result after each action.
  - When a choice is ambiguous or missing they raise `OPTION_AMBIGUOUS` or `OPTION_NOT_FOUND` with the visible
    choices, instead of guessing.
- **`qa_platform/automation/semantic.py`** turns old recordings into intent:
  - a calendar-cell click becomes `pick_date` with the date;
  - a click on the page background becomes "close the pop-up";
  - a hover becomes optional;
  - an option click becomes a recognised option.
  The **Semantic upgrade** button shows these changes and saves them as a new script version.
- **The recorder** now records:
  - `pick_date` steps with a relative date rule (`{{TODAY+Nd:%Y-%m-%d}}`);
  - `choose_option` steps for list choices;
  - the typed text before the suggestion pick;
  - the small clickable control instead of a surrounding container.
- **The runner:**
  - a visible run started from the editor pauses with the browser open and offers choices, Continue, Retry, Skip or
    Stop (default limit 15 minutes);
  - payment and booking clicks ("Pay now", "Book now", "Complete booking", …) are blocked unless allowed;
  - a hover that no longer matches is skipped rather than failing the run;
  - a click on a whole form container is skipped;
  - self-healing ignores prices and "N left" counters in labels.

## Bug 4: AI Operations Agent chat page and "Multiple matching controls found for 'Add to cart'"

**Root cause of the pause.** On Swag Labs there are six "Add to cart" buttons. They differ only in
`data-test="add-to-cart-<product>"`. The locator scored their ids by similarity, so all six stayed within the 0.08
margin and the result was "ambiguous". The agent then told the person to "select manually".

**Fix:**
- **Locator:** when exactly one control's `data-test`, `id` or `name` matches the learned one exactly, that control is
  used. Similar ids alone never decide.
- **True duplicates (no ids):** the agent lists them in chat by their row or card, for example
  "Add to cart (Sauce Labs Onesie)". The person picks one with a button.
  - The pick applies to that step only. It is not saved as the control's general location.
  - Resuming without a pick still works (click it yourself, then Resume).
- **Chat page:**
  - The example placeholder, the side note and the permanent microphone note are removed.
  - The status bar shows only the application, state, progress, record and Pause / Resume / Stop.
  - "Cancel remaining" and details moved to the Executions tab.
  - Pause messages are rendered as text only.
