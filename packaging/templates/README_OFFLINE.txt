AI Agentic Test Agent Enterprise - offline package
===================================================

This folder runs on 64-bit Windows without Internet and without administrator rights.
It contains its own Python, Java (Eclipse Temurin JRE), Apache JMeter and Chromium for Playwright.

1. Check the package on this PC (optional, recommended):
       VALIDATE_OFFLINE.cmd
   Every check runs with outbound network connections blocked. The report is saved as
   validation-report.txt next to this file.

2. Install for your Windows user:
       INSTALL_OFFLINE.cmd
   Default folder: %LOCALAPPDATA%\Programs\AIAgenticTestAgent
   Another folder:  INSTALL_OFFLINE.cmd --target "D:\Tools\AIAgenticTestAgent"
   Without shortcuts: INSTALL_OFFLINE.cmd --no-shortcuts

   The installer checks every file against MANIFEST.json, copies the new version beside the current one,
   checks it, switches over, validates Java, JMeter, Chromium and the Python packages, and switches back
   automatically if anything fails. Your data folder (settings, database, evidence, reports) is never touched.
   Saved TFS PATs and application passwords stay in Windows Credential Manager.

3. Start the dashboard:
       START_QA_PLATFORM.cmd in the install folder, or the Start Menu / Desktop shortcut.

4. Go back to the previous version if needed:
       ROLLBACK_OFFLINE.cmd in the install folder (close the dashboard first).

On-Premise AI: the package does not download models. If the package was built with a model it is in
payload\runtime\ai; otherwise point the On-Premise AI settings at your own llama.cpp (Qwen GGUF) server.

DEPENDENCIES.md in payload lists every bundled component and Python package with its version and license.
