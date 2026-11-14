# Changelog: SkyGuard QA 23.6.1 (cumulative patch: 23.4.0 + 23.5.0 + 23.6.0 + 23.6.1)

This one package installs on **23.3.35, 23.4.0, 23.5.0 or 23.6.0** and brings the installation to 23.6.1. It contains
every application file changed since 23.3.35, so you do not need to apply the earlier patches first. The 23.6.0 notes
are included under `previous-23.6.0/`.

## 23.6.1

### Fixed (Guided Browser Automation)
- **Suggestion lists (Trip.com city list, step 9 "New Delhi - All airports India"):**
  - the typed text is recorded before the suggestion click (it was recorded after it, when the field lost focus);
  - suggestion rows with no role and only generated class names are recognised and recorded as "Choose ... (typed ...)";
  - playback types the text again key by key when the list is not open, and chooses the row by its text, never by
    its position (`[data-testid="0"]`);
  - a list replaced while loading (popular cities, then results) is read again before the click;
  - recordings made with 23.6.0 play: the suggestion click before the typing is played as "type, then choose".
- **Calendars:**
  - Trip.com's hotel calendar: a click on any day was read as the 1st of the month (the week row was taken for a day);
  - greyed past days (`is-disable` on the cell around the day number) are refused as not available;
  - a recorded day cell whose label holds a price is no longer used as the date field (no more 10 s waits).
- **Passenger / room / guest counters:** + and - presses are recorded as "Set Children to 1" and played by the
  counter's label until it shows the number; the application's limits are reported, not ignored.
- **Custom dropdowns opened by a click** (child age): recorded and played as a choice.

### Added
- `components.set_count()` and the counter recogniser in `js/components.js` (`counter`, `counters`, `stepperOf`).
- Generic list reading next to a field (`nearRows`, `rowOf`, `rowText`, `pointerRoot`) used by `findOption`.
- `semantic.suggestion_context()` and a Semantic upgrade rule for suggestion clicks.
- The exported pytest file types the recorded text before choosing a suggestion.
- Tests: `tests/test_tripcom_playback_23610.py` and the Trip.com stand-in `tests/site/tripcom/`.

### Packaging
- Version 23.6.1; installs on 23.3.35, 23.4.0, 23.5.0 or 23.6.0; same offline, no-administrator installer with backup,
  automatic rollback on failure, verify and rollback. The patch file list is now `PATCH_FILES_23.6.1.json`; a rollback
  to 23.6.0 leaves 23.6.0's own list in place.
- No database migration (still 18), no new Python packages, no change to the local AI model or Playwright.

## 23.6.0 and earlier

See `previous-23.6.0/PATCH_CHANGELOG.md`.
