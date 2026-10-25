# Capability inventory (23.0.0)

This lists what the application does and where. Entries marked **new** or **changed** were added or changed in the
Command Center upgrade. Everything else was kept as it was in the `baseline-v22.7.4` tag.

## Mission Control

* **New: AI Voice Command Center** (`#/command-center`). Talk to or type to the agent, see the transcript, the
  command handoff, the Agent Squadron, the status lights and the radio settings.
* **Changed: Dashboard.** It now has an Agent Squadron with live states, and the KPIs refresh only when a run
  finishes.
* **Changed: Agent Workflow.** All agents are shown with live states (READY, WAITING, RUNNING, COMPLETED, FAILED,
  PAUSED).
* Notifications and Execution History are unchanged.

## Test design (TFS)

* TFS Explorer, User Stories, Acceptance Criteria, Test Plans, Test Suites, Test Cases and Test Data are kept.
  Work item pages now also accept `?iteration=&state=&q=` filters (used by voice search).
* AI Test Design is kept: requirements, plan, strategy, cases and data, with publishing to TFS.
* Online and offline TFS profiles, Test Connection and PASS/FAIL diagnostics are kept unchanged. A test now
  also updates the status strip.

## Automation Studio

* AI-Driven Automation and Script Studio are kept: view, edit, Flow Logic, data, credentials, history, results,
  logs, evidence, regenerate.
* The Recorded Steps editor and saved credentials are kept.
* **Changed: Guided Browser Automation.** Voice commands now work while recording, and relative dates are
  recorded as rules.
* Application Discovery and Update is kept. **Changed:** interactive waits show PAUSED.
* Hybrid automation is kept.
* **Changed: playback.** The synchronized cursor, click sound, visible dropdown choice and `pick_date` are new.
  Locator self-healing and evidence capture are unchanged.

## Performance

* JMeter load, stress, spike and soak tests, results and performance defects are kept.

## Quality

* Defects, Defect Review, Reports and Analytics are kept.

## Settings

* **Changed: Preferences.** Theme, defect mode and the playback cursor settings (speed, highlight, click sound,
  volume).
* TFS Connection is kept.
* **Changed: AI Runtime.** It adds the Managed Local LLM (llama-server start, restart and stop, Auto Detect).
* **New: Voice & Command Agent.** Agent name, listening, recognition, voice reply, radio and ambience, and the
  playback cursor.

## Voice commands (registry)

* **Conversation:** help, status, confirm, cancel, go back, select the Nth item.
* **Navigation:** open a page, open or search work items.
* **Test design:** generate test cases or the test plan; save test cases to TFS; create, write or delete test data.
* **Automation:** generate automation; open a script (any tab); archive a script; run a test case or all tests;
  stop the run.
* **Recording:** start or stop recording; browser actions (click, fill, select, open calendar, select a date,
  next page, scroll).
* **Results:** performance tests; show failed tests, results, evidence and logs; the performance report;
  generate or show the QA report; analyze a failure; create defects.
* **Diagnostics:** test TFS (online or offline); switch the TFS profile; test the local AI; read notifications.

## Platform

* The application starts from the desktop icon and runs as a normal Windows user. It installs from the
  offline package without Administrator rights.
* The local AI is reached through an OpenAI-compatible endpoint (llama.cpp with Qwen GGUF, or Ollama). There
  is no cloud dependency.
* Offline speech uses Vosk. Spoken replies use Windows SAPI voices.
* The runtime stops only what it owns at shutdown: the managed LLM it started.
