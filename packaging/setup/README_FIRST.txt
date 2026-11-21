SkyGuard QA 23.13.3 - cumulative upgrade from SGQA-23.3.35 (Arabic AI Agent commands, spoken replies, Arabic voice)
==================================================================================================================

WHAT THIS IS
  One upgrade that brings any installed SkyGuard QA from 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1,
  23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1, 23.11.2, 23.12.0, 23.13.0, 23.13.1 or 23.13.2 to 23.13.3, including
  every release in between. Any
  other version is refused and nothing is changed. No internet. Windows 10 (1909 or later)
  or Windows 11.

  New in 23.13.3 (details: PATCH_CHANGELOG.md):
  - AI Agent: spoken Arabic is translated literally (Arabic "open page" stays "open page", also when the recognizer
    misspells it); nothing that was not said is added. "Which page do you mean?" lists the page names only and offers them as
    buttons.
  - Asked and told in the language you spoke: an Arabic request is answered (and the question asked) in Arabic, an
    English one in English; after the action the agent also says the preview is ready, the task is done or failed,
    in the same language.
  - Websites that change their address on every visit (abc.com/12345, next time abc.com/by123) are the same page
    and the same application: learned pages open with today's address, without asking to be taught again.
  - The browser extension 1.4.3 does all of this too (UPDATE_EXTENSION.cmd).

  WHERE IS THE SKYGUARD QA FOLDER? (INSTALL_ARABIC_VOICE.cmd, GET_ARABIC_VOICE.ps1, INSTALL_SPEECH_MODEL.cmd)
    Paste this into the File Explorer address bar:  %LOCALAPPDATA%\Programs\AIAgenticTestAgent\app
    If Setup made a side-by-side copy:              %LOCALAPPDATA%\SkyGuardQA\app-23.13.3
    Or right-click the SkyGuard QA shortcut > Open file location.

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
  4. For Arabic spoken replies: run INSTALL_ARABIC_VOICE.cmd once with the zip from GET_ARABIC_VOICE.ps1 (see above).

  If your PC does not allow starting the .exe: open the folder Manual_Install and double-click APPLY_PATCH.cmd.
  It does exactly the same upgrade, with the same checks, backup and automatic restore.

  Browser extension users: run UPDATE_EXTENSION.cmd from SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip
  (extension 1.4.2), then press Reload on the SkyGuard card of edge://extensions.

YOUR DATA
  Backed up before anything changes (patch-backups folder in your installation). Never deleted. If any step fails,
  the previous version is restored automatically. To undo later: patch-tools\23.13.2\ROLLBACK_PATCH.cmd.

IN THIS FOLDER
  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe   the installer (wizard; unattended: /S /APP="<folder>")
  INSTALLATION_GUIDE.md          step by step, unattended install, protected folders, rollback, troubleshooting
  PATCH_CHANGELOG.md             what is new in 23.13.2
  USER_GUIDE.md                  API Testing step by step
  ROOT_CAUSE_ANALYSIS.md         what was found and changed, and why
  RELEASE_INCLUSION_MANIFEST.md  every release included (23.4.0 to 23.13.2) and what each one added
  SECURITY_REPORT.md             security checks, controls and residual risks
  sbom.cdx.json                  software bill of materials (CycloneDX 1.5)
  TEST_REPORT.md, test-results\  what was tested, passed, failed, skipped or not executed
  KNOWN_LIMITATIONS.md           what was not verified, and why
  FINAL_RELEASE_REPORT.md        summary of the release
  SOURCE_ARCHITECTURE_AUDIT.md   where each part is, and the release number
  Manual_Install\                the same upgrade without the .exe
  SHA256SUMS.txt                 SHA-256 of every file here (certutil -hashfile <file> SHA256)
