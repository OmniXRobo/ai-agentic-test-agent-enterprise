SkyGuard QA 23.15.1 - cumulative upgrade from SGQA-23.3.35 (AI test case generation on the Test Cases page fixed
and faster, on top of 23.15.0)
===============================================================================================

WHAT THIS IS
  One upgrade that brings any installed SkyGuard QA from 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1,
  23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1, 23.11.2, 23.12.0, 23.13.0, 23.13.1, 23.13.2, 23.13.3, 23.14.0,
  23.14.1 or 23.15.0 to 23.15.1, including every release in between. Any other version is refused and nothing is
  changed. No internet. Windows 10 (1909 or later) or Windows 11.

  New in 23.15.1 (details: PATCH_CHANGELOG.md, cause: ROOT_CAUSE_ANALYSIS.md, how to use it: USER_GUIDE.md):
  - "GENERATE TEST CASES WITH AI" ON THE TEST CASES PAGE WORKS WITH THE LOCAL MODEL. It stopped with "The
    On-Premise AI did not return valid structured output" while AI Test Design worked for the same story. The Test
    Cases page did not send the answer format to the AI server (AI Test Design always did); now it does, and an
    almost-right answer is repaired instead of refused, case by case.
  - FASTER, SAME QUALITY: the AI server enforces valid criterion ids and at least two steps per case, texts stay
    short, and only the cases the quality rules flag are reviewed a second time.
  - The browser companion answers a refused request cleanly on Windows.
  - Everything from 23.15.0 and earlier is kept (no database change).

  New in 23.15.0 (details: previous-23.15.0/PATCH_CHANGELOG.md):
  - CREATE TFS DEFECT WORKS FROM EVERY RESULT. Vulnerability Scanning, Execution History (a failed run and a run you
    stopped) and Performance Results (passed or failed) answered "Request validation failed"; they now open a
    preview and create the defect. The page sent the severity as a number and the API asked for text: that was the
    whole cause.
  - The defect reads like a defect: numbered steps with their result, Expected result, Actual result, the error, the
    browser, address, script and run - not an escaped JSON dump.
  - The evidence is attached: the run's screenshots (the failed step first), the trace, the video, the report and
    the result files are uploaded to the TFS work item. For a performance run, qa-report.pdf and metrics.json.
  - "Approve and send to TFS now" creates the bug in one step; Defect Review (review, approve, publish) is
    unchanged, a duplicate is reported instead of creating a second bug, and a TFS refusal keeps the draft.
  - THE AI AGENT IS NOW THE WORKFLOW OPERATOR (same page, same link). It stops asking twice:
    * one preview, then a plain "yes" ("go ahead", "run it", or in Arabic) runs it - no more waiting at WAITING,
    * what you typed while teaching, and the values you used last time, fill what your request leaves unsaid (the
      preview says where each value came from),
    * one request becomes many records: "create 10 trips with the same data, one per day, drivers: A, B, C",
    * your usual requests are remembered and offered as suggestions,
    * THE SIGN-IN PASSWORD AND AN AUTHORIZATION CODE YOU TYPE WHILE TEACHING ARE KEPT for you, in the Windows
      Credential Manager, masked everywhere as ******** - a one-time code (SMS, authenticator) is never kept.
  - VOICE IN THE LANGUAGE YOU SPEAK. Arabic as the recognizer writes it is understood ("open notifications",
    "open the dashboard") and answered in Arabic; "English and Arabic" really detects Arabic now; a page name said
    with a small slip still opens the page.
  - LISTEN ALWAYS: press it once in the voice popup (or in the extension's chat) and speak one request after
    another with no button in between. Press Stop listening when you are done.
  - Browser extension 1.5.0 (same extension ID): the new name, Listen always, and the agent core of 23.15.0.
    Run UPDATE_EXTENSION.cmd, then press Reload on the SkyGuard card of edge://extensions.
  - Everything from 23.14.x is kept, including your Arabic voice, speech model and voice settings (Setup checks
    this; it never downloads them).

  New in 23.14.1:
  - TFS is fast again on every TFS page for accounts with their own PAT and project; the Dashboard shows everything
    at once and never waits more than 8 seconds for TFS; Connection Test is quicker.
  - Test design, test data, script generation, voice commands, reports and defect analysis use YOUR PAT and project.

  New in 23.14.0 (details: PATCH_CHANGELOG.md, guides: USER_GUIDE.md):
  - Security Testing menu (8 screens): AI Security Assessment, Application Discovery, Vulnerability Scanning,
    Security Test Scenarios, Security Findings, Security Regression, Security Reports, Security Preferences.
    OWASP checks, read-only discovery, OWASP ZAP on this PC (optional, pre-staged), bounded fuzzing, authorization
    recorded before any run, nothing state-changing on Production.
  - Integration Testing > Red Hat Fuse Testing (8 tabs): connections (Karaf, Spring Boot, EAP or black box), route
    discovery and mapping, AI test designer, execution, monitoring, transformation comparison, results and defects.
    Read-only: routes are never started, stopped, restarted or changed.
  - Integration Testing > ActiveMQ Testing (Classic and Artemis, 8 tabs): publish and verify, consumer, retry and
    dead-letter, TTL, priority, selectors, monitoring. Only the test destinations you authorize; never purges and
    never consumes business messages.
  - The same buttons on every result: Analyze with AI, Create TFS Defect, Generate PDF Report, Email Developer,
    View Evidence, Retest (a passed result: evidence, PDF and email only).
  - Settings > Email / SMTP: company SMTP server, CA certificate, allowed recipient domains, attachment limits,
    developer directory, Test connection and the send history. Nothing is sent before you press Send and confirm.
  - Dashboard figures for Security, Fuse, ActiveMQ and the shared actions (click to open the items behind them).
  - Agent Workflow lists the new agents; voice commands open the new screens (English and Arabic); Voice settings
    have Test microphone, Test Arabic transcription and Test command interpretation.
  - Your Arabic voice and speech model are kept exactly as installed (Setup checks this; it never downloads them).

  WHERE IS THE SKYGUARD QA FOLDER? (INSTALL_ARABIC_VOICE.cmd, GET_ARABIC_VOICE.ps1, INSTALL_SPEECH_MODEL.cmd)
    Paste this into the File Explorer address bar:  %LOCALAPPDATA%\Programs\AIAgenticTestAgent\app
    If Setup made a side-by-side copy:              %LOCALAPPDATA%\SkyGuardQA\app-23.15.1
    Or right-click the SkyGuard QA shortcut > Open file location.

  New in 23.13.3:
  - AI Agent: Arabic translated literally, page choices as buttons, replies in the language spoken; websites
    whose address changes on every visit are recognized. Browser extension 1.4.3.

  New in 23.13.2:
  - AI Agent page: a request made by voice gets a spoken reply, in English for English and in Arabic (the Arabic
    voice) for Arabic. The browser extension 1.4.2 does the same (UPDATE_EXTENSION.cmd).

  New in 23.13.1:
  - Arabic replies are spoken by an Arabic voice, male or female as you choose (Voice settings > Arabic reply voice).
    The offline Arabic voice is installed once: GET_ARABIC_VOICE.ps1 on a PC with Internet, then
    INSTALL_ARABIC_VOICE.cmd in the SkyGuard QA folder. A Windows Arabic voice (Naayf, Hoda) is used when installed.
  - The agent's name said in Arabic is understood however the recognizer spells it, and common misheard Arabic
    command words still work (for example "Captain, open page" heard badly).
  - INSTALL_SPEECH_MODEL.cmd (the Voice hint referred to it) installs the Arabic speech model offline; a model
    folder with another name (for example small.ar) is used for Arabic automatically.

  New in 23.13.0:
  - Microsoft Edge is used for AI-Driven, Discovery and Hybrid when it is chosen in Preferences; a browser that is
    not installed is replaced by the next one (Preferences, Edge, Chrome).
  - Dashboard: charts first, click any chart to see the items behind it; AI analysis at the bottom.
  - Report bar on every testing page: Generate AI result, Create TFS defect, Send email (PDF). Analytics AI.
  - Defect Review: "Assign to" is a list of the people in your TFS project.
  - Users: email, own TFS project and Personal Access Token (required); welcome email with the address, user name,
    first password and the user manual (PDF).
  - HTTPS with a friendly name (for example https://skyguard-qa/), made offline on this PC.
  - Healing is kept in the script; dynamic URLs; Guided Browser tabs; API Testing Open and TLS fixes.
  - AI Agent: Arabic commands, public websites without an account, one browser window, the lifecycle filter fix.
  - Voice: Arabic and UAE Arabic; the chosen male voice no longer answers with a female voice.

HOW TO INSTALL (5 minutes)
  1. Close SkyGuard QA.
  2. Double-click  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe
     Welcome -> Detect (finds your installation and version) -> Backup -> Install -> Verify -> Finish.
     If Windows shows "Windows protected your PC": More info -> Run anyway (the program is not code-signed).
  3. In the browser press Ctrl+F5 once. (From 23.12.0 or older: open Users and add each person's email, TFS project
     and PAT.)
  4. Arabic voice: nothing to do if it already works (it is kept). If Setup reports it missing, run
     INSTALL_ARABIC_VOICE.cmd once with the zip from GET_ARABIC_VOICE.ps1 (see above).
  5. Try the fixes: Execution History > a failed or stopped run > Create TFS Defect (the preview opens; press
     "Approve and send to TFS now" to create the bug in one step), and Performance Results > Create TFS Defect.
  6. The left menu entry AI Agent now reads Workflow Operator (same page). Teach a workflow once, then ask for it
     by name and answer "yes" to the preview.
  7. Voice: open the voice popup and press "Listen always" to speak one request after another. Speak Arabic and the
     answer comes back in Arabic.

  If your PC does not allow starting the .exe: open the folder Manual_Install and double-click APPLY_PATCH.cmd.
  It does exactly the same upgrade, with the same checks, backup and automatic restore.

  Browser extension users: run UPDATE_EXTENSION.cmd from SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip
  (extension 1.5.0), then press Reload on the SkyGuard card of edge://extensions. The extension keeps its ID, so
  nothing has to be paired or allowed again.

YOUR DATA
  Backed up before anything changes (patch-backups folder in your installation). Never deleted. If any step fails,
  the previous version is restored automatically. To undo later: patch-tools\23.15.1\ROLLBACK_PATCH.cmd.
  The Arabic voice, the speech models and the voice settings are not touched; Setup verifies they are unchanged.

IN THIS FOLDER
  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe   the installer (wizard; unattended: /S /APP="<folder>")
  INSTALLATION_GUIDE.md          step by step, unattended install, protected folders, rollback, troubleshooting
  PATCH_CHANGELOG.md             what is new in 23.15.1
  USER_GUIDE.md                  Create TFS Defect from any result, the Workflow Operator and voice step by step
  COMPATIBILITY_MATRIX.md        Fuse, ActiveMQ, ZAP, SMTP, browser and Windows versions: tested or not
  DEPENDENCY_INVENTORY.md        what each feature needs, all offline
  ROOT_CAUSE_ANALYSIS.md         what was found and changed, and why
  RELEASE_INCLUSION_MANIFEST.md  every release included (23.4.0 to 23.15.1) and what each one added
  SECURITY_REPORT.md             security checks, controls and residual risks
  sbom.cdx.json                  software bill of materials (CycloneDX 1.5)
  TEST_REPORT.md, test-results\  what was tested, passed, failed, skipped or not executed
  KNOWN_LIMITATIONS.md           what was not verified, and why
  FINAL_RELEASE_REPORT.md        summary of the release
  SOURCE_ARCHITECTURE_AUDIT.md   where each part is, and the release number
  Manual_Install\                the same upgrade without the .exe
  SHA256SUMS.txt                 SHA-256 of every file here (certutil -hashfile <file> SHA256)
