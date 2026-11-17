# Final release report: SkyGuard QA 23.11.1 (AI Agent repair) and extension 1.3.0

## Release number

The request was titled "23.10.0". The repository already held 23.11.0, built on 23.10.0. As agreed, this repair is
**23.11.1 on top of 23.11.0**: nothing of 23.11.0 is lost, and it installs on any release from 23.3.35 to 23.11.0,
23.10.0 included. The zips keep the names the request gave them.

## What you get

| File | What it is |
|---|---|
| `SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch.zip` | The upgrade to 23.11.1. Contents: `SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe` (Welcome → Detect → Backup → Install → Verify → Finish), `Manual_Install` (the same upgrade without the .exe, with `APPLY_PATCH.cmd`, `VERIFY_PATCH.cmd`, `ROLLBACK_PATCH.cmd`), `README_FIRST.txt`, the documents, the SBOM, test results, evidence screenshots and `SHA256SUMS.txt`. |
| `SkyGuard_SGQA-23.10.0_Edge_Chrome_Extension.zip` | Extension and companion 1.3.0: `extension\` (manifest 1.3.0), `INSTALL.cmd` for new installs, `UPDATE_EXTENSION.cmd` for installed ones, offline runtimes, `docs\COMPATIBILITY.md`, `docs\TEST_REPORT.md`, `docs\CHANGELOG.md`, `SHA256_CHECKSUM.txt`. |
| `.sha256` files | SHA-256 of each zip. |

No administrator rights or internet are needed. Your data, and what the agent already learned, are backed up first and
kept. A failed step restores the previous version automatically, and `ROLLBACK_PATCH.cmd` undoes the upgrade later.

## The four reported faults

| Fault | Root cause | Fixed by | Shown by |
|---|---|---|---|
| "navigate to online store" became a *Cancel* preview | Every command went to task matching, and the model had to pick one of the tasks. The shop's *Cancel* button had been learned as a task. | Intent first: navigation opens a page and never a plan. The model sees only compatible tasks and may answer "none". Dismiss buttons are no longer learned as tasks. | Test A (with and without a model that picks "cancel"), UI test |
| A stale or unrelated workflow | A pending question was glued to every later message, without expiry. | Kept only for the same application, 30 minutes, and only for answers. A plan records its request and application. | Test A (stale question), Test E (plan from another request) |
| "Tick the confirmation box first" / "Every record is blocked" | The page decided about the checkbox, and refreshes cleared it. The preview did not check the records' current status. | The tick survives refreshes. The server checks the acknowledgement, the application and the plan. The preview checks each record's status and says why nothing can run. | Test E, UI tests (ticked through refreshes; nothing to run gives no Confirm and a 409) |
| Saved learning not used | Learning covered only single-page forms; record-page actions and multi-step chains were missed. Nothing checked that a saved task could be found again. | Multi-step and record-page learning; *Test learned knowledge* routes every task and page through the command router, also on Stop & save. | Tests B, C, F, G |

The details are in ROOT_CAUSE_ANALYSIS.md.

## The rest of the request

| Requirement | Status |
|---|---|
| Suggestions from learned knowledge; no fixed examples, no permanent password warning | **Done and tested** (SkyGuard and the extension) |
| Learning the full Create → Approve → Authorize → Driver authenticate → Record process with role accounts in Credential Manager | **Done and tested** on the stand-in transport application. The driver's code is a step the person types. The tests use the vault's in-memory store; Windows Credential Manager itself is unchanged from earlier releases and was not run here. |
| Per-application knowledge, *Test learned knowledge*, *Test learned workflow* | **Done.** The retrieval check behind *Test learned knowledge* is tested (companion and Stop & save). The *Test learned workflow* button only prepares a preview in the chat and was not clicked in a browser test. |
| Plain-language execution with the short preview (Action, Application, values, Role, Status; Confirm & Execute, Edit, Cancel; Missing information) and the application's own record number | **Done and tested**. A vehicle is chosen only when you say "any available"; otherwise it is asked. |
| Extension: Start/Stop learning, reconnect, duplicates, least privilege | **Done and tested** (test G) |
| Security: page text cannot command the agent, no cross-application execution, audit | **Done and tested** (test H; SECURITY_REPORT.md) |
| Installer, backup, rollback, migrations | **Done**: 12 of 12 earlier releases upgraded, verified, rolled back and upgraded again; Setup walked through under Wine over a real 23.11.0; Windows CI on the pull request |

## Test results

* Full suite on Linux: **975 passed, 0 failed**, 96 skipped; the page regression test: 1 passed.
* Tests A to H: 41 passed. Extension 1.3.0: 5 passed. AI Agent page in a browser: 5 passed. Installer: 24 passed.
* See TEST_REPORT.md for how each was run, and the one failure found on the way (an intended behaviour change).

## Not verified

* **Your own Online Store and transport system** (stand-ins were used). **Before relying on a task:** open
  *Applications & Knowledge*, press *Test learned knowledge*, then run the task once on a test environment.
* Microsoft Edge and Google Chrome on Windows (Chromium on Linux was used for the extension).
* A live local Qwen.
* Windows 11 itself (Windows Server runners and Wine were used).

## Behaviour changes to know about

* The agent no longer picks a vehicle or similar resource by itself. Say "any available vehicle" if you want it to.
* A navigation request never prepares a task, even when the model suggests one.
* One task per command: "create a trip, then submit it" is two commands (KNOWN_LIMITATIONS.md).
