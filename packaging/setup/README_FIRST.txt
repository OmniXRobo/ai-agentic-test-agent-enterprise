SkyGuard QA 23.10.0 - cumulative upgrade from SGQA-23.3.35
==========================================================

WHAT THIS IS
  One upgrade that brings any installed SkyGuard QA from 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1,
  23.7.2, 23.8.0 or 23.9.0 to 23.10.0, including every release in between. Any other version is refused and nothing
  is changed. No administrator rights. No internet. Windows 10 (1909 or later) or Windows 11.

HOW TO INSTALL (5 minutes)
  1. Close SkyGuard QA.
  2. Double-click  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe
     Welcome -> Detect (finds your installation and version) -> Backup -> Install -> Verify -> Finish.
     If Windows shows "Windows protected your PC": More info -> Run anyway (the program is not code-signed).
  3. In the browser press Ctrl+F5 once. Open "AI Agent" in the menu.

  If your PC does not allow starting the .exe: open the folder Manual_Install and double-click APPLY_PATCH.cmd.
  It does exactly the same upgrade, with the same checks, backup and automatic restore.

  Browser extension users: also run UPDATE_EXTENSION.cmd from SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip.

YOUR DATA
  Backed up before anything changes (patch-backups folder in your installation). Never deleted. If any step fails,
  the previous version is restored automatically. To undo later: patch-tools\23.10.0\ROLLBACK_PATCH.cmd.

IN THIS FOLDER
  SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe   the installer (wizard; unattended: /S /APP="<folder>")
  INSTALLATION_GUIDE.md          step by step, unattended install, protected folders, rollback, troubleshooting
  RELEASE_INCLUSION_MANIFEST.md  every release included (23.4.0 to 23.10.0) and what each one added
  PATCH_CHANGELOG.md             what is new in 23.10.0
  USER_GUIDE.md                  the new AI Agent and guided recording for the Integration Comparison
  SECURITY_REPORT.md             security checks and their results
  sbom.cdx.json                  software bill of materials (CycloneDX 1.5)
  TEST_REPORT.md, test-results\  what was tested, passed, failed, blocked or not executed
  KNOWN_LIMITATIONS.md           what was not verified, and why
  FINAL_RELEASE_REPORT.md        summary of the release
  SOURCE_ARCHITECTURE_AUDIT.md   source and architecture audit
  Manual_Install\                the same upgrade without the .exe
  SHA256SUMS.txt                 SHA-256 of every file here (certutil -hashfile <file> SHA256)
