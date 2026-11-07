SkyGuard QA - offline installation (all-in-one package)
=======================================================

Everything SkyGuard QA needs is inside this folder. Installing it needs no Internet, no administrator rights, no
patches and no other software. Only 64-bit Windows 10 or 11 and about 6 GB of free disk space.

What is inside
--------------
  payload\runtime\python          Python 3.13 with every package it needs (pip removed)
                                  + the Microsoft Visual C++ runtime files (no Visual C++ install needed)
  payload\runtime\ms-playwright   Chromium, for recording, test runs, discovery, PDFs and the app window
  payload\runtime\jre             Java 17 (Eclipse Temurin), for JMeter
  payload\runtime\apache-jmeter-* Apache JMeter 5.6.3, for performance tests
  payload\runtime\ai              llama-server and the AI model qwen2.5-1.5b-instruct-q4_k_m.gguf (On-Premise AI)
  payload\runtime\faster-whisper  Faster-Whisper speech recognition and its model small.en
  payload\runtime\speech          Vosk small English, the backup speech recognizer
  sbom.cdx.json                   every component with its version and checksum (CycloneDX), for the security team
  SECURITY.txt                    the security review of this version
  payload\DEPENDENCIES.md         every component and Python package with its version and license

What is left out, and why
-------------------------
  Visual Studio, VS Code    not used to run SkyGuard QA; only the C++ runtime files are needed, and they are included
  Node.js                   Playwright brings its own small copy inside its Python package
  Git                       not used
  Firefox, WebKit (Safari)  not included; tests run in Chromium, or Microsoft Edge which every Windows PC has
  Download tools            the models are already inside; nothing is downloaded
  A separate Python, pip    the included Python is used; pip is removed so nothing can be installed later

Install
-------
1. Check the zip: compare its SHA-256 with SHA256SUMS.txt (PowerShell: Get-FileHash .\SGQA-<version>.zip).
2. Right-click the zip > Properties > tick "Unblock" > OK. Then right-click > Extract All.
   Keep the extracted folder short, for example Downloads or C:\SGQA. Do not extract inside deep folders.
3. Optional, recommended: double-click VALIDATE.cmd. Every check runs with the network blocked.
4. Double-click INSTALL.cmd. Wait for "Installed".
   Default folder: %LOCALAPPDATA%\Programs\AIAgenticTestAgent (your data stays in its "data" folder).
   Another folder (for example one approved by IT):  INSTALL.cmd --target "D:\Tools\SkyGuardQA"
   IT can pre-fill the TFS server:  INSTALL.cmd --tfs-url "https://tfs-server/tfs/DefaultCollection" --tfs-project "MyProject"

Start
-----
Double-click SkyGuard QA (robot icon) on the Desktop or in the Start Menu.
* No command window opens. The dashboard opens in its own window.
* First start: First-Run Setup asks only for the TFS server URL, project and Personal Access Token. The AI model and
  the speech model are already set.
* Closing the window stops SkyGuard QA and everything it started: test runs, browsers, JMeter, the local AI and the
  speech server. When a schedule is active, SkyGuard QA keeps running in the background so the schedule runs;
  double-click the icon to open the window again, and press Exit (power button, top right) to stop everything.
* START_QA_PLATFORM.cmd in the install folder still starts it with a visible command window, for troubleshooting.

Settings for a PC without Internet
----------------------------------
  AI:                 On-Premise AI (already set). Do not choose Cloud AI.
  Speech recognition: Faster-Whisper on this PC (already set). Do not choose "Browser speech recognition".
  Outlook Email:      "Company Exchange / other SMTP server" with your company's mail server, or "Outlook app on this
                      PC" connected to your company's Exchange. Microsoft 365 and Outlook.com need Internet.
  TFS:                your company's TFS / Azure DevOps Server. dev.azure.com needs Internet.

Check that it works
-------------------
  1. VALIDATE_OFFLINE.cmd in the install folder: every line PASS (logs\validation-latest.txt).
  2. Voice & Command Agent > Check Voice Setup: Faster-Whisper speech recognition PASS.
  3. AI Runtime: Local AI RUNNING; press Test AI.
  4. Performance Testing: run a short scenario against a test application on your network.

Troubleshooting
---------------
  "Windows protected your PC" or blocked .cmd: the zip was not unblocked (step 2), or AppLocker allows programs only
      in approved folders: install there with --target.
  "Port 8765 is used by another program": close that program, or change server > port in data\settings.json.
  The AI answers slowly: normal on an older CPU for the first request; the model stays loaded afterwards.
  Something else: run VALIDATE_OFFLINE.cmd in the install folder and send logs\validation-latest.txt and data\logs\start.log.

Uninstall: close SkyGuard QA, delete the install folder and the two SkyGuard QA shortcuts. Nothing else is changed
on the PC (no services, no registry entries).

Later updates come as small patch zips; this full package is only needed for the first installation.
