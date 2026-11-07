# Patch 23.3.35: tests play in the browser chosen in Preferences, a browser tab with an address bar, a steady top bar

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

## SkyGuard QA opens as a browser tab

The desktop icon opened the dashboard in an app window with no address bar or tabs. It now opens as a normal
Microsoft Edge window with an address bar and tabs (Google Chrome, then the included Chromium, when Edge is missing).
It still uses its own Edge profile, so it doesn't mix with your own Edge, and closing that window still stops
SkyGuard QA (or keeps it running for a schedule or Network access, as before).

Settings > Preferences > **Open SkyGuard QA as** switches back to the app window. The change applies the next time
SkyGuard QA opens. Files: `qa_platform/launcher.py` (`open_as`, `window_command`), `qa_platform/config.py`
(`ui.open_as`, default `browser`), `qa_platform/api/core.py` (validation), `web/js/pages/settings.js`. Test:
`tests/test_launcher_23332.py`.

## The top bar looks the same on every page

The status chips (TFS, Local AI, Playwright, Speech, Mic) shared one row with the page name, so a short name such as
"Dashboard" left room for three chips per row and a long one such as "AI Voice Command Center" pushed them into three
rows. The top bar now always has two rows. Row 1 holds the page name, the clock and the buttons; a very long name ends
with "…" and the full name shows on hover. Row 2 holds the chips, starting at the left. Files: `web/css/hud.css`,
`web/js/app.js`.
