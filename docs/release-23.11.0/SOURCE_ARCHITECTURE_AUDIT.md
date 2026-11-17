# Source and architecture audit (23.10.0 → 23.11.0)

## Source available

* The full git history from before 23.3.35 to 23.10.0 (`8c8dcc4`) is in the repository. Every release in
  RELEASE_INCLUSION_MANIFEST.md was extracted with `git archive` from its own commit and used as an installation
  baseline in the installer tests. No source was missing, and no source was reconstructed.
* The 23.10.0 baseline results (TEST_REPORT of 23.10.0) were:
  * Linux: 868 passed, 0 failed;
  * Windows CI: 895 passed, 0 failed.

## What existed before 23.11.0, and what was changed

| Area | Found in 23.10.0 | Decision in 23.11.0 |
|---|---|---|
| Python of a recorded script | Generated from the steps on every view and download (`pytest_export.py`). It could not be edited in SkyGuard. Editing it outside meant the next export lost the edits. | `PythonScripts` keeps saved versions in the database (`script_python`, `script_python_state`). The saved version runs and downloads. Generation is unchanged and is used until the first save, after Revert, or after Regenerate. |
| Running edited code | Not possible. | A pytest subprocess in the run's own evidence folder, with the application's Python, a timeout, secrets passed by environment and masked in the output, and JUnit results. The code is first checked by a static policy (allowlisted imports, no `eval`, `exec`, process, deletion or dunder calls). **This policy is a guard against mistakes, not a sandbox.** Only Admin and Tester roles can save or run, and the run has the same rights as the Windows user. |
| Waiting | Fixed `wait` steps, Playwright auto-waits, and `networkidle` on navigation. There was no shared notion of "the page is busy". | One `SmartWait` per browser context, built on Playwright request events (read-only listeners) and one page probe (readyState, `jQuery.active`, `aria-busy`, loader selectors, MutationObserver quiet time, target state). Used by the recorder, the runner and the exported Python, where the same source is embedded. Playwright is not monkey-patched. |
| Defect drafts | Several sources made drafts with different evidence. The "automatic" mode created TFS bugs without a review. Publishing assumed success when the HTTP call returned. | One `DefectService` for all sources. Eight statuses, an event history, approval roles, a validate-only call, attachment checks, a correlation-ID tag, reconciliation after a lost response, read-back before "Published", and retry. Automatic mode only prepares drafts. |
| Integration Comparison → TFS | `create_in_tfs` read `exc.code` from `QAError` (the attribute is `error_code`). Any TFS refusal raised `AttributeError`, which the API reports as "Unexpected Server Error". | Fixed everywhere it occurred (comparison service, filters, workspace). The comparison now uses the shared publisher with full evidence, and only offers defects for confirmed mismatches. |
| Failure classification | `analysis.py` described failures, but there was no class or confidence. | `triage.py`: rule-based classification into six classes with confidence and evidence. An optional local Qwen refinement receives only the redacted facts, must answer one of the fixed classes and recommendations (anything else is discarded and the rules stand), and its reasons are shown labelled "AI:" next to the rules' own view, which is kept. SkyGuard cannot prove the AI's reasons are right; the reviewer decides. A suggested script change is never applied without the person. |
| Installer | 23.3.35 to 23.9.0, 19 migrations. | 23.3.35 to 23.10.0, 20 migrations, two new verification checks. The Setup wizard, backup, automatic restore and manual install are unchanged. |

## Components touched

```
qa_platform/automation/python_scripts.py (new)   versions, validation policy, format, run, failure drafts
qa_platform/automation/smart_wait.py     (new)   SmartWait, policy, bulk insert/update/remove, embedded source
qa_platform/automation/recorder.py               records AJAX/Data Load waits it observed
qa_platform/automation/runner.py                 policy waits, wait_until steps, trace on failure, busy-page facts
qa_platform/automation/pytest_export.py          embeds Smart Wait; export/bundle use the saved Python
qa_platform/automation/service.py                Run / data rows / scheduled / bulk use the saved Python
qa_platform/defects/service.py, triage.py (new), analysis.py   one review and publishing service
qa_platform/comparison/service.py                review_draft, create_in_tfs through the publisher
qa_platform/storage/db.py                        migration 20
web/js/python-editor.js (new), smart-wait-ui.js (new), pages/defect-review.js, pages/automation.js
```

## Rules kept

* No credentials in prompts, learning memory, scripts, screenshots or logs.
  * Exported and edited Python reads passwords from environment variables.
  * Run output is masked.
  * A Playwright trace is kept only when no secret was typed.
  * Draft evidence is redacted.
* MFA, CAPTCHA and application permissions are never bypassed.
* No machine-wide changes: no machine registry keys, system PATH, services or protected folders.
* Qwen proposes. It does not execute. Its classification is shown beside the rules' view and changes nothing by itself.
