SkyGuard Offline Edge/Chrome Agent Extension 1.4.3 (with SkyGuard QA 23.13.3)
============================================================================

The Manifest V3 extension for Microsoft Edge and Google Chrome (116 or later) and its local companion, which runs on
this PC only (127.0.0.1). Offline, per user.

NEW INSTALL (no commands, no pairing code, no sign-in)
  1. Double-click INSTALL.cmd and wait until it says "One last step in the browser".
     It installs the companion, copies the AI and speech models from SkyGuard QA on this PC when it has them, and
     prepares the extension so it connects and signs in by itself.
  2. The extensions page of Edge (or Chrome) opens. Switch on "Developer mode", press "Load unpacked" and paste
     the folder with Ctrl+V (INSTALL.cmd put it on your clipboard and opens it for you).
  That is all. Click the SkyGuard icon to open the side panel.
  Edge and Chrome only allow an extension that is not from their store to be added by hand (step 2); SkyGuard does
  not change browser policies to get around that.

ALREADY INSTALLED (1.0 to 1.4.2)
  Double-click UPDATE_EXTENSION.cmd, then press Reload on the SkyGuard card of the extensions page it opens. The
  extension then connects and signs in by itself. To undo: UPDATE_EXTENSION.cmd -Rollback

NEW IN 1.4.3
  - Spoken Arabic "open page" is translated literally; when the page is not named, the question lists the page names
    only and shows them as buttons.

NEW IN 1.4.2
  - A request made by voice gets a spoken reply: English with the voices installed in Windows, Arabic with the
    offline Arabic voice (run INSTALL_ARABIC_VOICE.cmd from the SkyGuard QA folder once; it gives the companion
    the same voice). Options > Spoken replies: on or off, Male or Female.

NEW IN 1.4.1
  - Arabic and UAE Arabic commands are translated to English on this PC; the side panel shows "Understood as".
  - Public websites need no account, addresses that change on every visit stay the same website, and one browser
    window is kept (the AI Agent fixes of SkyGuard QA 23.13.0).

NEW IN 1.4.0
  - Installs without typing anything: the installer leaves a one-time ticket in the extension folder of your own
    profile; the extension pairs with it once and signs in as this PC's owner. Another browser profile, another
    extension or a web page still cannot reach the companion. Pairing with a code and signing in with a password
    stay available for more users (Options).
  - Models are copied by the installer (no separate command).
  - The AI Agent fixes of SkyGuard QA 23.12.0 (Finish lab on Swag Labs completes, recorded tasks found by name).

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
