# SkyGuard QA 23.5.0: Adaptive AI Application Learning & Operations Agent

The agent learns a web application with your approval, then operates it through its normal screens: you ask in
plain words (typed or spoken), it shows a preview, you confirm, it fills the application's own forms in a normal
browser, and it reports for every record what the application confirmed. It never writes to the application's
database, never needs database access, and never bypasses the application's own validation, sign-in or MFA.

Open it from the left menu: **AI Operations Agent**. The page has tabs: Chat, Execution, Applications, Learn,
Knowledge and Audit.

## How it fits into SkyGuard

| SkyGuard part reused | How the agent uses it |
| --- | --- |
| Sign-in and roles (23.3.34) | No second login. Admin and Tester accounts can learn, confirm and run. Developer and Manager accounts are view only. Restoring or importing knowledge and changing the agent's settings need an admin. |
| AI gateway (On-Premise Qwen, llama-server) | Understands requests, proposes rules and workflows from what you showed it. Never receives a password. |
| Voice (Faster-Whisper / built-in) | Spoken requests become text, which goes through the same secret check as typed text. |
| Windows Credential Manager (secret store) | Saved application passwords, namespace `opsagent`, only after you tick the consent box. |
| Browser Manager and Playwright | The browser chosen in Preferences, visible by default. Manual intervention happens in that same window. |

The agent's knowledge lives in its own database, `data\opsagent\knowledge.sqlite3`. SkyGuard's main database is not
changed by 23.5.0 (still 18 migrations), so a rollback leaves SkyGuard's data exactly as it was.

## 1. Register an application

Applications tab: name and address (http or https), then **Register**. Then add one entry per account you want the
agent to use (for example Operator, Manager), with the role the account has in that application.

To save an account's password, use **Add / replace credential**. The form asks you to tick *I agree that this
password is stored encrypted in the Windows credential vault for this account*. Without the tick nothing is stored. Passwords are
never shown again, never exported, never sent to the AI and never written to logs, audit, chat or screenshots.
**Revoke** deletes a saved password at once.

Accounts that need a one-time code, MFA, a driver authentication code or a CAPTCHA are supported only with you at
the screen: the agent stops and says *Authentication required. Please complete the one-time code / MFA step yourself
in the browser.* It never reads, stores or types those codes.

## 2. Teach it (three ways, all needing your approval)

* **Discovery** (Learn tab): the agent signs in with a saved account and visits the application's pages (at most 25
  by default), recording screens, fields, labels, menus and links. Nothing is submitted.
* **Guided learning** (Learn tab, *Start guided learning*): a browser opens; you perform the task once yourself.
  Every click, choice and typed value is recorded (password fields are recorded as *secret entered*, never the value).
  When you finish, the agent proposes a workflow with the steps and the fields that change per record.
* **Describe it** (Chat): write a rule or a step in plain words, for example *trip numbers are unique* or *a bus can
  not have two trips at the same time*. The AI proposes the structured rule.

Everything learned starts as **Discovered** or **AI suggested** and is used only after someone approves it in the
Knowledge tab. Each entry shows its source (discovery, demonstration, AI, a person, import), its version and who
approved it. **Correct** creates a new version that needs approval again; **Roll back** brings back an earlier version.
An unapproved workflow can never run.

Rule types: required, pattern, allowed values, date, time, range, unique, no overlap (a resource cannot be booked
twice for overlapping times), and depends (a field is required when another one is set).

## 3. Ask it to do something

Chat: for example *create 3 trips on 2026-11-20 from 07:00 every 60 minutes, route Dubai to Sharjah, bus BUS-101,
driver Dina Yousef, trip numbers starting at T100 as Operator*.

1. The agent picks the approved workflow and the account, generates the records, and checks them against the
   approved rules. When a rule depends on data already in the application (unique, no overlap) the agent first
   reads the application's own list screens to see existing records.
2. **Preview**: every record, the account it will use, and every rule finding. Records that break an enforced rule are
   marked and will be skipped. Nothing has been entered yet.
3. **Confirm** runs exactly what was previewed (the confirmation is bound to a hash of the preview; any change needs a
   new preview).
4. **Execution**: the agent signs in through the login screen, opens the form, fills each field the way a person
   types, submits, and then checks the application's own response: a success message, the new record in the list, or
   an error shown by the application. Each record ends as *Confirmed success*, *Confirmed failure* (with the
   application's message), *Skipped*, *Manual intervention required* or *Unknown / unverified*. The agent never
   invents record IDs or statuses: an ID is shown only when the application displayed it.

**Pause**, **Resume**, **Stop** and **Cancel remaining** are available while it runs. A pause keeps the browser
open where it is.

### Several accounts in one request

*Create the trips as Operator, then approve them as Manager*: the agent runs the first stage with the Operator
account, signs out through the application's logout, signs in as Manager, checks the signed-in name and role shown
by the application, and only then continues. Sessions and cookies are never shared between accounts (each account
gets its own browser context in SkyGuard). It never acts as an account you did not name.

## 4. When the screen changed (self-healing) and manual intervention

The agent remembers where each control was found and how it was verified. If a control moved or was renamed, it
looks for it by its label, role and the form it belongs to. If exactly one match is found, it continues and the new
location is proposed for approval (shown in Knowledge as a healed locator). If several controls could match, or none,
the agent does not guess: it pauses, highlights what it is looking for, and asks you to do that one step yourself in
the browser. It watches what you do, uses it to continue, and remembers it for next time (after approval).

A field that refuses typed text also pauses the run (the agent never assigns a value through script to get around
the application's checks).

## 5. Voice

Press the microphone in Chat, speak, press again. Speech recognition runs locally (Faster-Whisper or the built-in
recognizer). The text appears in the chat box for you to check before sending; it is never sent automatically.

## 6. Audit, backup, export

* **Audit** tab: every sign-in, approval, preview, confirmation, record result, pause and manual action, with the
  account and time. The trail is hash-chained; the tab shows whether the chain is intact or the first record where it is broken.
* **Backup** (Knowledge tab) copies the knowledge database to `data\opsagent\backups`. **Restore** (admin) first backs
  up the current state.
* **Export** writes approved knowledge (applications, screens, workflows, rules, locators) to JSON without any
  credential. **Import** (admin) brings it in as *awaiting approval*, never as approved.

## Security rules the agent follows

* A password typed or spoken into the chat is detected before it is sent: it never reaches the AI, chat history,
  memory, logs, audit, screenshots or any index. The page says the message *looks like it contains a password or a one-time code, so it was
  not sent*.
* One-time codes, MFA secrets, driver authentication codes and recovery codes are never stored.
* Page content is treated as data, never as instructions: text on a web page cannot approve, confirm or change what
  the agent does.
* No direct database writes, no script value assignment, no CAPTCHA or MFA bypass, no cookie sharing.
* No cloud service is needed. Qwen and Whisper run on this PC.
