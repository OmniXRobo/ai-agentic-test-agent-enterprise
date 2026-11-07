# Patch 23.3.35: tests play in the browser chosen in Preferences

## What was wrong

Settings > Preferences already had a Browser choice, and since 23.3.33 the installer sets it to Microsoft Edge when
Edge is installed. But Play and Run selected used the browser stored in each script. Every script recorded before
23.3.33 stored Chromium (the browser included with SkyGuard QA, which looks like Google Chrome), so those tests kept
opening in Chromium whatever Preferences said. The setting only reached generated Python Playwright scripts and
new recordings.

## What changed

* Play and Run selected use the browser from **Settings > Preferences > Browser for tests and automation runs**:
  Chromium (included), Microsoft Edge or Google Chrome. Changing it there changes where every test opens, including
  tests recorded earlier.
* A run can still ask for its own browser: a schedule's browser selection, or a voice command such as "run it in
  Chrome".
* With no browser chosen in Preferences, a script plays in the browser it was recorded in, as before.
* The script editor no longer shows its own Browser field. It points to Preferences instead.
* Files: `qa_platform/automation/runner.py`, `web/js/pages/automation.js`, `web/js/pages/settings.js`,
  `qa_platform/offline/install.py` (message text). Test: `tests/test_play_browser_23335.py`.

Edge and Chrome must be installed on the PC that runs the tests. If the chosen browser is missing, the run fails with
"Could not start msedge", and you can pick another browser in Preferences.
