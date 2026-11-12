# SkyGuard QA 23.5.0: final release notes

**Adaptive AI Application Learning & Operations Agent**, a cumulative patch for SkyGuard QA 23.4.0, and its
companion deliverable, the **SkyGuard Adaptive AI Browser Extension** (Chrome and Edge, independent of SkyGuard).

## Deliverables

| Zip | For | Contents |
| --- | --- | --- |
| `SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch.zip` | SkyGuard QA 23.4.0 installations | application files changed since 23.4.0, installer with backup / verify / rollback, tests, documents, SHA-256 |
| `SkyGuard_Adaptive_AI_Browser_Extension_Final.zip` | any Windows 11 PC with Chrome or Edge, SkyGuard not needed | MV3 extension, local companion, offline runtimes, per-user installer and uninstaller, model installer, documents, tests, SHA-256 |

Each zip has a `.zip.sha256` next to it and a `SHA256_CHECKSUM.txt` inside.

## What it does

* Learns a web application with your approval: read-only discovery, guided learning by demonstration, rules described
  in plain words, AI suggestions from the local Qwen model. Everything learned is versioned and used only after
  approval.
* Operates the application through its normal screens from a request in plain words or voice: checks existing
  records, applies the approved business rules, shows a preview, runs only after confirmation, verifies the
  application's own response for every record, and reports honestly (confirmed success, confirmed failure, skipped,
  manual intervention required, unknown).
* Several accounts in one request, with verified sign-out and sign-in and identity and role checks.
* Self-healing when screens change, and a clear pause for the person when it cannot be sure, then learns from what
  the person did.
* Pause, resume, stop, cancel remaining; recovery after a restart.
* Security: no password ever reaches the AI, chat, logs, audit or memory; credentials only with consent in an
  encrypted vault; OTP, MFA, CAPTCHA and driver codes always left to the person; no database writes; no script value
  assignment; web content treated as data; local only, no cloud.

## Documents

INSTALLATION_GUIDE, USER_GUIDE, PATCH_CHANGELOG, PATCH_COMPATIBILITY, SECURITY_AUDIT, REGRESSION_TEST_REPORT,
OFFLINE_INSTALLATION_TEST_REPORT (this zip). README.txt and docs\ (the extension zip): Windows 11, Chrome, Edge,
companion, enterprise deployment, offline dependencies, security, troubleshooting, uninstall, user guide, test report,
changelog.

## Known limitations

* Not verified on a Windows 11 PC, in real Google Chrome or Microsoft Edge builds, against the real transport
  application, with Windows Credential Manager or DPAPI, or with the Qwen and Faster-Whisper runtimes loaded. The
  PowerShell and .cmd wrappers were not executed (no Windows). See the test reports for exactly what was run.
* The browser extension works in the person's own browser profile: one application session at a time per profile,
  and the person should not use the same application in another tab during a run.
* Applications served from `127.0.0.1` are not supported by the extension (use `localhost` or the PC name).
* Fields that refuse typed input pause the run (or use the extension's optional trusted typing).
* Models are not bundled in the extension zip; they are copied from SkyGuard or offline media with SHA-256 checks.
