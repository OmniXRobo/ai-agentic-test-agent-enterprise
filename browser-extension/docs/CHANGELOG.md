# Changelog

## 1.3.0 (companion 1.3.0, agent core shared with SkyGuard QA 23.11.1)

* **Start learning / Stop & save learning** for the selected application in the Learn tab, with a visible status: a
  red REC badge and a page banner while learning, and the number of actions and pages sent. Stop & save analyses what
  was seen and tests that every learned task and page can be found again by a command, then reports the result.
* **Reconnect**: while the companion cannot be reached, the person's actions and the pages they saw are kept in order
  (up to 500 items, in memory) and sent when it is back, retried after 2 to 30 seconds. The badge turns grey and the
  Learn tab says so. The list of registered applications is cached, so pages are still recorded during the outage.
* **Duplicates removed**: the same action reported twice within 0.4 s (frames, re-injection) is kept once.
* Chat:
  * **suggestions** from what was learned, the page in the active tab, records waiting for a step, and recent requests
    (viewers get no operations);
  * no fixed example text and no permanent warning;
  * "Missing information" is shown as it is asked;
  * a navigation answer opens the page in a tab, only for a registered and granted site.
* **Short preview**: Action, Application, the values, Role, Status, then **Confirm & Execute | Edit | Cancel**. When
  nothing can run, it shows **Provide missing data | Edit request | Cancel** and the reason. The confirmation box keeps
  its tick while the panel refreshes. Confirm sends the application, and the companion checks it.
* Agent core: intent routing, plans linked to their request, the shared server-side confirmation, multi-step and
  record-page learning, and the label text without the options of a list (`lib/agent_dom.js`, identical to
  SkyGuard's). See SkyGuard 23.11.1's ROOT_CAUSE_ANALYSIS.md.
* Companion routes: `GET /v1/simple/suggestions`, `POST /v1/simple/learn/{app}/save`, `POST /v1/simple/verify/{app}`,
  `GET /v1/simple/runs/{id}`.
* Unchanged: the permissions, the fixed extension ID, local-only operation, pairing and sign-in.

## 1.2.0 (companion 1.2.0, agent core shared with SkyGuard QA 23.10.0)

* Simple agent: the chat uses the same simple agent as SkyGuard's new AI Agent page. Requests in plain words or voice
  ("Create two trips for tomorrow for driver John", "Show pending trips", "Approve the selected trips", "Authorize all
  approved trips") are understood from what was learned while browsing, missing details are asked once, and one
  consolidated preview is shown. What was learned is trusted automatically when it was seen succeed (verified when
  seen at least twice, otherwise marked uncertain); AI suggestions stay suggestions.
* Operations that change the state of records (approve, authorize, reject, cancel, delete ...) need a ticked
  confirmation box under the preview as well as the preview itself; the companion refuses the confirmation without it
  (ACKNOWLEDGE_REQUIRED).
* When a sign-in password is missing, the chat offers the secure credential page (consent, Windows Credential
  Manager); the password never goes through the chat.
* Learn tab: Connect an application by its address (name suggested; the same site is reused).
* Learn tab: Record for Integration Comparison. Records the way to a report in the person's own tab (menus, report,
  filters, pagination, one detail record) and saves it as a file for SkyGuard's Integration Comparison (Connect to
  Application, Import from browser extension). Read-only: sign-in steps, passwords and anything that looks like a
  change are left out; SkyGuard checks the file again on import and keeps it to the application's own site.
* Companion: /v1/simple/connect, /v1/simple/knowledge/{app}, /v1/simple/trust/{app}, /v1/simple/tasks,
  /v1/comparison/recording; /v1/runs/{id} reports whether a run is consequential.

## 1.0.0 (companion 1.0.0, agent core shared with SkyGuard QA 23.5.0)

First release.

* Manifest V3 extension for Google Chrome and Microsoft Edge 116+: side panel with Chat, Execution, Applications,
  Learn, Knowledge and Audit; Options (pairing, companion users, typing, microphone, AI and speech settings); separate
  credential page with consent; microphone page.
* Learning: guided learning by demonstration in the user's own tab, read-only discovery, AI suggestions (local Qwen),
  approval workflow with versions and rollback.
* Operations: request in plain words or voice, inspection of existing records, rules (required, pattern, allowed values,
  date, time, range, unique, no overlap, depends), preview, confirmation bound to the preview, human-like typing,
  verification of the application's own response, per-record results, pause / resume / stop / cancel remaining,
  manual intervention with learning, self-healing locators, account switching through logout and verified sign-in.
* Local companion (Python standard library): 127.0.0.1 only, origin and host checks, pairing, users and roles,
  short-lived tokens, rate and size limits, DPAPI vault, hash-chained audit, knowledge backup and restore, local Qwen
  (llama-server) and Faster-Whisper management.
* Per-user Windows installer and uninstaller without administrator rights, offline runtimes pinned by SHA-256, model
  installer from SkyGuard or offline media, prerequisite check.
