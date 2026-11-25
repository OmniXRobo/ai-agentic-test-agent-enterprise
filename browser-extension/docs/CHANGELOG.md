# Changelog

## 1.5.0 (agent core shared with SkyGuard QA 23.15.0)

* New name: **SkyGuard Workflow Operator**. The extension does what it is called: you teach it your application's
  workflows, it previews them and runs them. Nothing about pairing, permissions or the companion changes, and the
  extension ID stays the same, so an installed extension only needs Reload.
* **Listen always**: one button in the chat starts hands-free listening. Say a request, pause, and it is transcribed
  and sent without touching the mouse; the reply is spoken in the language you spoke. The microphone is released
  between requests, listening stops when you leave the chat or press Stop listening, and a request that changes
  records still waits for the confirmation under its preview.
* Arabic spoken in the chat is understood even when speech recognition misspells it, and is answered in Arabic.
* The agent remembers the values and requests you use, so it asks for them once, not every time; sign-in passwords
  and authorization codes typed while teaching go to the credential store, masked (one-time codes are never kept).

## 1.4.0 (companion 1.4.0, agent core shared with SkyGuard QA 23.12.0)

Simpler offline install: no command to type and no pairing or port to configure.

* The installer copies the AI and speech models when it finds them on this PC, writes a one-time set-up ticket into
  the extension folder, opens the browser's extensions page and puts the extension folder path on the clipboard.
* On first start the extension pairs with the companion by itself using that ticket and signs in as the PC owner
  (the first admin is created for the Windows user). The ticket is single use, valid 7 days and deleted once used.
* A device paired with the ticket may sign in again without a password only as that owner; code pairing is unchanged.
* UPDATE_EXTENSION writes a fresh ticket, so an update also needs no pairing.
* Still by hand, because Edge and Chrome require it: Developer mode and Load unpacked, once.

## 1.3.1 (companion 1.3.1, agent core shared with SkyGuard QA 23.11.2)

* **Answers to missing information**: when the agent says "I still need: First Name, Last Name, Zip/Postal Code", an
  answer such as "First Name; muhammad", "last name Ali, zip 54000" or just "54000" (one field asked) is read field by
  field. The reply confirms what was received and asks only for what is still missing; "prepare it" asks again for
  the missing fields only. (Agent core, `skyguard_agent/autopilot.py`.)
* **Postal codes are not secrets**: "Zip/Postal Code: 54000" is no longer stopped by the side panel's secret check
  (`lib/secret-check.js`, identical to SkyGuard's). One-time codes, driver codes and PINs still are.
* **Applications that never show the signed-in user** (Swag Labs, for example) can run operations that no role
  restricts, after accepting the sign-in the agent made with the saved account. Role-restricted operations still need
  the role shown by the application.
* Nothing else changed: permissions, routes, pairing and the local channel are the same as 1.3.0.

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
