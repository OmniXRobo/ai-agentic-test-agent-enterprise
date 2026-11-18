SkyGuard QA 23.12.0 - cumulative upgrade from SGQA-23.3.35 (API Testing, schedulable API tests and comparisons)
==============================================================================================================

WHAT THIS IS
  One upgrade that brings any installed SkyGuard QA from 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1,
  23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1 or 23.11.2 to 23.12.0, including every release in between. Any
  other version is refused and nothing is changed. No administrator rights. No internet. Windows 10 (1909 or later)
  or Windows 11.

  New in 23.12.0:
  - Automation Studio > API Testing: authorized targets, Test Connection with real diagnostics, OpenAPI / Swagger
    (JSON or YAML, file or intranet URL, fully offline), cURL / Postman / Python imports (never executed), generated
    tests with documented expectations, suites and chains, pytest + httpx scripts, response viewer, failures as
    Defect Review drafts (published to TFS only after approval). Secrets stay in the Windows Credential Manager.
  - Scheduled Runs: API tests and Integration Comparisons; execution mode Visible / Headless / Smart Auto.
  - Integration Comparison: confirmed mismatches become Defect Review drafts.
  - Agent Workflow (same page): six new agents, and UNAVAILABLE with the reason when an engine is missing.
  - Python editor: full screen and adjustable height.

HOW TO INSTALL (5 minutes)
  1. Close SkyGuard QA.
  2. Double-click  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe
     Welcome -> Detect (finds your installation and version) -> Backup -> Install -> Verify -> Finish.
     If Windows shows "Windows protected your PC": More info -> Run anyway (the program is not code-signed).
  3. In the browser press Ctrl+F5 once. Open Automation Studio > API Testing > New application.

  If your PC does not allow starting the .exe: open the folder Manual_Install and double-click APPLY_PATCH.cmd.
  It does exactly the same upgrade, with the same checks, backup and automatic restore.

  Browser extension users: the extension stays 1.3.1. If you have not installed 1.3.1 yet, run UPDATE_EXTENSION.cmd
  from SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip.

YOUR DATA
  Backed up before anything changes (patch-backups folder in your installation). Never deleted. If any step fails,
  the previous version is restored automatically. To undo later: patch-tools\23.12.0\ROLLBACK_PATCH.cmd.

IN THIS FOLDER
  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe   the installer (wizard; unattended: /S /APP="<folder>")
  INSTALLATION_GUIDE.md          step by step, unattended install, protected folders, rollback, troubleshooting
  PATCH_CHANGELOG.md             what is new in 23.12.0
  USER_GUIDE.md                  API Testing step by step
  ROOT_CAUSE_ANALYSIS.md         what was found and changed, and why
  RELEASE_INCLUSION_MANIFEST.md  every release included (23.4.0 to 23.12.0) and what each one added
  SECURITY_REPORT.md             security checks, controls and residual risks
  sbom.cdx.json                  software bill of materials (CycloneDX 1.5)
  TEST_REPORT.md, test-results\  what was tested, passed, failed, skipped or not executed
  KNOWN_LIMITATIONS.md           what was not verified, and why
  FINAL_RELEASE_REPORT.md        summary of the release
  SOURCE_ARCHITECTURE_AUDIT.md   where each part is, and the release number
  Manual_Install\                the same upgrade without the .exe
  SHA256SUMS.txt                 SHA-256 of every file here (certutil -hashfile <file> SHA256)
