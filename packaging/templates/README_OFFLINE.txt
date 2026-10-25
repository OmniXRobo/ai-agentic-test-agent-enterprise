AI Agentic Test Agent Enterprise - offline package
===================================================

This folder runs on 64-bit Windows without Internet and without administrator rights.
It contains its own Python, Java (Eclipse Temurin JRE), Apache JMeter, Chromium for Playwright, llama-server for
the local LLM and an offline speech model. Nothing is downloaded and no configuration commands are needed.

0. If the zip was copied from a download or a network share, Windows may mark it as "from the Internet" and warn
   before running the .cmd files. Before extracting: right-click the zip > Properties > tick "Unblock" > OK.
   SHA256SUMS.txt (delivered next to the zip) holds its checksum; sbom.cdx.json lists every component (CycloneDX).

1. Check the package on this PC (optional, recommended):
       VALIDATE_OFFLINE.cmd
   Every check runs with outbound network connections blocked. The report is saved as
   validation-report.txt next to this file.

2. Install for your Windows user:
       INSTALL_OFFLINE.cmd
   Default folder: %LOCALAPPDATA%\Programs\AIAgenticTestAgent
   Another folder:  INSTALL_OFFLINE.cmd --target "D:\Tools\AIAgenticTestAgent"
   Without shortcuts: INSTALL_OFFLINE.cmd --no-shortcuts
   If your company only allows programs in an approved folder, install there with --target.
   Optional pre-fill for First-Run Setup (the PAT is always entered on the screen, never on a command line):
       INSTALL_OFFLINE.cmd --tfs-url "https://tfs-server/tfs/DefaultCollection" --tfs-project "MyProject"
                           --llm-model "D:\Models\qwen.gguf" [--voice-model "D:\Models\vosk-model"]

   The installer checks every file against MANIFEST.json, copies the new version beside the current one,
   checks it, switches over, validates Java, JMeter, Chromium and the Python packages, and switches back
   automatically if anything fails. Your data folder (settings, database, evidence, reports) is never touched.
   Saved TFS PATs and application passwords stay in Windows Credential Manager.

3. Start the dashboard:
       START_QA_PLATFORM.cmd in the install folder, or the Start Menu / Desktop shortcut.
   The first start opens First-Run Setup. It asks for three things only:
       - TFS server URL, project and Personal Access Token (stored in Windows Credential Manager)
       - the LLM model file (.gguf); the bundled llama-server starts it now and with every start of the dashboard
       - optionally a voice model (Vosk folder or .zip); otherwise the bundled one is used

4. Go back to the previous version if needed:
       ROLLBACK_OFFLINE.cmd in the install folder (close the dashboard first).

On-Premise AI: llama-server is bundled in payload\runtime\ai\llama.cpp. The package does not download models;
choose your Qwen .gguf file in First-Run Setup (or AI Runtime > Managed Local LLM).

Voice commands: a small offline speech model is bundled in payload\runtime\speech, so the microphone works without
Internet or a speech server. A larger Vosk model can be chosen in First-Run Setup. Replies use the PC's own Windows voices.

DEPENDENCIES.md in payload lists every bundled component and Python package with its version and license;
sbom.cdx.json lists the same plus every JMeter library with its SHA-256, for vulnerability scanners.
