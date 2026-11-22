# Changelog: SkyGuard QA 23.14.1

Cumulative on 23.14.0 (commit `18d9fde`). One upgrade for every release from 23.3.35 to 23.14.0. Everything works
offline. No database change (still **23 migrations**), no new dependency, the browser extension stays 1.4.3, and the
Arabic voice, speech model and voice settings are kept exactly as installed (Setup checks this; it never downloads
them). Earlier changes: `previous-23.14.0/`, `previous-23.13.3/` ...

## TFS is fast again (every TFS page)

Reported on 23.14.0: Connection Test slow (but passing), the Dashboard on "Loading..." and every TFS page slow for an
account with its own PAT and project. Root cause and measurements: `ROOT_CAUSE_ANALYSIS.md`.

* **Connections are reused.** One open HTTPS connection pool per server, project and PAT, instead of a new connection,
  TLS handshake and certificate store read on every TFS call. Affects every page that reads TFS: Dashboard, Work Items,
  User Stories, Acceptance Criteria, Test Plans, Test Suites, Test Cases, Test Design, Test Data, Defect Review
  ("Assign to"), TFS Scope / hierarchy, Script Studio, Intelligent Discovery, voice commands and reports.
* **Your PAT and project are read once**, not on every call (the Dashboard made 70 Windows Credential Manager reads; it
  now makes none once warm). A change (Save on TFS Connection, the Users page, Remove saved PAT) is used at once.
* **No duplicate TFS queries.** When two requests need the same TFS figure at the same moment (the Dashboard sends two,
  the status strip a third), TFS is asked once and both get the answer.
* **An unreachable server fails in 10 s**, not 30 s.

## Dashboard

* Shows everything at once and **never waits more than 8 s for TFS**. When TFS is slower the TFS figures read
  "TFS figures loading…" and fill in by themselves when TFS answers (the page asks again every 5 s).
* The TFS counts (user stories, test cases, open defects, all defects) are asked for at the same time, and so are the
  project's work item types, categories, iterations and areas.
* The Security / Fuse / ActiveMQ figures are requested together with the other figures, not after them.

## Connection Test

* Same six checks and results, quicker: reachability and authentication come from one call (the organization's
  sign-in page is no longer downloaded), and the collection, project, work item and test plan checks run together.

## Several users, several projects

* Background work runs **as the person who started it**, with their own PAT and project: test design, test case
  generation and publishing, test data generation, Script Studio generation and runs, voice commands, reports, defect
  analysis and agent runs. Before, this work used the administrator's PAT and project.
* The TFS light in the status strip is kept per person and project.
* 100 request threads instead of 40, so slow TFS calls no longer hold up pages that do not use TFS.

Not in this patch (from the Multi-User Isolation plan, available on request): requiring a personal PAT whenever a
personal project is set, owners and projects on scripts / runs / schedules, and one test design run per user.
