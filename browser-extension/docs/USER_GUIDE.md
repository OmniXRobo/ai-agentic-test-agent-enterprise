# Using the extension

Click the toolbar icon to open the side panel. Tabs: **Chat**, **Execution**, **Applications**, **Learn**,
**Knowledge**, **Audit**. The pill at the top shows the companion and who is signed in.

## 1. Applications and accounts

1. Applications > name and address > **Register and grant access**. The browser asks for access to that site only.
2. **Add account** for each application account the agent may use (label, login name, role in the application).
3. **Add credential** opens a separate small window (*Secure credential*). Type the password twice and tick *I agree
   that this password is saved in the vault on this PC and typed by the agent only into this application's sign-in
   page*. Without the tick nothing is saved. The password goes straight to the companion's vault (Windows DPAPI, your
   user only). It is never shown again, never in chat, logs, audit or the AI. **Revoke** deletes it.

## 2. Learn

* **Guided learning**: type what you are teaching (for example *Create trip*), press **Start recording**, then do the
  task once in the application tab. Password fields are recorded as *secret entered*, never their value. Press
  **Finish and review**: the agent proposes a workflow and, if AI is available, rules.
* **Automatic discovery (read-only)**: choose an account and the number of screens; the agent opens screens and reads
  them. It never submits anything.
* **Learned (awaiting your approval)** and the Knowledge tab: **Approve** or **Reject** each entry. Only approved
  knowledge is ever used.

## 3. Ask, preview, confirm

In Chat, write what you need, for example:
*create 2 trips on 2026-11-20 from 07:00 every 240 minutes, route: Dubai to Sharjah, bus: BUS-101, driver: Dina
Yousef, trip numbers starting at T100 as Operator*.

The agent opens the application in a normal tab of your browser, checks existing records where a rule needs it,
and shows the **preview** in the Execution tab: each record, the account, and any rule finding. Nothing is entered
yet. **Confirm** starts exactly what was previewed. While it runs, a banner in the application tab shows what the
agent is doing. **Pause**, **Resume**, **Stop** and **Cancel remaining** are always available.

At the end every record shows what the application itself confirmed: *Confirmed success*, *Confirmed failure*
(with the application's message), *Skipped*, *Manual intervention required* or *Unknown / unverified*. IDs are shown
only when the application displayed them.

## 4. When it needs you

If the application asks for a one-time code, MFA, a driver authentication code or a CAPTCHA, or a control cannot be
found unambiguously, the run pauses, the badge blinks, a Windows notification appears and the application tab shows
what is needed. Do that step yourself in the tab, then press **Resume**. When you clicked a control the agent could not
find, it remembers it for next time (after your approval). **Acknowledge** stops the notification.

## 5. Several accounts

*... as Operator, then approve them as Manager*: the extension works in your own browser profile, so it signs out
through the application's logout, checks the sign-in page is back, signs in as the next account and checks the name and
role the application shows before it continues.

Do not use the same application in another tab while a run is active: the sign-in state is shared by the browser
profile.

## 6. Voice

**Speak** records from the microphone until you press it again (allow the microphone once in Options). The audio
goes to the companion's local speech recognizer; the text appears in the chat box for you to check and send.

## 7. Audit

Every sign-in, approval, preview, confirmation, result, pause and manual step, with the companion user, the
application account and time. The tab shows whether the hash chain is intact.
