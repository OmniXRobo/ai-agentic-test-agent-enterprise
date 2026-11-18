SkyGuard Offline Edge/Chrome Agent Extension 1.3.1 (with SkyGuard QA 23.12.0)
============================================================================

The Manifest V3 extension for Microsoft Edge and Google Chrome (116 or later) and its local companion, which runs on
this PC only (127.0.0.1). Offline, per user.

ALREADY INSTALLED (1.0, 1.1, 1.2 or 1.3.0)
  Double-click UPDATE_EXTENSION.cmd. It checks the files (SHA-256), backs up, updates the companion and the
  extension, and starts the companion. Then open edge://extensions (or chrome://extensions) and press Reload on the
  SkyGuard card. To undo: UPDATE_EXTENSION.cmd -Rollback

NEW INSTALL
  Double-click INSTALL.cmd and follow docs\INSTALL_EDGE.md or docs\INSTALL_CHROME.md (Load unpacked, pairing).
  Then copy the AI model files with scripts\Install-Models.ps1 (from your SkyGuard installation or offline media).

UNCHANGED IN SKYGUARD QA 23.12.0
  The extension and companion stay 1.3.1. API Testing does not use the extension.

NEW IN 1.3.1
  - Chat: when the agent asks for missing information, answer in your own words ("First Name; muhammad",
    "last name Ali", or just the value when one field is asked). What you answered is kept and confirmed; only
    what is still missing is asked again. A postal or zip code is no longer stopped as if it were a secret code.

NEW IN 1.3.0
  - Learn tab: Start learning / Stop & save learning for the chosen application. A red REC badge and a page banner
    show that learning is on. Stop & save tests that every learned task and page can be found again, and says so.
  - Reconnect: while the companion cannot be reached, your actions and pages are kept in order (up to 500, in
    memory) and sent when it is back. The badge turns grey meanwhile.
  - The same action reported twice is kept once.
  - Chat: suggestions from what was learned; "navigate to ..." opens the page; a short preview with
    Confirm & Execute | Edit | Cancel, or Provide missing data when something is needed.
  - Permissions are unchanged. Site access is still asked per site, when you press Start learning.
  See docs\CHANGELOG.md, docs\COMPATIBILITY.md, docs\TEST_REPORT.md and docs\USER_GUIDE.md.

SHA256_CHECKSUM.txt lists the SHA-256 of every file in this folder.
