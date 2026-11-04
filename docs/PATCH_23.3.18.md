# Patch 23.3.18: date and time pickers become rules, Data button for Excel / CSV columns

Cumulative: includes 23.3.17 and everything before it. No database change. Scripts, settings and data are kept.

## 1. Recording a date or time picker (OrangeHRM Leave List and similar)

Before: clicking the calendar icon and then day 2 was saved as two clicks ("Click div:nth-of-type(1) > ...",
"Click 2"). Played on another day they clicked day 2 of whatever month was showing, and the locators were fragile.

Now the recorder sees the picker: the click that opens it, any month / year / arrow clicks and the day click become
**one step on the field itself**, named by its label:

* a date -> `pick_date` with a rule measured from the recording day, e.g. From Date 2 Nov recorded on 3 Nov =
  `{{TODAY-1d:%Y-%m-%d}}` (Current system date - 1 day), To Date 30 Nov = `{{TODAY+27d:%Y-%m-%d}}`;
* a time (hour / minute picker) -> `fill` with Current system time +/- hours and minutes in the field's own format,
  e.g. `{{NOW+2h:%I:%M %p}}`;
* the field's format comes from its placeholder, so OrangeHRM's `yyyy-dd-mm` reads 2026-02-11 as 2 Nov, not 11 Feb;
* fields whose `<label>` has no "for" (OrangeHRM) get a locator from the label, e.g.
  `xpath=//label[normalize-space()="From Date"]/ancestor::*[.//input][1]//input`, instead of a chain of divs.

Change any of them with **Rule** in Edit Script.

## 2. Playing a date back

`pick_date` opens the calendar, then:
* uses the month and year drop-downs of the calendar heading (OrangeHRM, MUI, react-datepicker) for jumps of more
  than two months, otherwise the arrow buttons (also icon-only arrows such as `bi-chevron-left`);
* clicks the day;
* when no calendar can be read, types the date in the field's own format (yyyy-dd-mm, dd/mm/yyyy, mm-dd-yyyy, ...),
  where before it always typed yyyy-mm-dd;
* a step that points at the calendar icon (older recordings) opens the calendar with the icon and uses the field
  next to it;
* waits up to about 2 seconds for the calendar to open, and clicks the field again once if the calendar closed
  straight away (the previous picker was still closing). After the day click it waits for the calendar to close.

A recorded time is rounded to the whole hour when it is within 2 minutes of one: a time picker opened at 10:00 and
saved at 10:01 with 12:00 gives `{{NOW+2h}}`, not `{{NOW+1h+59min}}`.

## 3. Older scripts: "Replace with one date step"

Edit Script shows under a pair like steps 6-7 ("Click ..." then "Click 2"): *Calendar clicks (steps 6-7, day 2) ...
Replace with one date step*. It opens the Rule dialog (Today, Yesterday, + N days ...); the two clicks become one
`pick_date` step. The month and year were never recorded, so choose the date rule yourself. Save to keep it.

## 4. Data button

Next to **Rule**, a **Data** button lists the columns of the imported Excel / CSV with their row 1 value; pick one and
the value becomes `{{DATA:column}}`. Under the value: "Excel column id - row 1: Admin". The Test data card's warnings
("No step uses a column yet", "No column for ...") now follow the edits as you make them, not only after Save.

## Files
* `qa_platform/automation/js/recorder.js`, `js/fingerprint.js`, `js/calendar.js`, `recorder.py`, `visual.py`,
  `date_rules.py`, `qa_platform/api/automation.py`
* `web/js/pages/automation.js`, `web/css/app.css`
* `tests/test_datepicker_23318.py`, `tests/site/oxd_leave.html` (new)
