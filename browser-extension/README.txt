SkyGuard Adaptive AI Browser Extension (Chrome and Edge, Manifest V3) with its local companion
=============================================================================================

The extension learns your web applications with your approval and operates them through their normal screens in
your own browser: you ask in the side panel, it shows a preview, you confirm, it fills the application's forms and
reports what the application confirmed for every record. A small local companion on this PC (127.0.0.1 only) keeps
the knowledge database, the encrypted credential vault, the audit trail and the local AI (Qwen) and speech
(Faster-Whisper). Nothing goes to the cloud. SkyGuard QA is NOT required.

Quick start (Windows 11, no administrator rights)
  1. Extract this zip to a folder, for example Downloads\SkyGuard_Adaptive_AI_Browser_Extension_Final.
  2. Double-click INSTALL.cmd. It installs the companion for your Windows user only
     (%LOCALAPPDATA%\SkyGuardAgent), starts it and shows an 8-digit pairing code.
  3. Optional, for AI and voice: run Install-Models.ps1 (copies the models from a SkyGuard installation or a folder
     you give, checks their SHA-256). Without models the agent still works with its built-in request parser.
  4. Chrome: chrome://extensions   Edge: edge://extensions
     Turn on Developer mode, press "Load unpacked", choose %LOCALAPPDATA%\SkyGuardAgent\extension
  5. In the extension's Options: Save (port 8790), type the pairing code, create the first companion user (admin),
     and sign in. Then open the side panel from the toolbar icon.

Documents (docs\)
  INSTALL_WINDOWS11.md      whole installation, step by step
  INSTALL_CHROME.md         Chrome: Load unpacked, pinning, side panel, site access
  INSTALL_EDGE.md           Edge: the same for Microsoft Edge
  COMPANION_SETUP.md        the companion: pairing, users, settings, AI and speech, commands
  ENTERPRISE_DEPLOYMENT.md  Chrome Web Store, Edge Add-ons, IT policy (force install), allow lists
  OFFLINE_DEPENDENCIES.md   what is bundled, versions, SHA-256, licences, what is not bundled
  SECURITY.md               how secrets, accounts, the companion and web content are protected
  TROUBLESHOOTING.md        messages and what to do
  UNINSTALL.md              removing the extension and the companion, keeping or deleting data
  USER_GUIDE.md             using the side panel: learn, approve, ask, preview, confirm, verify
  TEST_REPORT.md            what was tested and how, and what could not be tested
  CHANGELOG.md              version history

Package contents
  INSTALL.cmd, UNINSTALL.cmd   per-user installer and uninstaller (call scripts\*.ps1)
  extension\                   the unpacked MV3 extension (also copied to %LOCALAPPDATA%\SkyGuardAgent\extension)
  companion\                   the companion (Python, standard library) with the shared agent core
  offline\                     Python 3.13.17, llama-server b10549 (CPU), Faster-Whisper wheels, OFFLINE_MANIFEST.json
  scripts\                     Install, Uninstall, Install-Models, Check-Prerequisites, SkyGuardAgent.cmd
  tests\                       automated tests (run from a source checkout; see docs\TEST_REPORT.md)
  SHA256_CHECKSUM.txt          SHA-256 of every file; the installer checks it before installing
