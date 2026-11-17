# Source and architecture audit (23.11.0 → 23.11.1)

## Which release this patch is built on (§17 of the request)

* The request says "23.10.0". The repository was checked before building. It already holds **23.11.0** (`292743c`),
  which was built on 23.10.0 (`8c8dcc4`) earlier the same day, in the same pull request (#55).
* Building a separate "23.10.0 repair" would have meant either reusing the number 23.10.0 for different code, or
  leaving out what 23.11.0 added. So this repair is **23.11.1, built on 23.11.0**. It upgrades any release from
  23.3.35 to 23.11.0, including 23.10.0.
* Nothing in 23.11.0 was overwritten. All of 23.11.0's own tests still run, and their results are in TEST_REPORT.md.
* The zip names in the request are kept.
* No 23.3.35-specific assumption was added. 23.3.35 remains only the oldest version the cumulative installer accepts,
  as before.

## Source available

* The full history from before 23.3.35 to 23.11.0 is in the repository. Every release in
  RELEASE_INCLUSION_MANIFEST.md is extracted with `git archive` from its own commit and installed as a baseline in the
  installer tests.
* No source was missing. No source was reconstructed.

## What existed before 23.11.1, and what changed

| Area | Found in 23.11.0 (same as 23.10.0) | Decision in 23.11.1 |
|---|---|---|
| Command understanding | Every command went to task matching (`planner.match_workflow`, bag of words), then to the model, whose prompt forced a choice among the given tasks. | `skyguard_agent/intent.py` (new): a deterministic intent class first. Navigation, read and explanation are answered without a plan. Operations are matched only to compatible tasks, and the model may answer "none". |
| Conversation state | A pending question was prepended to every later message, with no expiry. | It is kept for the same application, for 30 minutes, and only for answers or data entry. |
| Plan ↔ request | A run did not record which request or application it came from. | The run's `request.context` holds the intent, command, conversation, application, task and a fingerprint. These are used by `Autopilot.confirm` (refusals) and for duplicate prevention. |
| Confirmation | `Engine.confirm` checked the preview hash and readiness. The page decided about the checkbox. | `Autopilot.confirm` (shared by SkyGuard and the companion) adds the checks for the application, the plan's link to its request, nothing-to-run with the reasons, and the acknowledgement. The preview checks each record's current status during the read-only inspection. |
| Learning (Miner) | Commits came from a fixed word list that included "cancel". Single-page forms only. No actions on record pages. | Dismiss buttons are excluded. Multi-step chains are learned (Continue, then Finish). Detail-page actions are learned with key, URL template, required and resulting status, and the existing-record check. Code fields become `manual_secret` steps. Generated keys are captured in the success pattern. |
| Capture (`agent_dom.js`) | The text of a `<label>` included the options of the list it wraps. | The label text excludes `select`, `option`, `textarea` and `datalist`. The file is changed identically in SkyGuard and the extension (`test_shared_files_are_identical_to_skyguard`). |
| Verification of learning | None. | `Autopilot.verify_retrieval` routes every task title and page name through the command router. It is run on Stop & save (SkyGuard and companion) and on demand. |
| Execution result | Success or failure only. | `executor.reported()` adds the record number (the success pattern's capture group) and the status, only from what the page shows. |
| Suggestions | A fixed list of examples in the page. | `Autopilot.suggestions` builds them from learned knowledge, the current page, records waiting for a step that the accounts may perform, and recent commands. View-only users get no operations. |
| Extension | Learning only with "Learn automatically" on, or guided sessions. A failed send lost the events. | Explicit Start/Stop learning per application. A bounded, ordered queue of events and pages with backoff. Duplicate filter. Cached application list while offline. |

## Components touched

```
skyguard_agent/intent.py          (new)  intent classes, compatibility, relevance, navigation target
skyguard_agent/autopilot.py              command routing, suggestions, simple_view, confirm, verify_retrieval, outcome_text
skyguard_agent/agent.py                  context-aware operation planning, model limited to compatible tasks
skyguard_agent/planner.py                prompt allows "no task"; field names without units
skyguard_agent/understanding.py          dismiss buttons, multi-step chains, record-page actions, generated keys
skyguard_agent/executor.py               manual_secret step, reported number/status, status-aware preview
skyguard_agent/js/agent_dom.js           label text without options (same file in the extension)
qa_platform/api/agent_simple.py          /suggestions, /runs/{id}, /runs/{id}/cancel, /verify/{app}; confirm via Autopilot
qa_platform/opsagent/service.py          open_page (navigate replies), current_url, environment
web/js/pages/ai-agent.js                 four tabs: suggestions, short preview, kept checkbox, knowledge views
browser-extension/extension/*            1.3.0: Start/Stop learning, reconnect queue, dedupe, suggestions, preview
browser-extension/companion/.../server.py  /v1/simple/suggestions, /learn/{app}/save, /verify/{app}, /runs/{id}
```

## Rules kept

* No credentials in prompts, learning memory, scripts, screenshots or logs.
* MFA, CAPTCHA and application permissions are never bypassed. Codes are typed by the person.
* The model proposes and never executes. Plans run only through the validated steps of the execution engine.
* No machine-wide changes: no registry keys, system PATH, services or protected folders.
