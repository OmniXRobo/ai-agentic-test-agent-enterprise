# Learn while I browse: user guide (SkyGuard QA 23.7.0, browser extension 1.1.0)

The Operations Agent now learns an application **by watching you use it**. You do not name tasks, explain statuses or
type rules. You work as usual, and it works out the screens, tasks, statuses, business rules and roles itself.

## 1. Start learning

Register the application once (Operations Agent > Learning > *Register application*: name and URL). Then use either
way below. Both write to the same knowledge.

### In SkyGuard QA

1. Operations Agent > **Learning** > **Learn while I browse** (the card marked *recommended*).
2. A browser window opens on the application. Use it normally: sign in, open screens, create records, approve, search,
   sign out and sign in as another role. Mistakes are useful too: when the application refuses something, the agent
   learns the rule behind it.
3. Close the window, or press **Stop** on the card, when you are done. The agent analyses what it saw.

### In Chrome or Edge with the SkyGuard extension

1. Extension **Options** > card **Learning** > tick **Learn automatically while I browse my registered applications**.
2. Grant the extension access to the application's site when it asks (only registered applications are ever
   observed; other websites are not).
3. Just work in your normal browser tabs. The agent analyses every 25 actions and when the tab closes. The side
   panel's **Learn** tab shows whether automatic learning is on, with buttons to Options and to *What I understood*.

Tabs the agent itself is driving (a run, guided learning) are never learned from.

## 2. See what it understood

Open the **Understanding** tab (SkyGuard: Operations Agent > Understanding; extension: side panel > Understanding):

- **Tasks (flows):** each task with its fields in order, steps, what success looks like, which role did it and how
  often it was seen.
- **Lifecycles:** the statuses a record goes through, for example *Planned → Approved → Authorized*, with the task and
  role that moves it.
- **Rules:** what the application enforced, with the message it showed as evidence.
- **Roles:** who did what, which actions each role was offered, and what each role was refused.
- **Screens:** every screen, its kind (form, list, search, sign-in…) and how it is reached.

**Analyse now** re-reads everything observed so far. **Forget observations** (admin only) deletes the observation
journal for that application. Approved knowledge is kept.

## 3. Ask it in chat

Ask in your own words. Examples that are answered from what it saw:

| You ask | It answers from |
|---|---|
| what have you learned? | a summary: tasks, lifecycle, roles, rules, main screens |
| how do I create a trip? | the steps and fields in order, example values, options, what success looks like |
| who can approve trips? / what can a manager do? | the roles seen doing it, and roles that were refused |
| what happens after a trip is approved? / what statuses can a trip have? | the lifecycle |
| what rules apply to trip number? / can a bus be double booked? | rules from application messages and screen constraints |
| what fields do I need to add a trip? | the task's parameters and options |
| what is the status of T100? / how many trips are planned? | the last list pages it saw (it says so; it is not a live query) |
| where is the trip list? | the screens and how they are reached |

A question it cannot answer from the model is answered from the closest facts it has. With the local AI model
running, the wording is smoother.

## 4. Let it do a task

Write the request, for example:

> add 1 trip T300 on 2026-11-20 at 10:00, route Dubai to Sharjah, bus BUS-102, driver Dina Yousef as Entry Operator

The first time, the agent answers that it learned *Create trip* by watching and needs one approval. Press **Approve and
run** (or approve it in Knowledge). It then works as before:

1. signs in with the saved account for that role (add it under Learning > Accounts, with your consent to store the
   password in Windows Credential Manager);
2. reads the application first, without changing anything, to check duplicates and booking conflicts against the
   rules it learned;
3. shows the **preview** and waits for you to confirm;
4. performs the task through the application's normal screens and verifies the result in the application's list.

Rules learned from application messages start as *Discovered*. They are checked and shown in the preview, and block a
record only after someone approves them.

## 5. Privacy

- Passwords, one-time codes and any field marked secret are recorded as *secret entered*, never the value.
- Only the registered application's origin is observed. Clicks made by the agent itself are never learned.
- Everything stays on this PC, in `data\opsagent\knowledge.sqlite3` (SkyGuard) or
  `%LOCALAPPDATA%\SkyGuardAgent\data` (extension companion). The journal keeps at most 20 000 entries per
  application, oldest first out.
- What you submit while learning is real. Use a test environment or test data.
