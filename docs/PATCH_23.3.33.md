# 23.3.33: install over a running older version

Full package `SGQA-23.3.33.zip`, the same as 23.3.32 with one installer fix.

* Installing over an older version could stop with "The current version could not be moved aside ([WinError 5]
  Access is denied)" when that version had left a program running (for example its local AI server after the
  dashboard was closed). The installer now finds every program running from the folder of the version it replaces,
  stops it, and retries the switch for about 30 seconds while Windows releases the files. If a window the installer
  cannot close is still open in that folder (Explorer, a command window, an editor), it says so; nothing is changed.
