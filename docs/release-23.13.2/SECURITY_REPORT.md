# Security report: SkyGuard QA 23.13.2 and extension 1.4.2

## 23.13.2 (21 November 2026)

| Area | Check |
|---|---|
| Companion `/v1/voice/speak` | Needs a paired device and a signed-in user with write access (as transcribe); rate-limited in the "voice" bucket; JSON body validated (text at most 600 characters, gender only male/female); Piper started with fixed arguments, text on stdin, output in a temporary folder that is deleted. No new extension permission; the manifest's permissions are unchanged. |
| Spoken line | Built only from the reply text the page already shows (web addresses removed); the Arabic translation prompt is the same as for the Voice Command Center. |
| Installer | `INSTALL_ARABIC_VOICE.ps1` copies the voice into `%LOCALAPPDATA%\SkyGuardAgent\runtime\piper` only when the companion is installed; per user. |


## 23.13.1 (21 November 2026)

Scope: what 23.13.1 changes since 23.13.0 (commit `e0f9ac4`): `qa_platform/voice/piper.py` (new), `tts.py`,
`service.py`, `fw_service.py`, `config.py`, `api/voice.py`, `skyguard_agent/translate.py`, `web/js/voice.js`,
`web/js/radio.js`, `web/js/pages/voice.js`, and the PowerShell helpers `GET_ARABIC_VOICE.ps1`,
`INSTALL_ARABIC_VOICE.cmd/.ps1`, `INSTALL_SPEECH_MODEL.cmd/.ps1`, `GET_FASTER_WHISPER_MODEL.ps1`.

| Check | Result |
|---|---|
| bandit on the changed Python modules | 5 low findings, all reviewed: B404/B603 `subprocess` (Piper and PowerShell are started with fixed argument lists, no shell, the reply text goes on stdin or in an environment variable, never on the command line), B110 (an existing fallback to the word list). No medium or high finding. (`test-results/bandit-231301.json`) |
| New dependencies | None in Python. Piper (MIT) is downloaded only by `GET_ARABIC_VOICE.ps1` on an internet PC, checked against a pinned SHA-256 (`f3c58906...1efcea`); the voice model is checked against the SHA-256 Hugging Face publishes. |
| What runs | Only `runtime\piper\piper.exe` (or `piper.exe` under `QA_PIPER_DIR`), with `--model <installed .onnx> --output_file <cache .wav> --length_scale <number>`. The model path comes from the installed folder, not from a request. |
| API | `PUT /api/voice/settings` takes `arabic_voice_gender` only as "", "male" or "female" (422 otherwise). `/api/voice/speak` keeps its 600-character limit. |
| Installer helpers | Offline, per user, no administrator rights, staged install (`.new` folder renamed into place); unpacked with .NET `ZipFile` into a new temporary folder; a zip without the expected files is refused and nothing changes (tested in Windows CI). The scripts are ASCII only, so Windows PowerShell 5.1 reads them as written. |
| Wake-name matching | Only removes the leading name from the text; the command then goes through the same parser, confirmations and permissions as before. |

Residual risk: Piper's own program is a third-party binary (MIT, from the rhasspy GitHub release). It is pinned by
SHA-256 but not code-signed.

## 23.13.0

Scope: everything 23.13.0 changes since 23.12.0 (commit `fd5a117`), 45 Python files and the changed pages. Most
attention went to the parts that are new kinds of exposure: HTTPS (a local certificate authority made on this PC),
personal TFS tokens, the welcome email with a password, the report and defect buttons, healing that writes into
scripts, and the wider "same site" rule of the AI Agent. For 23.12.0 and earlier see `previous-23.12.0/` and the
other `previous-*` folders.

## 1. Tools run (20 November 2026)

| Check | Tool | Result |
|---|---|---|
| Static analysis of every changed Python file | `bandit 1.9.4 -ll` | 20 findings at medium or high. **1 high (B324, SHA-1)**: the RFC 5280 key identifier of the certificates; it is an identifier, not a signature. Marked `usedforsecurity=False`. **1 B104 (bind to 0.0.0.0)**: existing, only with *Network access* on, which an administrator turns on. **18 B608 (SQL built from strings)**: each was read. They join the code's own column names (`UPDATE users SET {k} = ?` over fixed keys), `?` placeholder lists (`IN (?,?,...)`) or fixed `WHERE` clauses; every value is a bound parameter. **No injectable query.** |
| Known vulnerabilities in dependencies | `pip-audit 2.10.1 -r requirements.txt` | **No known vulnerabilities found.** |
| New dependencies | `requirements*.txt` | **None.** The certificates are made with the Python standard library (no `cryptography` wheel was added). |
| Secrets in the change | Search for private keys, tokens and password assignments | **No real secrets.** Test PATs and passwords are fixed fake values in `tests/`. |

## 2. Findings fixed in this review (5)

| # | Finding | Fix |
|---|---|---|
| 1 | The first password goes out by email in clear text (as asked), but new accounts did not have to change it. | When the welcome email is sent with a password, the account is marked *must change password*; the person sets a new one at the first sign-in. Test: `test_welcome_email_carries_the_address_and_the_manual`. |
| 2 | *Trust on this PC* built a PowerShell command with the data folder path inside single quotes. A folder name with a `'` would have broken out of the quoting (administrator-only route, path chosen at install time). | Every value is now a PowerShell single-quoted literal with quotes doubled; the hostname is validated by a strict pattern and the port is an integer. |
| 3 | The page under test can call the binding that reports what a person clicked while a run is paused for help. | Only small (< 20 KB), well-formed `click`/`change` reports with a fingerprint object are kept, at most 50, and only while that run is paused for help. Selectors taken from them are cut to 500 characters. |
| 4 | *Approve and send now* on the new defect button used the "send anyway" path, which skips the likely-duplicate check, and could send the same defect twice. | It approves (approve permission checked) and then publishes **with** the duplicate check; a likely duplicate stays in Defect Review with the reason, and the same defect sent again returns the existing work item instead of a new one. Test: `test_assignees_come_from_tfs_and_reach_the_defect`. |
| 5 | SHA-1 flagged by bandit. | Used only for the certificate key identifier; marked `usedforsecurity=False`. Signatures are SHA-256 with RSA. |

## 3. New features: threats and controls

| Area | Threat | Control |
|---|---|---|
| **HTTPS certificates** | Weak keys or a predictable RNG. | RSA-2048 (e = 65537) from `secrets` (the OS CSPRNG), 40 Miller-Rabin rounds, each signature verified before use (RSA-CRT fault check). Checked with `openssl verify`, `openssl x509 -text` and a Python TLS handshake that verifies the chain and the name (tests). |
| | The CA private key stolen and used to sign certificates for other sites. | The CA is a *local* root that only this PC (and colleagues who run `TRUST_SKYGUARD_QA.cmd`) trusts. Its key is in the data folder with owner-only permissions where the file system supports them (`0600`), and it is kept so renewals need no new trust. **Residual risk 1** below. |
| | Certificate for any name. | The server certificate names only the chosen name, `localhost`, the PC name, `127.0.0.1` and the PC's own addresses; it is valid 825 days (CA 10 years); key usage and extended key usage are set (server authentication only, CA basic constraints with path length 0). |
| | Host header / DNS rebinding with the new name. | The Host check accepts the configured HTTPS name only after the administrator sets it (`https_hosts`); other names are still refused. |
| | Who can turn HTTPS on or run the trust script. | Under `/api/auth/network/...`: administrators only, CSRF checks as for every change. The trust script asks Windows for elevation on this PC's screen; nothing is elevated silently. The CA certificate download is public data (no key). |
| **Personal TFS PAT** | One person's token used for another person's data. | The PAT is stored per account (`tfs-user`, key `<user>:<mode>`) in Windows Credential Manager; never returned by the API (only *has PAT*); TFS caches are keyed by person and project, so no answer is shared between people. Deleting a user deletes their token. |
| | An invalid or wrong PAT saved. | Checked against the project when TFS can be reached (422 `TFS_PAT_INVALID`); the account is not created without its PAT (rolled back if saving fails). |
| **Welcome email** | Password in an email. | Asked for by the request. The first password must be changed at the first sign-in (fix 1). *Send manual* never includes a password. The email goes only to the address typed for that user, through the Outlook account set up by the administrator. |
| **Report buttons / module PDF** | Data from other modules or other people in a report. | `module_facts` reads only the named module's tables; the module name must be in a fixed list (`MODULES`); all values are escaped in the HTML/PDF. Email uses the existing reviewed mail service. |
| **Create TFS defect** | Bypassing approval. | Drafts go to Defect Review. *Approve and send now* is honoured only for roles that may approve (the same check as the Approve button); view-only roles do not see the button and the API refuses them. The duplicate signature check still applies. |
| **Healing that is kept** | A wrong locator saved into scripts. | Only a healed locator whose own step then **passed** is written; the old selector stays as an alternate; every change is a new script version (history and rollback); `playback.auto_apply_healing` in `settings.json` turns it off. |
| **AI Agent same-site rule** | The agent treats another site as the application. | Only host names of the **same registrable domain and scheme** count (`www.`, `booking.` of `example.ae`); a different domain, IP addresses and single-label names never match. **Residual risk 3** below. |
| **Public sites without account** | A site marked public by mistake. | Editors only; shown and reversible under Applications & Accounts; it stops the agent asking for credentials, it does not bypass any sign-in. |
| **Arabic translation** | Prompt injection through a command. | The translation is only text for the existing command parser; the English command then goes through the same confirmation and permission rules as typed English. Everything runs on the local AI; nothing leaves the PC. |
| **Offline Arabic voice (Piper)** | A tampered program or voice; text reaching a command line. | `GET_ARABIC_VOICE.ps1` checks Piper against a pinned SHA-256 and the voice files against the SHA-256 Hugging Face publishes; the install is offline, per user, staged and needs no administrator rights. The runtime runs only `runtime\piper\piper.exe` with fixed arguments; the reply text goes on stdin, never on the command line; output is a WAV in the voice cache. |
| **API Testing TLS trust** | Turning verification off to "fix" the false FAIL. | Verification stays on. The Windows trust store is added to the public bundle and the application's own CA; nothing is skipped. |
| **Browser fallback** | Using a browser nobody chose. | Only a browser that is *not installed* is replaced, in the order Preferences, Edge, Chrome; the switch is logged. |
| **Extension 1.4.1** | New permissions. | None: manifest permissions and host permissions are unchanged; the companion still listens on 127.0.0.1 only with pairing. |

## 4. Residual risks (stated so they can be judged)

1. **Local CA key on disk.** Anyone who can read this PC's SkyGuard data folder (an administrator, or malware running
   as the user) could sign certificates that PCs trusting the SkyGuard CA would accept. Keep the data folder on the
   user's own profile, trust the CA only on PCs that need it, and renew (Users > HTTPS > *Renew*) if the PC is
   compromised. An office PKI certificate is the stronger option where one exists.
2. **Hand-written RSA and X.509.** The `cryptography` package is not in the offline bundle, so the certificate
   maker uses Python integers. It is used only to *make* certificates once; TLS itself is Python's OpenSSL. The
   output was checked with OpenSSL and a verified handshake, but the code has not had an external review. Python
   integer arithmetic is not constant-time; this matters little for a one-time local operation.
3. **Shared hosting domains.** Without the public suffix list offline, `same_site` treats `a.github.io` and
   `b.github.io` (or similar shared hosting) as one site. Register such applications with their exact address.
4. **Password in email.** Mail can be stored in mailboxes and backups. The password must be changed at the first
   sign-in, which limits the exposure to the time before that.
5. Unchanged from 23.12.0: DNS rebinding between two lookups in API Testing, the Python-level script guard, and
   Setup.exe not code-signed.
