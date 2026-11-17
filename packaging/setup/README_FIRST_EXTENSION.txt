SkyGuard Offline Edge/Chrome Agent Extension 1.2.0 (with SkyGuard QA 23.11.0)
============================================================================

The Manifest V3 extension for Microsoft Edge and Google Chrome (116 or later) and its local companion, which runs on
this PC only (127.0.0.1). Offline, per user, no administrator rights.

ALREADY INSTALLED (1.0 or 1.1)
  Double-click UPDATE_EXTENSION.cmd. It checks the files (SHA-256), backs up, updates the companion and the
  extension, and starts the companion. Then open edge://extensions (or chrome://extensions) and press Reload on the
  SkyGuard card. To undo: UPDATE_EXTENSION.cmd -Rollback

NEW INSTALL
  Double-click INSTALL.cmd and follow docs\INSTALL_EDGE.md or docs\INSTALL_CHROME.md (Load unpacked, pairing).
  Then copy the AI model files with scripts\Install-Models.ps1 (from your SkyGuard installation or offline media).

NEW IN 1.2.0
  - Chat uses the simple agent: "Show pending trips", "Approve the selected trips", one preview, a confirmation box
    for operations that change records.
  - Learn tab: Connect an application by its address; Record for Integration Comparison (saves a file that SkyGuard
    QA 23.10.0 and later import: Integration Comparison > Connect > Import from browser extension).
  - Installer fix (same 1.2.0 extension): INSTALL.cmd no longer stops at "Unpacking the bundled Python" with
    "Could not find a part of the path" on a first install (the runtime folder is now created before Python is
    moved into it, and Python is staged inside that folder instead of %TEMP%).
  See docs\CHANGELOG.md and docs\USER_GUIDE.md.

SHA256_CHECKSUM.txt lists the SHA-256 of every file in this folder.
