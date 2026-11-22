# User guide: SkyGuard QA 23.14.1 (what is new in 23.14.0, and the 23.14.1 TFS speed fix)

> **23.14.1** changes nothing you need to learn: TFS pages, Connection Test and the Dashboard are quicker, and the
> Dashboard fills in slow TFS figures by itself ("TFS figures loading…"). The guide below (23.14.0) still applies.

Press Ctrl+F5 once after the upgrade. Everything below works offline on the office intranet.

## 1. Before any testing: record the authorization

Every Security application, Fuse connection and ActiveMQ broker has an **Authorization to test** box. Tick it and write
who approved testing, the scope and the period, and a ticket or email reference. Without it no run starts. The record
(and who saved it) is shown on every screen and in every report.

## 2. Security Testing

1. **AI Security Assessment** > New application: name, environment (QA, Test, UAT, Staging, Production), base URL,
   allowed and excluded paths, roles and test accounts (save each password in the credential boxes; they go to the
   Windows Credential Manager), intensity, and the categories you are allowed to test actively.
2. **Application Discovery** > **Run read-only discovery**: SkyGuard opens each page as each role (read-only). Open the map, check it and
   press **Approve for active testing**.
3. **Vulnerability Scanning**: **Passive checks** (looks only), **Vulnerability scan** (the approved categories) or
   **OWASP ZAP scan** (needs your own ZAP on this PC, set in Security Preferences). Watch the results arrive; Pause,
   Resume or Cancel at any time.
4. **Security Test Scenarios**: **Draft scenarios with AI** writes scenarios from the approved map; edit, approve and **Run
   approved scenarios**.
5. **Security Findings**: filter by status and severity; mark each finding confirmed, false positive, accepted risk or
   fixed (with a note).
6. **Security Regression** re-checks open findings after a fix. **Security Reports** makes the PDF.

## 3. Red Hat Fuse Testing (Integration Testing)

1. **Fuse Connections**: runtime (Karaf, EAP, Spring Boot, or black box), the Jolokia or actuator URL and user (password
   in the credential box), the linked ActiveMQ broker, test URLs for HTTP inputs. **Test connection** shows the
   detected runtime and version.
2. **Route Discovery** lists contexts and routes; **Route Mapping** shows a route's inputs, outputs, choices,
   transformations and error handling (read from the route definition, nothing is changed).
3. **AI Test Designer**: **Draft tests with AI** from the mapped routes or write them (input, message, expected outputs with
   comparison rules, failure expectations); approve them.
4. **Test Execution** runs the approved tests. Each run has a correlation ID; SkyGuard sends the input, finds the output
   on each expected queue by that ID, compares it, checks the DLQ and the route statistics.
5. **Route Monitoring** shows route health. **Transformation Comparison** compares any two messages with rules.
6. **Results & Defects**: every result with the result actions (section 5).

## 4. ActiveMQ Testing (Integration Testing)

1. **Broker Connections**: Classic or Artemis, management (Jolokia) URL and user, STOMP host / port / TLS, the **test
   destinations** you may publish to (for example `SKYGUARD.TEST.*`), the consume destinations (a part of them used for
   consumer tests), the DLQ name, limits. DLQ, ExpiryQueue and system destinations are refused.
2. **Queue & Topic Discovery**: destinations with depth, consumers and counts. **Browse** shows messages without taking
   them off the queue.
3. **Message Test Designer**: publish and verify, consumer, retry and DLQ, TTL, priority, selector, durable,
   performance, security. **Draft tests with AI** or write; approve; **Run approved tests**.
4. **Publish & Verify**, **Consumer Verification**, **Retry & Dead-Letter Testing** run the approved tests of that kind.
5. **Broker Monitoring**: depth, consumers, DLQ growth and alerts. **Results & Defects** as for Fuse.

## 5. The result actions (everywhere)

| Button | What it does |
|---|---|
| Analyze with AI | The local AI explains the failure. **Verified facts** (what was recorded) are shown apart from **hypotheses**. History and Reanalyze. |
| Create TFS Defect | An editable preview of the defect (title, steps, expected / actual, severity, attachments); nothing goes to TFS until you confirm. |
| Generate PDF Report | An offline PDF of this result, run or finding. |
| Email Developer | A ready email: pick people from the developer directory or type addresses, edit subject and message, choose attachments, press Send and confirm. The history shows "accepted by the SMTP server" or the error; a failed email stays as a draft. |
| View Evidence | Facts, the trace and screenshots. |
| Retest | Runs the same test again. |

A passed result shows View Evidence, Generate PDF Report and Email Developer only.

## 6. Email / SMTP (Settings)

Choose "Company Exchange / other SMTP server", enter server, port and security, the sign-in user and password (kept in
the Credential Manager), the From address, and in **Intranet SMTP and safety rules**: verify the certificate, the
company CA file, timeout, allowed recipient domains, attachment limits and the developer directory (one line per
person: `name, email, team, fuse|activemq`). **Test connection** checks the server without sending; **Send test email**
sends one. The **send history** is below.

## 7. Voice

Say or type, in English or Arabic: "open security findings", "show critical vulnerabilities", "start a security scan"
(opens Vulnerability Scanning; you press Start), "show failed fuse tests", "route monitoring", "show broker health",
"dead letter tests", "show queues", "email history" ... Arabic: "افتح الثغرات الأمنية", "ابدأ فحص أمني", "افتح ريد هات
فيوز", "اعرض الاختبارات الفاشلة في فيوز", "ورني مراقبة الوسيط", "افتح إعدادات البريد".

**Settings > Voice & Command Agent > Voice tests**: Test microphone (passes only when real sound is heard), Test Arabic
transcription (read the sentence shown), Test command interpretation (shows what a command would do; nothing runs).

## 8. Agent Workflow

The 19 new agents are listed with their category, inputs, outputs, dependencies, availability and last run. Choose the
look in **Preferences** (Classic, Mission Control, Command Center, Holographic, SkyGuard Core). Stop, Pause and Resume
are on every agent card.
