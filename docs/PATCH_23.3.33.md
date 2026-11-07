# 23.3.33: Microsoft Edge for the dashboard and test runs; install over a running older version

Full package `SGQA-23.3.33.zip`, the same as 23.3.32 with these changes.

**Microsoft Edge**

* The SkyGuard QA icon opens the dashboard window in Microsoft Edge (then Google Chrome, then the included
  Chromium). The included Chromium is Chrome for Testing, which shows an "only for automated testing" bar.
  Closing the Edge window still stops SkyGuard QA and everything it started; the window uses its own Edge profile
  with background mode and extensions off, so Edge does not keep running after it is closed.
* The installer sets test runs and recordings to Microsoft Edge when it is installed and no browser was chosen yet
  (Settings > Automation runs). The Browser field on Record, Performance recording and new schedules starts on that
  choice. Chromium and Google Chrome can still be chosen.

**Installer**


* Installing over an older version could stop with "The current version could not be moved aside ([WinError 5]
  Access is denied)" when that version had left a program running (for example its local AI server after the
  dashboard was closed). The installer now finds every program running from the folder of the version it replaces,
  stops it, and retries the switch for about 30 seconds while Windows releases the files. If a window the installer
  cannot close is still open in that folder (Explorer, a command window, an editor), it says so; nothing is changed.
