# SkyGuard QA 23.10.0 changelog

Cumulative from 23.3.35. The notes of 23.4.0 to 23.9.0 are in the `previous-*` folders and are summarized in
RELEASE_INCLUSION_MANIFEST.md.

## AI Agent (simple mode)

* New **AI Agent** page with four tabs:
  * **AI Chat & Voice**: type or speak a request, answer the agent's one question, read one consolidated preview,
    confirm.
  * **Connect & Learn**: paste the application's address. The name is suggested, you choose Edge or Chrome, and a
    maximized window opens. Use the application normally; the agent learns while you browse. *Explore (read-only)*
    visits screens without submitting anything (*Explore automatically (read-only)*).
  * **Applications & Knowledge**: what the agent knows, marked *verified* (seen to succeed at least twice) or
    *uncertain* (seen once). *This is wrong* forgets an item, and *Use it* trusts a suggestion. Also the roles, the
    status flow, and the accounts, each with a secure *Save password* (Windows Credential Manager).
  * **Tasks & History**: every run with its completed, failed, uncertain and skipped records.
* No workflow names to type and no approval for each observation. What was seen to succeed is trusted automatically
  (an administrator can switch this off with `auto_trust`). AI guesses stay suggestions until you use them.
* Commands in plain words:
  * "Create two trips for tomorrow for driver John": works out the dates, numbers, the next trip number and available
    resources, and asks once for anything missing.
  * "Show pending trips": the list becomes your selection.
  * "Approve the selected trips" and "Authorize all approved trips": the task is chosen by the first operation word,
    and each record's real status is checked before acting.
* Operations that change the state of records (approve, authorize, reject, cancel, delete, …) need a ticked
  confirmation box in addition to the preview, and the server refuses them without it.
* If the password for the needed account is missing, the agent says so and offers the secure form. A password typed
  into the chat is never stored (as before).
* The previous agent page is now **Advanced Administrator Settings**, for administrators only.

## Integration Comparison: guided recording

* The application's **Connect** dialog has **Start Guided Recording**, **Stop & Save**, **Edit Recorded Steps**,
  **Replay & Verify**, **Re-record**, **View Captured Tables** and **Import from browser extension**.
* The recording keeps the navigation (menus, tabs, links, the report), the filters with their values (which you can
  change later without recording again), whether pagination was used (every page is always read), and a detail page
  as an address with the record's key (`/orders/{key}`), so the detail of every record is read.
* The recording leaves out the sign-in (the saved login or the tester signs in at replay; MFA and CAPTCHA are never
  bypassed), the pagination and sorting clicks, and anything that looks like a change. Each item left out is listed
  with its reason.
* Master-detail: the detail lines are a second dataset. Each line carries its parent key (*Parent Order No*), and the
  dataset is marked incomplete if a parent could not be read.
* Typed steps are still available under *Advanced (manual navigation)*.

## Browser extension and companion 1.2.0

* The chat uses the simple agent.
* The confirmation box for consequential runs, enforced by the companion.
* The secure credential page offered from the chat.
* Learn tab: **Connect an application** and **Record for Integration Comparison**, which saves a file for SkyGuard's
  *Import from browser extension*.

## Installation

* **Setup.exe** wizard (Welcome, Detect, Backup, Install, Verify, Finish). It runs per user with no administrator
  rights, works offline, and has an unattended mode.
* The installer accepts 23.3.35 to 23.9.0 and refuses anything else before changing anything.
* The new knowledge-database table `conversation_state` is added during the upgrade.
* `detect` (read only) and `--log`.

## Fixes found while building this release

* "Authorize all approved trips" was understood as *Approve*, because the word *approved* matched first. The task is
  now chosen by the operation word that comes first in the sentence.
* The application name suggested for an IP address contained a colon, which application names do not allow.
* The run card read the plan from the wrong place, so the confirmation box and the task list did not show.
* The LLM status label showed a method object instead of the model's name.
* SHA-1 used for record identifiers in the comparison is now marked as not for security
  (`usedforsecurity=False`). The identifiers did not change.
