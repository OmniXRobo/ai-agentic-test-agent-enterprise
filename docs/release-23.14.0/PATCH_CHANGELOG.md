# Changelog: SkyGuard QA 23.14.0

Cumulative on 23.13.3 (commit `9b42d1b`). One upgrade for every release from 23.3.35 to 23.13.3. Everything works
offline. The database goes to **23 migrations** (migration 23 adds `itest_targets`, `itest_runs`, `itest_results`,
`itest_cases`, `itest_audit`, `sec_findings`, `sec_app_maps`, `ai_analyses` and `email_outbox`; nothing existing is
changed). The browser extension stays 1.4.3. Earlier changes: `previous-23.13.3/`, `previous-23.13.2/` ...

## Module 1: Security Testing (new menu, 8 screens)

* **AI Security Assessment**: applications with environment, URLs in scope, allowed / excluded paths, roles and test
  accounts (secrets in the Windows Credential Manager), intensity (passive / standard / thorough: rate and request
  budget), approved categories, and the authorization record (who approved, scope, period, reference). No run starts
  without it.
* **Application Discovery**: read-only crawl per role (GET only, forms and state-changing links are listed, never
  submitted), API descriptions, sign-in and upload forms, page access per role. The map must be approved before active
  tests use it.
* **Vulnerability Scanning**: passive checks, the active categories you approve (CORS reflection, reflected XSS,
  error-based SQL injection, path traversal, open redirect, command injection, XML parser, authorization: anonymous,
  vertical and object level, session after logout, lockout), bounded fuzzing (boundary values, encodings, malformed
  JSON / XML, wrong types; at most 12 or 30 inputs per parameter), and an optional OWASP ZAP scan through a ZAP on this
  PC. Pause, resume and cancel; results recorded so far are kept.
* **Security Test Scenarios**: drafted by the local AI from the approved map, reviewed and approved by a tester before
  they run; manual editing.
* **Security Findings**: deduplicated findings with OWASP Top 10 / API Top 10 / WSTG mapping, severity, evidence
  (sanitized), review status (open, confirmed, false positive, accepted risk, fixed) with a note.
* **Security Regression**: re-runs the rules of open findings; a finding is marked fixed only when its own rule ran
  and passed.
* **Security Reports**: offline PDF reports.
* **Security Preferences**: the safety rules and every check with its OWASP mapping; ZAP address.
* 46 rules (headers, cookies, CORS, TLS, error disclosure, secrets / card numbers in responses, caching of
  authenticated pages, forms, directory listing, mixed content, redirects, SSRF parameters, uploads, and the active
  categories above).

## Module 2: Red Hat Fuse Integration Testing (Integration Testing > Red Hat Fuse Testing, 8 tabs)

* **Fuse Connections**: Karaf, Spring Boot or EAP through Jolokia (version detected), Spring Boot actuator, or black box
  (routes described by hand). Linked to an ActiveMQ broker for queue inputs and outputs. Authorization record.
* **Route Discovery / Route Mapping**: CamelContexts, routes, state and statistics; route XML through
  `dumpRouteAsXml` only, parsed into from, to, choices, transformations, error handling and endpoints.
* **AI Test Designer**: drafts routing, transformation, reliability and end-to-end tests; a tester approves them.
* **Test Execution**: queue input (through the ActiveMQ test-destination rules) or HTTP input (in scope, state-changing
  only when allowed), output found by correlation ID on each expected destination and compared, DLQ checked, route
  statistics before / after, the trace. A → Fuse → ActiveMQ → B / C with a correlation ID per run.
* **Route Monitoring**: state, exchanges, failures, redeliveries, in-flight, mean / max time, alerts.
* **Transformation Comparison**: JSON, XML (no DTD / entities) or text, with ignore, field mappings, numeric tolerance,
  date formats, case, trim, ignore order and ignore extra fields.
* **Results & Defects**: runs, results and the result actions.
* Never starts, stops, restarts, redeploys or changes a route: only read operations are called (whitelist).

## Module 3: ActiveMQ Classic and Artemis (Integration Testing > ActiveMQ Testing, 8 tabs)

* Separate adapters for Classic and Artemis over Jolokia (read operations on a whitelist; purge, remove, move, retry,
  pause, delete and the like are refused by name), STOMP for publishing and consuming (TLS with a company CA).
* **Broker Connections**, **Queue & Topic Discovery**, **Message Test Designer** (AI drafts, tester approves),
  **Publish & Verify**, **Consumer Verification**, **Retry & Dead-Letter Testing**, **Broker Monitoring** (depth,
  consumers, enqueue / dequeue, DLQ growth, alerts), **Results & Defects**.
* Test types: publish and verify (JSON path checks), consumer delivery, retry and DLQ, TTL, priority, selectors,
  durable subscriptions, performance (bounded count and rate), and security (anonymous STOMP / management, TLS).
* Publishing only to the destinations listed as test destinations; consuming only from the consume destinations (a
  subset of them). DLQ, ExpiryQueue and `ActiveMQ.*` system destinations can never be published to. Production needs a
  separate approval. Messages are browsed (not consumed) to verify outputs. Nothing is ever purged.

## The same actions on every result

Analyze with AI, Create TFS Defect, Generate PDF Report, Email Developer, View Evidence and Retest, on Security,
Fuse and ActiveMQ results, security findings, suite runs and Execution History. A passed result gets View Evidence,
Generate PDF Report and Email Developer only.

* **Analyze with AI** (local Qwen): verified facts (from the recorded result) are shown apart from the AI's
  hypotheses, likely cause, impact and suggested checks; history of every analysis and Reanalyze. Passwords and tokens
  are removed before anything reaches the model.
* **Create TFS Defect**: the existing TFS setup and Defect Review; an editable preview; published only after you
  confirm.
* **Generate PDF Report**: offline; Execution, AI analysis, Security, Fuse, ActiveMQ, End-to-end, Suite and Defect
  evidence report types.
* **Email Developer**: To / CC / BCC (editable, developer directory), subject and message written for you,
  attachments (PDF, sanitized log, message metadata, screenshots) with size limits, confirmation before sending, one
  send per draft (a second press does not send twice), header-injection and recipient-policy checks, send history,
  "accepted by the SMTP server" kept apart from delivery, the draft kept on failure.
* **View Evidence**: the facts, the trace and screenshots.

## Email / SMTP (Settings, renamed from "Outlook Email")

Intranet SMTP host, port, STARTTLS / SSL / none, company CA file, certificate check, sign-in, timeout, allowed
recipient domains (or allow external), attachment limits, the developer directory, **Test connection** (no email
sent) and the Email Developer send history.

## Dashboard, Agent Workflow, voice

* Dashboard: Security findings by severity, Fuse results, ActiveMQ results, and analyses / defect drafts / PDF reports
  / emails / retests, from recorded data only; every slice opens the screen behind it. The existing widgets are kept.
* Agent Workflow: 19 agents registered (the five security agents, four Fuse, four ActiveMQ, AI bug analysis, evidence
  collection, TFS defect preparation, PDF reporting, Email Developer, AURA memory), each with category, inputs,
  outputs, dependencies and real availability. Five looks to choose from in Preferences (Classic, Mission Control,
  Command Center, Holographic, SkyGuard Core) and Stop / Pause / Resume on every agent card.
* Voice: "open security findings", "show critical vulnerabilities", "start a security scan" (opens the screen; scans
  start only from the page), "show failed fuse tests", "show broker health", "dead letter tests", "email history" ...
  in English and Arabic ("ابدأ فحص أمني", "اعرض الثغرات الحرجة", "افتح ريد هات فيوز", "ورني مراقبة الوسيط").
  Voice settings: **Test microphone** (passes only when real sound is recorded), **Test Arabic transcription**,
  **Test command interpretation** (shows what a command would do; nothing is executed).
* The working Arabic voice (Kareem / Hoda, `models\small.ar`) and the voice settings are not touched. Setup records
  them, verifies they are unchanged, and reports anything missing with the helper to run. It never downloads.

## Fixes found while building

* The dashboard no longer throws an error when it is left while its figures are loading.
* Checkbox labels in settings forms stay on one line.
* The email message no longer says "Expected behaviour: null" when a result has no expected text.
