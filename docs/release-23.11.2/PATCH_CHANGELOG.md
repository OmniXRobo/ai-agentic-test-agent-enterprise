# SkyGuard QA 23.11.2: the AI Agent understands your answers to "I still need: ..."

23.11.2 is a follow-up to 23.11.1. It contains all of 23.11.1 and every release before it, and upgrades any release
from 23.3.35 to 23.11.1 in one step. Browser extension and companion: **1.3.1**.

## Fixed

* **Answers to missing information are understood.** When the agent asks *"I still need: First Name, Last Name,
  Zip/Postal Code"*, an answer such as "First Name; muhammad", "first name muhammad", "last name Ali, zip 54000",
  "Muhammad; Ali; 54000" or just "54000" (when one field is asked) is read field by field.
* **What you answered is kept.** The reply says what was received ("Got it: First Name = muhammad.") and asks only for
  what is still missing. A message without a value ("pre pare it", "go ahead") asks again for the missing fields only,
  never for the ones already answered.
* **A postal code is not a secret.** "Zip/Postal Code: 54000" is no longer stopped as if it were a one-time code.
  One-time codes, driver codes and PINs are still stopped.
* **Applications that never show who is signed in can run tasks.** Swag Labs, for example, never shows the user name.
  Every task there stopped with *"not authorized: The signed-in identity has not been verified from the
  application"*. Now, when the agent signed in with the saved account and the application accepted it, an operation
  that no role restricts runs. Role-restricted operations, and Production mode, still need the identity and role shown
  by the application.

See ROOT_CAUSE_ANALYSIS.md for the causes.

## Changed files

* `skyguard_agent/autopilot.py`: `parse_answer`, `field_aliases`, answers kept with the pending question.
* `skyguard_agent/secrets_guard.py`, `web/js/secret-check.js`, `browser-extension/extension/lib/secret-check.js`:
  ordinary codes (postal, zip, product, ...) are not secrets.
* `skyguard_agent/executor.py`, `skyguard_agent/sessions.py`, `skyguard_agent/authz.py`: a sign-in confirmed by the
  application.
* Extension and companion 1.3.1: the same agent core and secret check. Nothing else in the extension changed.
* Installer: accepts 23.11.1, adds the check **Missing information follow-up (23.11.2)**. No database change.

## Earlier: 23.11.1

23.11.1 repairs the simple AI Agent introduced in 23.10.0. It is built on 23.11.0 and contains all of it. It upgrades
any release from 23.3.35 to 23.11.0 in one step. Browser extension and companion: **1.3.0**.

Why 23.11.1 and not "23.10.0": 23.11.0 was already released on top of 23.10.0. Calling this patch 23.10.0 would
either reuse a version number that is already installed, or leave out 23.11.0. The zip names you asked for are kept:
`SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch.zip` and `SkyGuard_SGQA-23.10.0_Edge_Chrome_Extension.zip`. Inside them
the version is 23.11.1, which upgrades a PC that has 23.10.0 or 23.11.0.

## Fixed

* **"Navigate to Online Store" opens the store.** A navigation is never read as a cancellation, deletion, approval or
  any other business task. You get the page; no plan and no preview are made. "Open the trip creation page" opens the
  learned page. "Show pending trips" lists them.
* **No unrelated or stale task.** Each command is first sorted into a class: navigation, read/search, data entry,
  create, edit, approve, authorize, authenticate, record, cancel/delete, or explanation. Only tasks of that kind are
  considered. If none fits, the agent asks one question instead of guessing. A question left open does not get mixed
  into your next command.
* **Confirmation works.** The confirmation box keeps its tick while the card refreshes. Confirm & Execute appears only
  when at least one record can run. Otherwise you see **Provide missing data**, **Edit request** and **Cancel**, with
  the reason. The server makes the same checks, so a preview for another application, for another request, or in
  which nothing can run is refused.
* **What you taught is used.**
  * A form with several pages (Continue, then Finish) is learned as one task, named after what it is ("Checkout
    order").
  * A Cancel or Close button no longer becomes a task.
  * Actions on a record's own page are learned with the status they need and the status they produce: approve,
    authorize, the driver's authentication with a code, and record.
  * Stop & save tests that every learned task and page can be found again by a command, and tells you if one cannot.
* **Missing information is asked before the preview.** For example, "Create a trip tomorrow from Dubai to Abu Dhabi
  for Aslam" answers "Missing information: Vehicle (BUS-101, BUS-102, VAN-7)". A vehicle is chosen for you only when
  you say "any available vehicle".
* **The result is the application's own.** After Confirm & Execute the reply quotes the number and status that the
  application shows, for example "number TRP-0003, status Pending Approval". Nothing is invented when the application
  does not show one.

## New

* **Dynamic suggestions** under the chat. They are built from what was learned for the selected application, the page
  open in the learning window, records waiting for a step that your accounts may perform, and your recent requests.
  View-only accounts get reading and navigation only. They refresh when something changes. The fixed example list and
  the permanent password warning are gone. A security notice appears only when a sign-in is needed, and the agent
  never asks for a password or code in the chat.
* **Environment** when connecting a website (Test or Production). A warning appears for Production, because learning
  there works with real records.
* **Applications & Knowledge** shows:
  * the learned pages, tasks, roles, rules and statuses;
  * the rules that are not verified yet (seen once: shown but not enforced until seen again or confirmed by an
    administrator);
  * the last learning session;
  * **Test learned knowledge** and **Test learned workflow**.
* **Role accounts**:
  * The agent uses the account whose role the task needs. When several accounts have that role, it uses the one that
    can sign in now.
  * A one-time code at sign-in, or a driver's code inside a task, is typed by the person in the application window.
    The agent pauses for it and never reads or stores it.
  * If no account is available, the agent asks you to save one in Windows Credential Manager. It never asks for the
    password in the chat.
* **Duplicate requests**: sending the same request again returns the preview already prepared instead of a second one.

## Browser extension and companion 1.3.0

* **Start learning / Stop & save learning** in the side panel (Learn tab), for the selected application, with a
  visible status:
  * a red **REC** badge on the toolbar icon and a banner on the page while learning;
  * the number of actions and pages sent;
  * a grey badge and "keeping your actions and retrying" while the companion cannot be reached.
* **Reconnect**: when the companion is restarting or unreachable, your actions and the pages you saw are kept in order
  (up to 500 items) and sent when it is back, with a backoff of 2 to 30 seconds.
* **Duplicates removed**: the same action reported twice within a moment (frames, re-injection) is kept once.
* The chat shows the same **dynamic suggestions** and the same short preview (Action, Application, values, Role,
  Status, then Confirm & Execute | Edit | Cancel, or Provide missing data | Edit request | Cancel).
* A "navigate" answer opens the page in a tab, only for a registered application's site that you granted.
* Unchanged: the permissions, the fixed extension ID, the local companion on 127.0.0.1 with pairing and sign-in, and
  that nothing goes to the cloud.

## Database

No new migration. The main database stays at 20 migrations and the agent's knowledge database at schema 2, with the
`conversation_state` table from 23.10.0. A rollback keeps the data as it is.
