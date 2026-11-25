# Security report: SkyGuard QA 23.14.1

> **23.14.1:** no change to the security controls below. The fix reuses TFS connections and keeps PATs only in the
> Windows Credential Manager and in memory (never in settings.json, logs or responses); certificate checking is
> unchanged. Background work now runs with the signed-in person's own PAT instead of the administrator's.

23.14.0 adds tools that **attack applications on purpose** (security testing) and tools that **write to message
brokers** (ActiveMQ / Fuse testing), plus outgoing email. The design rule throughout: deterministic code decides what is
allowed; the local AI only drafts text and tests that a person reviews. The AI never decides whether an action is
permitted. For 23.13.3 and earlier see `previous-23.13.3/` and the other `previous-*` folders.

## 1. Controls by module

| Area | Control (enforced in code, tested) |
|---|---|
| Authorization to test | Every Security, Fuse and ActiveMQ target needs a recorded authorization (who approved, scope, period, reference, recorded by whom and when) before any run; audited. |
| Scope | Security requests go only to the target's base URL and allowed paths (excluded paths and other hosts refused, redirects to other hosts not followed); each request checked by `ScopeGuard` and the API Testing `Policy`. The STOMP / Jolokia / Fuse endpoints must be the exact host and port configured. |
| Production | No state-changing security test, and no publishing to a Production broker without a separate production approval; a Production security target cannot be saved with state-changing tests allowed (422). |
| Rate and budget | Every security run has a request rate, a request budget and a time limit (`Throttle`); fuzzing is bounded (12 / 30 inputs per parameter). Pause, resume and cancel are honoured between requests. |
| Discovery | GET only; forms and logout / delete / state-changing links are listed, never followed or submitted. Active tests use only an application map a tester approved. |
| Broker writes | Publish only to the configured test destinations (exact names or a `PREFIX.*` pattern); consume only from the consume destinations (a subset). DLQ, ExpiryQueue, `ActiveMQ.*` advisory / system destinations and wildcards are refused for publishing. Outputs are verified by **browsing** (no consumption). |
| Broker management | Jolokia calls limited to `read`, `search`, `version`, `list` and `exec` of a whitelist of read operations (`browse`, `countMessages`, `listMessagesAsJSON`, `dumpRouteAsXml` ...); a second deny pattern refuses purge / remove / move / retry / pause / resume / stop / start / destroy / delete / create / reset even if the whitelist were changed. Fuse routes are never started, stopped, restarted, redeployed or changed. |
| ZAP | Only a loopback ZAP address is accepted; the API key is kept in the credential vault; ZAP contexts are limited to the approved scope; alerts outside the scope are dropped. |
| Secrets | Passwords, tokens, API keys and passcodes go to the Windows Credential Manager; a configuration containing a secret-looking field is refused (422). Every evidence text, header, message body and log is passed through the sanitizer (known secret values, Authorization / Cookie / token headers, password fields) **before** it is stored, shown, sent to the local AI, put into a PDF or attached to an email. |
| Local AI | Receives only sanitized facts; output is treated as untrusted text (escaped when shown, never executed); drafted tests and scenarios must be approved by a tester before they run; drafts using destinations outside the test destinations are refused like any other case. |
| Email | Sent only after the person presses Send and confirms; one send per draft (a repeated press returns the earlier result); recipients checked against the allowed domains / sender domain unless external recipients are allowed in settings; addresses validated and CR / LF refused in every header field; attachment size limits; SMTP acceptance recorded separately from delivery; failed sends keep the draft. TLS certificate verification on by default with a company CA file option. |
| STOMP | CR / LF / NUL refused in credentials; header values escaped per STOMP 1.2; TLS with certificate verification by default. |
| XML | DTDs and entities are refused before parsing (route definitions and message comparison). |
| Identifiers | Run, target, case and draft ids are validated (`[A-Za-z0-9_.-]`, no `..`, no `:`) before they are used in file names; PDF report kinds checked against a fixed list (path tricks tested). |
| Permissions | View-only accounts can read results, generate PDFs, compare messages and use voice look-ups; they cannot create targets, start runs, review findings, create defects or send email (tested). |
| Voice | New voice commands only open screens; a scan or broker test cannot be started by voice. `/api/voice/interpret` executes nothing and stores nothing. |
| Installer | Never downloads; the Arabic voice, speech models and voice settings are recorded before the upgrade and verified unchanged afterwards. |

## 2. Tools run (22 November 2026)

| Check | Result |
|---|---|
| bandit on the new modules (`sectest`, `fusetest`, `amqtest`, `itest`, the three new APIs, `mailer.py`, voice intents) | Reviewed every finding (`evidence/bandit-231400.json`). **1 high (B613)**: a fuzzing payload contained a right-to-left override character written literally in the source. **Fixed**: the payloads are now written as `\u` escapes. **11 medium**: 9 × B608 (SQL built from fixed column names and `?` placeholders; values are always bound parameters; no user text reaches the SQL text), 2 × B314 (`xml.etree` on input already rejected when it contains a DTD or entity). Low: B404 / B603 / B607 (the existing Outlook-app sender in `mailer.py`, a fixed PowerShell argument list, no shell), B405 (the guarded `xml.etree` import), B110 (best-effort fallbacks that never hide a test result). |
| Real-broker security test | ActiveMQ Classic 5.18.6 with default settings accepted an anonymous STOMP connection: SkyGuard reports it as a **high** finding (correct). Artemis 2.37.0 with `--require-login` passed. |
| Deliberately vulnerable fixture app | Reflected XSS, SQL error, path traversal, open redirect (also after a redirect), CORS reflection, missing headers and insecure cookies detected; requests outside scope refused; Production state-changing refused. |
| Dependencies | No new package. |

## 3. Residual risks

* Active security tests send attack-like input to the target. Within scope, rate and budget, they can still trigger
  alerts, lock test accounts (the lockout category must be approved, needs state-changing permission and a non-Production target) or create test data on non-Production systems where
  state-changing tests are allowed. Run them only with the recorded authorization.
* Publishing to a test destination that a real consumer also reads would deliver the test message to that consumer.
  Use dedicated `SKYGUARD.TEST.*` destinations.
* "Accepted" by SMTP does not prove delivery; bounces arrive in the sender mailbox, not in SkyGuard.
* ZAP itself (a third-party Java program) is outside SkyGuard's control once started.
* The sanitizer removes known secrets and common patterns; a secret in an unusual format inside a response body could
  remain in evidence. Evidence stays on this PC unless a person attaches it to an email or a defect.
