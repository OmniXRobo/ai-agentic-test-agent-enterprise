# TFS Hierarchy-Aware Intelligent Application Discovery: architecture analysis

This is the analysis done before changing code, as the requirement asks. It maps
every part of the requirement onto what already exists, and lists what is new.
Principle: extend, reuse, integrate. No second TFS client, credential store,
Playwright framework or agent registry is created.

## 1. Current architecture (inspected)

| # | Area | Where it lives today | State |
|---|---|---|---|
| 1 | Application Discovery | `qa_platform/automation/discovery.py` (`Discovery.crawl`), API `POST /api/automation/discoveries`, UI `web/js/pages/automation.js` (`discoveryPanel`) | Link-following BFS over `a[href]`. No login, no clicks, no pause. `login_steps` parameter exists but is unused. A page whose `goto` failed is still counted as a page. |
| 2 | TFS client | `qa_platform/tfs/client.py` (`AzureDevOpsClient`) | One REST client for Online and Server. `wiql`, `work_items(ids, expand_relations)`, `work_item` (with relations), iterations/areas, categories. |
| 3 | Hierarchy retrieval | `qa_platform/tfs/service.py` `detail()` | Reads one item's relations and names them with `RELATION_NAMES` (Parent, Child, Related, Tested By, Tests). No tree walk, no Epic/Feature support. |
| 4 | User Story retrieval | `TfsService.query(category="requirement")`, Script Studio `stories()` | Category-driven (`Microsoft.RequirementCategory`), so PBI/Requirement work too. |
| 5 | Acceptance Criteria | `Microsoft.VSTS.Common.AcceptanceCriteria` field, or linked work items whose type contains "acceptance" (`_traceability`). Studio `_criteria()` numbers them `AC<story>-<n>` or `AC<id>`. | Reused as the AC identity scheme. |
| 6 | Test Case retrieval | `detail()` → `traceability.test_cases` via Tested By; steps from `Microsoft.VSTS.TCM.Steps` parsed by `generator.parse_tfs_steps` | Reused. |
| 7 | Test Data | `design.service` `test_data` table linked by `tfs_test_case_id` | Reused. |
| 8 | Relationship APIs | work item `relations` (`System.LinkTypes.Hierarchy-Forward/Reverse`, `Microsoft.VSTS.Common.TestedBy-*`) | Reused; the hierarchy walk only follows these real links. |
| 9 | Application Credentials | `automation/store.py` `CredentialProfiles` (usernames in SQLite, passwords in Windows Credential Manager, namespace `appcred`), UI `app-credentials.js` | Reused for discovery login and for "save as static". |
| 10 | Application memory | `discoveries` table (one JSON result per run, keyed by base URL) + `_diff()` | Extended: per application, versioned, richer change detection. |
| 11 | Playwright launcher | `automation/browser.py` (`start_playwright`, `launch`, bundled `runtime/ms-playwright`) | Reused unchanged (portable, no admin). |
| 12 | Page Object framework | `automation/framework/` (builder, registry, codegen, template `page_objects/`) | Reused; discovery now proposes page names the builder can reuse. |
| 13 | Flow framework | template `flows/`, `registry.scan()` finds flows | Reused. |
| 14 | Self-Healing Locator Agent | `automation/healing.py`, `runner._heal`, framework `utilities/locators.py` | Reused; benefits from alternates recorded by discovery. |
| 15 | Visual cursor / highlighter | `automation/js/overlay.js` (`__qaOverlay.point/click/clear/status`) | Reused for Show Browser discovery. |
| 16 | Evidence storage | `paths.evidence/<run_id>`, `GET /api/automation/evidence` | Reused for discovery screenshots. |
| 17 | Result engine | `execution_results`, `runs`, framework runner progress.jsonl | Unchanged. |
| 18 | Agent Workflow | `BaseAgent.activity()` → `agent.started/progress/completed/failed`; SSE | Reused. Waiting for input is published as progress with `state=WAITING_FOR_INPUT`; the card stays RUNNING (not PASSED) until the job ends. |

Already delivered by PR #14 (Script Studio) and not rebuilt: Play with a
Show Browser / Background choice, Play Selected, Play Again, Stop, one script for
both modes, step screenshots in both modes, POM + flows + tests + data layers,
traceability header comments, credentials outside the source, runtime date/time,
validation before GENERATED, failure diagnosis, self-healing.

## 2. TFS hierarchy model

Nodes are real work items. Levels come from the project's process, not from
names:

* **Requirement parent types**: work item types that have Child links to
  requirement types (Epic, Feature by default; configurable).
* **Requirement types**: `Microsoft.RequirementCategory` (User Story, Product
  Backlog Item, Requirement).
* **Test types**: `Microsoft.TestCaseCategory`.
* **Acceptance Criteria**: linked AC work items when the process has them, else
  the AC field split into `AC<story>-<n>` items (same ids Script Studio uses).

Edges used: `Hierarchy-Forward/Reverse` (parent/child), `TestedBy-Forward/Reverse`
(story ↔ test case). AC → test case mapping uses a real link when an AC is a
work item; for field-based AC it uses the AC ids the generated test declares, or
"traceable to story only". Nothing is inferred from title similarity.

Selection normalisation: every selected node expands to its descendants unless
the tester excluded them; child-only selections keep the ancestor chain as
`context` (not in active scope); the final scope is a set of work item ids, so
duplicates (F100 + US118 + TC1268 under F100) are processed once and loaded with
one batched `work_items` call per level.

## 3. New components

| Component | File | Notes |
|---|---|---|
| Authenticated, interactive explorer | `automation/discovery.py` (extended) | Login detection, credential fill, input pause/resume in the same browser context, CAPTCHA pause, safe click exploration for SPAs, destructive-action guard, visual cursor/highlight. |
| Discovery jobs | `automation/discovery.py` `DiscoveryJob` | In-memory job with state machine (`IDLE … COMPLETED/FAILED/STOPPED`), pending input request, stop. |
| Application memory | `discoveries` table + new columns | `application`, `version`, `knowledge_source`, `state`; diff per application. |
| TFS hierarchy service | `tfs/hierarchy.py` | Lazy tree (children on expand), session cache, scope normalisation, counts. Uses `AzureDevOpsClient` only. |
| Requirements Intelligence | `discovery/requirements.py` (agent `requirements`) | Builds Requirement Memory (stories, AC, test cases, steps, expected results, test data, expected pages/fields). |
| Requirement ↔ application mapping | `discovery/mapping.py` | Deterministic token matching of steps/AC to discovered pages and elements, optional On-Premise AI refinement; coverage %, requirement gaps, application gaps, out-of-scope discoveries. |
| Unified QA Knowledge | `qa_knowledge` table | Requirement memory + application memory + mapping, read by Script Studio generation. |

## 4. Schema changes

Migration 10 (additive only):

* `discoveries`: `application TEXT`, `version INTEGER`, `knowledge_source TEXT`, `state TEXT`.
* `requirement_memory (id, project, scope_key, created_at, scope, memory)`.
* `qa_knowledge (id, application, run_id, discovery_id, requirement_memory_id, created_at, knowledge)`.

Existing rows keep working (`application` null → treated as the base URL host).

## 5. UI changes (Application Discovery & Update page)

* Existing URL / Max pages / Link depth / Discover controls kept.
* New: Discovery Knowledge Source (Application Only, TFS Requirements Only, TFS + Application, recommended).
* New: Authentication (application + role from Application Credentials, "requires login").
* New: Show Browser / Background choice before start.
* New: TFS Requirement Scope (project, sprint, scope option, lazy hierarchy tree with search, counts, preview, large-scope warning, selection summary).
* New: input window when the agent waits (masked for secrets, "save if reusable/static", Proceed, Cancel Discovery).
* New: result summary, coverage map, gaps, versions.

## 6. Backend / agent changes

* `Discovery.crawl` keeps its signature (existing callers in Script Studio, AI generation and performance still work) and gains keyword options.
* Application Discovery Agent emits WAITING_FOR_INPUT / RESUMING progress.
* Requirements Intelligence Agent runs first in TFS modes; QA Orchestration Agent maps requirements.
* Script Studio `_inventory` prefers the application's latest authenticated memory and adds requirement context to the prompt.

## 7. Regression risks and mitigations

| Risk | Mitigation |
|---|---|
| Discovery callers (`studio._inventory`, `automation.generate`, performance from-discovery) | `crawl()` signature and result shape kept; new keys only added. Existing tests unchanged. |
| Click exploration changes application state | Destructive-action guard; only links/buttons outside forms; logout/sign out never clicked; budget per page. |
| Secrets in logs/events | Values typed by the tester are never put in events or results; bus redaction still applies; password inputs are masked in the UI. |
| Headless pause with no visible browser for CAPTCHA | The agent pauses and says the verification needs Show Browser; the tester can cancel. |
| TFS load on Entire Project | Large-scope warning, lazy children, batched reads, per-job cache, Refresh invalidates. |
| Offline Windows package | No new dependencies; paths from `Paths`; no admin. |

## 8. Implementation sequence

1. PR A: authenticated + interactive discovery, input pause/resume, visual mode, application memory versions and change detection, discovery job API and UI.
2. PR B: TFS hierarchy service, scope selector, Requirements Intelligence → Requirement Memory, Intelligent mode mapping, coverage and gaps, Unified QA Knowledge used by Script Studio.
3. PR C (if still needed after B): OTP/MFA pause during Play.

## 9. Test plan

* Local test site with a login form, an OTP step, a CAPTCHA marker, a SPA with `href="#"` navigation, a burger menu, a cart and a destructive button (tests/site/shop).
* Unit tests: login detection, field classification, destructive guard, memory diff, hierarchy normalisation and dedupe (fake Azure DevOps with Epic → Feature → Story → Test Case links), mapping and coverage.
* End-to-end (Playwright headless in CI): Application Only with saved credentials; missing credentials → WAITING_FOR_INPUT → Proceed resumes same session; OTP not saved; static value saved through Application Credentials; TFS + Application mapping produces coverage and both gap lists.
* Full existing suite must stay green on Windows and Ubuntu CI.
