# What is new in SkyGuard QA 23.12.0

23.12.0 is one cumulative upgrade from SGQA-23.3.35. It contains every release up to 23.11.2 and adds the items below.

## API Testing (new, Automation Studio > API Testing)

* **Applications.** Name, environment (Development, Test, Staging, Production), a complete base URL or protocol +
  host name / IPv4 / IPv6 + port + base path, default headers, timeout, retries (only for requests that change
  nothing), rate limit, response size limit, response-time target, proxy, TLS verification and certificates.
  Creating one needs the tick "I am authorized to test this API". Only its **authorized targets** are contacted.
* **Authentication.** None, Basic, Bearer token, API key (header or query), OAuth 2.0 (client credentials or
  password grant, token refreshed once on 401), client certificate, session sign-in (cookie) and custom headers, plus
  roles for authorization tests. Secrets go to the Windows Credential Manager; the page only shows "stored".
* **Test Connection** shows each real step: authorized target, name resolution, TCP connection, TLS certificate and the
  HTTP answer. A 404 at the base path counts as reachable: no health endpoint is assumed.
* **Imports, all offline and never executed.** OpenAPI 3.x and Swagger 2.0, JSON or YAML, one file or several that
  refer to each other, or an intranet URL that is an authorized target. References to other web sites are listed as
  unavailable, never downloaded. Also cURL commands, Postman collections, Python requests/httpx scripts (literal URLs
  are read, nothing runs) and SkyGuard's own export.
* **Generated tests** with the documented status codes and response schemas: positive, missing required field, wrong
  type, invalid / out-of-range values, missing query parameter, not-a-number parameters, without credentials,
  refused for a role, boundary values (minimum, maximum, lengths), pagination, filtering and sorting, response time and
  content type. When the specification documents no result, the test is **Needs review** and does not run until a
  person approves its expected result. The local AI can suggest tests from a sentence; they are always Needs review.
* **Editor** for each test (method, path, parameters, headers, body, assertions, extracted variables, data rows) with
  Save | Validate | Run | Generate Script | Schedule, and versions with Restore.
* **Runs**: one test, selected tests, all tests or a suite; sequential or up to 4 at the same time; data-driven (one
  result per row); **chains** that pass extracted values (`{{orderId}}`) to the next test, where a step whose input is
  missing is BLOCKED. Counts: total, passed, failed, skipped, blocked. Tests that change data run only after you
  confirm them; on Production they are off by default and never run from a schedule.
* **Response viewer**: status, time, size, request and response headers (secrets as [redacted]), JSON tree or raw body,
  every assertion with its message, extracted variables, redirects, Rerun. HTML report per run.
* **Python script**: one pytest + httpx file for all tests, selected tests or a suite, with the tabs View Script, Edit
  Script, Flow Logic, Test Data, Credentials, Components, History, Result, Evidence and Logs. Secrets are read from
  environment variables. Validate, Save (new version), Restore, Run (SkyGuard runs it with pytest and passes the stored
  secrets for that run only), Download.
* **Failures** are classified (application defect, API contract, automation script, test data, authentication,
  environment, performance, inconclusive) with evidence and become **Defect Review drafts** (Needs review). The same
  failure again adds an occurrence instead of a new draft. Nothing is sent to TFS until a person approves; then the
  existing TFS publisher creates the work item and keeps its ID and link.
* **Audit**: every configuration change, secret change (never the value), import, run and script save.

## Scheduled Runs

* API tests (one application's tests, a suite, a single test, the latest Python script) and saved Integration
  Comparisons are listed with the other scripts. Filter **Page**: API Testing or Integration Comparison.
* For browser tests, **Execution mode**: Visible, Headless, or Smart Auto (a scheduled browser test runs headless in
  the background, and the reason is shown on each result). API items always run as Authorized API/HTTP and say so;
  a browser test is never replaced by API calls.
* The time field shows this PC's time zone. Each result shows its counts and a link to the run.
* Notifications: scheduled run started, API run passed or failed, API connection failed, defect draft approved,
  and the existing completed / failed / draft / TFS notifications.

## Integration Comparison

* Every confirmed mismatch of a run (result FAIL) becomes a Defect Review draft (up to 25 per run) with the
  applications, record key, expected and actual values, rule and evidence. The page says how many were made.
* Saved comparisons can be scheduled (button **Schedule…**). A comparison that could not conclude is reported as
  such, never as a pass.

## Agent Workflow (same page, same cards, same layout)

* Six agents were added: AI Application Learning & Operations, Integration Comparison, API Testing, API Failure
  Analysis, API Contract Validation and Scheduled Execution. Each card reports real work (RUNNING, COMPLETED,
  FAILED, PAUSED).
* A card shows **UNAVAILABLE** with the reason when its engine is not available on this PC (for example the local
  AI is offline, or Playwright is not installed), instead of READY.
* Defect Review & TFS is the existing Defect Intelligence agent; no second card was added for it.

## Python editor (Guided Browser Automation and API Testing)

* **Full screen** (Esc to leave), **Taller / Shorter** and a resizable corner; the height is remembered on this PC.
* Lines with an error are marked in red in the line numbers, with the message on hover.

## Setup

* The wizard's footer shows only "SkyGuard QA 23.12.0". The words "offline, no administrator rights" were removed,
  as asked. The upgrade itself is still offline and per user.

## Fixed after the first delivery (reported on the first 23.12.0 package)

* **Setup publisher.** Setup.exe names **Muhammad Tufail** as its publisher: on the Welcome page and in the file's
  properties (Company, Copyright). Windows SmartScreen keeps saying *Unknown publisher* until Setup.exe is signed with
  a code-signing certificate; the build signs it when a certificate is given (INSTALLATION_GUIDE.md).
* **AI Agent: "Finish lab" on SauceDemo was refused** with *not authorized: The signed-in identity has not been
  verified from the application*. The sign-in had worked. Fixed; an application that has kept the wrong learned rule
  works again without forgetting anything (ROOT_CAUSE_ANALYSIS.md).
* **AI Agent, easier to read.** After you save a password the request continues by itself without an extra "again"
  message in the chat. Tasks seen once are shown as *Learned* (they can be used, with the preview first) instead of
  *Uncertain*. A task that ended without doing its records shows a *Why* line with the reason next to the result.
  Records that have not run yet show *Waiting*.
* **Integration Comparison: Apply filters** failed with *InvalidSelectorError* on
  `button[name=/(?i)^(apply|search|filter|go|show)/i]`. Fixed; a table that filters as you type (no Apply button) is
  recognised and logged as such.
* **Integration Comparison: live capture INCOMPLETE** with *page 6 repeats an earlier page (Next did not move on)*
  on tables whose Next button stays active on the last page (the tabulizer.com sorting / pagination demo). The last
  page is now confirmed from the application's own record count or its last page number; only when the application
  shows neither does the capture stay INCOMPLETE, with that reason.

