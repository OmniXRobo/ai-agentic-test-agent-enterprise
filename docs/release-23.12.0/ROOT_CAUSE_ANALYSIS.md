# Root cause analysis: what 23.12.0 changes and why

| Reported or requested | What was found in 23.11.2 | Cause | Change in 23.12.0 |
|---|---|---|---|
| Remove "offline, no administrator rights" from the Setup window (screenshot) | The footer of every wizard page read "SkyGuard QA 23.11.2  offline, no administrator rights". | `BrandingText` in `packaging/setup/SkyGuardSetup.nsi` carried the phrase. | `BrandingText "SkyGuard QA ${VERSION}"`. The version label is kept; the Welcome text still explains the upgrade. |
| Agent Workflow cards said READY although the engine could not work | A card was READY whenever no run was active, even with the local AI offline or Playwright missing. | `/api/agents` returned only the event state; nothing checked the agent's dependencies. | `api/core.py` `_availability` adds `available` and `unavailable_reason`; the card shows UNAVAILABLE with the reason. The page layout is unchanged. |
| Missing agents on Agent Workflow | The AI Agent (operations), Integration Comparison and scheduling did their work without a card; API Testing did not exist. | Those services never emitted agent lifecycle events and were not in `agents/registry.py`. | Six agents registered once each, and each service emits started / progress / completed / failed / paused through `BaseAgent.activity` (the operations engine through `_CardReporter`). |
| Integration Comparison in Scheduled Runs, and drafts for differences | A saved comparison could only be run by pressing RUN COMPARISON; a mismatch became a defect only when a person pressed Create defect. | The scheduler only knew AI-driven, Guided and Hybrid scripts. | A provider interface on the scheduler (`providers`), `ComparisonScheduleProvider` (`CMP:<id>`), and `comparison/schedule.py` turns every confirmed mismatch into a review draft (at most 25 per run). |
| API Testing module | None. | New. | `qa_platform/apitesting/*`, `api/apitesting.py`, `web/js/pages/api-testing.js`, migration 21, `ApiScheduleProvider`. |
| Execution mode shown with its reason | Scheduled Runs offered "Visible / Hidden" only; results did not say how they ran. | — | Visible / Headless / Smart Auto; each result keeps `mode` and `mode_reason`. API items always run as Authorized API/HTTP and say so; a browser test is never replaced by API calls. |
| Larger Python editor | Fixed maximum height of 620 px, no full screen, errors only listed above the code. | CSS `max-height` and no resize. | Resizable height (remembered), Taller / Shorter, Full screen, error lines marked in the gutter. |
| A per-test timeout in API runs (found while testing) | A test's own timeout was ignored, and the next tests of the run kept the changed configuration. | The client was created once per run with the application timeout. | The timeout is passed per request. |
| `{{column}}` in an assertion (found while testing) | Data-row values were filled into the request but not into the expected values. | Assertions were evaluated as written. | Expected values are filled from the row and extracted variables before they are checked. |
| HTTPS test CA rejected (found while testing) | "CA cert does not include key usage extension". | Python 3.13 verifies certificates strictly; the test CA lacked key usage. | The test CA now has the extensions a real CA has. The product behaviour (refuse) is kept and documented. |
| Upgrade refused at Verify by the 23.11.0 check (found in the first Setup run under Wine) | "Python editor, Smart Wait, Defect Review (23.11.0) FAIL"; Setup rolled back to 23.11.2 and nothing was lost. | The 23.11.0 check counted exactly 6 failure classes; 23.12.0 adds 3 for API failures. | The check now requires the six 23.11.0 classes to be present. Setup was run again: every check passed. |

## The AI Agent (part A of the request)

The SauceDemo flows (login, add to cart, checkout; missing information; previews; paused / uncertain runs) were
repaired in 23.11.1 and 23.11.2, which this upgrade contains. 23.12.0 adds the AI Agent's card on Agent Workflow
with the real end state of each run (a run that stops for a decision is shown as PAUSED with the reason). No new
root cause was found in the agent itself in this release. saucedemo.com could not be reached from the build machine
(the network refused it, HTTP 403), so the flows were checked on the local SauceDemo stand-in (`tests/site/saucedemo`)
and the stand-in shop (`tests/shop_site.py`); see TEST_REPORT.md.
