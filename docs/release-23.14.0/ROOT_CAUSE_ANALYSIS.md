# Design notes and root causes: 23.14.0

23.14.0 is a feature release; the notes below record the decisions that matter for safety and the defects found while
building it.

## Decisions

* **One shared store for the three modules** (`qa_platform/itest`): targets, runs, results, cases, audit, run control
  (pause / resume / cancel), scope guard, throttle and sanitizer. The result actions, PDF, email and dashboard work the
  same for every module because they read the same tables.
* **Reuse, not duplication**: the API Testing `Policy` (scope), `trust_context` (company CA), JSONPath, the credential
  vault, the AI gateway, `print_pdf`, Defect Review / TFS publisher and the mailer are reused.
* **Deterministic permission checks**: test destinations, read-only Jolokia whitelist plus deny pattern, scope, rate
  and budget, authorization record, Production rules. The AI only drafts; drafts go through the same checks.
* **Browse, do not consume**: outputs are verified by browsing with the correlation ID so business consumers are not
  disturbed; when a consumer was faster, the result is UNVERIFIED rather than a guess.
* **Voice kept**: the patch never contains voice models; the installer now records the voice setup before the
  upgrade and fails the verification (and restores) if it changed.

## Defects found and fixed while building

| Symptom | Root cause | Fix |
|---|---|---|
| Open redirect not found when the page itself redirected | the crawler kept only the final URL and lost the query parameters | the requested URL is kept with the page and used for parameter tests |
| STOMP target refused when the same host was used on another port | the target check compared hosts only | exact host:port comparison everywhere |
| Artemis DLQ test never reached the DLQ | Artemis drops a message on STOMP NACK instead of redelivering | the test route stand-in reconnects to force redelivery; documented |
| A TTL test passed on Artemis before the message was removed | Artemis expires messages lazily | reported UNVERIFIED until the broker removed it |
| Invalid request bodies returned 500 | pydantic errors were not turned into 422 | `validate()` helper |
| Ids with `..` or `:` could reach file names | the id pattern was too broad | strict id pattern and report-kind check |
| Fuzz payload with a right-to-left override in the source (bandit B613) | literal character in a string | written as an escape |
| Dashboard error when leaving it while loading | a refresh started after the page was left | the refresh stops when the page is gone |
| "Expected behaviour: null" in emails | `None` turned into the text "null" | empty values are written as "see the test definition" / "not recorded" |
| Retest offered on passed results | the passed rule was not applied to Retest | passed results show evidence, PDF and email only |
