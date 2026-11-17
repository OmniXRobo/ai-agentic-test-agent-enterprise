# Final release report: SkyGuard QA 23.10.0 and extension 1.2.0

## Delivered

| Deliverable | Where |
|---|---|
| One cumulative upgrade from SGQA-23.3.35 (or any later release up to 23.9.0) to 23.10.0, as a Setup.exe wizard | `SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip` → `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` |
| The same upgrade without the wizard (PowerShell 5.1 or cmd), for PCs that block unsigned programs | same ZIP → `Manual_Install/APPLY_PATCH.cmd` |
| The browser extension and its local companion 1.2.0 for Edge and Chrome, offline | `SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip` |
| Release inclusion manifest (every real release, its commit, pull request and date) | RELEASE_INCLUSION_MANIFEST.md |
| Source and architecture audit | SOURCE_ARCHITECTURE_AUDIT.md |
| Installation guide, user guide, changelog | INSTALLATION_GUIDE.md, USER_GUIDE.md, PATCH_CHANGELOG.md |
| Security report and SBOM (CycloneDX 1.5) | SECURITY_REPORT.md, `sbom.cdx.json` |
| Test report with passed, failed, skipped and not executed tests, the JUnit files and logs | TEST_REPORT.md, `test-results/` |
| SHA-256 of every file | `SHA256SUMS.txt`, and a `.sha256` next to each ZIP |
| Known limitations | KNOWN_LIMITATIONS.md |

## The request, part by part

| Part of the request | Status | Evidence |
|---|---|---|
| Every real release from 23.3.35, no invented versions | Done: 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0 | RELEASE_INCLUSION_MANIFEST.md; each is an install baseline in the tests |
| Source and architecture audit | Done | SOURCE_ARCHITECTURE_AUDIT.md |
| Simple AI agent: connect, learn while browsing, use normally, text and voice commands, AI operates after one preview and confirmation | Done, on the existing engine | TEST_REPORT.md section 6, `evidence/ui-*.png` |
| Read-only discovery, business rules, bulk runs with checkpoints, several roles | Done (existing engine, now reachable in plain words) | `test_autopilot_231000.py` |
| Four tabs and Advanced Administrator Settings | Done | `evidence/ui-1*.png` to `ui-4*.png` |
| Guided recording for the Integration Comparison: Start, Stop & Save, Edit, Replay & Verify, Re-record, View Captured Tables; filters editable; every page; master-detail; read-only | Done | `test_guided_comparison_231000.py`, `evidence/ui-c*.png` |
| One browser automation core | Done: one page reader and observer (`agent_dom.js`), one recording converter (`navrec`) for SkyGuard and the extension | SOURCE_ARCHITECTURE_AUDIT.md |
| MV3 offline Edge and Chrome extension | Done, 1.2.0, no new permissions | `test_extension_e2e.py`, `test_extension_120.py` |
| Security audit and fixes, SBOM | Done | SECURITY_REPORT.md, `sbom.cdx.json` |
| Database migrations, data kept, rollback | Done: one new table (`conversation_state`), row counts checked, rollback restores files and databases | TEST_REPORT.md section 3 |
| No-admin offline Setup.exe: Welcome, Detect, Backup, Install, Verify, Finish; detects the version; refuses unsupported versions | Done | TEST_REPORT.md sections 4 and 5, `evidence/setup-*.png` |
| Real testing, reported honestly | Done | TEST_REPORT.md |

## Your PC (Windows 10 1909, Windows PowerShell 5.1, domain-joined)

Setup.exe runs per user, needs no administrator rights, downloads nothing, and uses only `powershell.exe` 5.1 and
the Python that 23.9.0 already installed. It reads the installed version itself: on your PC it will find 23.9.0. It
backs up, upgrades, verifies, and rolls back automatically if any step fails. Windows 10 1909 was not available
for testing; see KNOWN_LIMITATIONS.md for exactly what was tested instead.

## Rules kept

* No passwords, OTPs, MFA secrets, session cookies or codes in prompts, learning memory, recordings, screenshots
  or logs. Tested.
* MFA, CAPTCHA and application permissions are never bypassed. The run pauses for the person.
* Qwen only proposes structured plans. The engine validates them and runs them through its own tools after your
  confirmation. Nothing from the model is run as shell, Python or JavaScript.
* No Program Files, machine registry, system PATH, services or Windows security changes. No enterprise extension
  restriction is bypassed.

## Open points

* Setup.exe is not code-signed (no company certificate was available). SmartScreen may show a warning. Sign it
  before wide distribution, or use `Manual_Install`.
* Not tested on: Windows 10 1909 itself, real Edge and Chrome builds with your policies, a local Qwen server, real
  Faster-Whisper speech, applications with SSO, MFA or Citrix. See KNOWN_LIMITATIONS.md.
