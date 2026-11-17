# Changelog

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
