# 23.3.32: SkyGuard QA, all-in-one offline package, security review, acceptance criteria detail

This version is delivered as a full package (`SGQA-23.3.32.zip`) for the first installation on a PC without
Internet. See `docs/OFFLINE_INSTALL.md` and, inside the package, `README.txt` and `SECURITY.txt`.

## What is new

**Test cases read the whole acceptance criterion**

* TFS Test Design and AI Test Design now give the AI each linked Acceptance Criteria work item's title, Description
  and Acceptance Criteria field (as plain text), not only its title. The Discussion is not sent.

**SkyGuard QA desktop application**

* Name SkyGuard QA with a robot icon on the Desktop and in the Start Menu. It starts with no command window and opens
  the dashboard in its own window.
* Closing the window stops SkyGuard QA and everything it started: test runs, browsers, JMeter, the local AI and the
  speech server (a Windows job object makes sure nothing is left behind). When a schedule is active it keeps running in
  the background; double-click the icon to open the window again, and the Exit button (top right) stops everything.

**All-in-one offline package**

* One zip with Python 3.13 and every package, Chromium, Java 17, JMeter 5.6.3, llama-server with the AI model
  `qwen2.5-1.5b-instruct-q4_k_m.gguf`, Faster-Whisper with its `small.en` model, Vosk, and the Microsoft C++ runtime
  files. Nothing is downloaded at install or run time; no patches are needed.
* Short names (`SGQA`, `INSTALL.cmd`, `VALIDATE.cmd`); the build refuses a path too long for Extract All.
* The installer sets the bundled AI model and Faster-Whisper, so First-Run Setup only asks for TFS.
* Firefox and WebKit are not included; their options are hidden (Chromium and Microsoft Edge remain).

**Security**

* The local API refuses requests naming another host (DNS rebinding) and changes sent from other web pages.
* AI-generated scripts that start programs, open network connections, run built code or delete files are refused.
* Red warning on the TFS settings page while TLS certificate checking is off.
* Faster-Whisper's packages are checked with pip-audit on every build, like the application's.
