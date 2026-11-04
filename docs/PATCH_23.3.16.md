# Patch 23.3.16: Delete script on the automation pages

Cumulative: includes 23.3.15 and everything before it. No database change. Your AI server, model, settings and
data are kept.

## What changed
A **Delete** action on every script row of:

* **AI-Driven Automation > Script Inventory** (generated Python scripts and recorded scripts listed there);
* **Guided Browser Automation > Scripts**;
* **Hybrid Test Automation > Scripts** (the "Record more steps" script picker is refreshed too).

Each script's own page has a **Delete** (generated script) or **Delete this script** (recorded script) button
instead of Archive.

Every delete asks first: **"Delete script?"** with the script name and Test Case. Only **Delete** deletes;
Cancel, the close button, Escape or a click outside keep the script.

## What a delete does
* The script leaves the list, **Download framework (.zip)**, **Download Python tests (.zip)**, **Play Selected** and
  **Run all**.
* Generated script (FWS-...): its test file (`tests/test_tc....py`) and its test data file move to
  `automation-workspace\.deleted\<script id>-<time>\`. Page Objects and flows stay, because other scripts use them.
  A test data file another script still uses stays. Versions and run evidence are kept.
  Before 23.3.16, Archive left the test file in `tests\`, so it was still in the zip and in a full test run.
* Recorded script (SCR-..., Guided / Hybrid / Discovery): moves to `scripts\_archived\<script id>-<time>\` as before.
* A script that is running is not deleted ("... is running. Stop it or wait until it ends, then delete it.").
* The Test Case can be generated or recorded again afterwards.

To bring a deleted script back, copy its files from the `.deleted` / `_archived` folder back.

## Files
* `web/js/confirm-delete.js` (new): the "Delete script?" confirmation.
* `web/js/pages/script-studio.js`: Delete on the Script Inventory and the script page.
* `web/js/pages/automation.js`: Delete on the Guided, Hybrid (and Discovery) script lists and the script editor.
* `web/css/app.css`: red Delete link and button.
* `qa_platform/automation/framework/studio.py`, `workspace.py`: delete moves the files out of `tests/` and
  `test_data/`, refuses a running script; `.deleted` is left out of the zip and the test run.
* `qa_platform/api/studio.py`, `qa_platform/api/automation.py`: the delete answer says what was moved.
