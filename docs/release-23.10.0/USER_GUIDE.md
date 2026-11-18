# Using SkyGuard QA 23.10.0

## The AI Agent in four steps

**1. Connect.** Open **AI Agent → Connect & Learn**, paste the application's address (for example
`https://trips.company.local/`), keep or change the suggested name, choose **Edge** or **Chrome**, and press
**Connect and start learning**. A maximized window opens on the application.

**2. Learn.** Use the application as you always do: sign in, create a record, approve one, open the lists. The agent
watches and works out the screens, the tasks, the statuses (for example *Pending → Approved → Authorized*), the rules
the application enforces, and which role does what. You do not name anything and you do not approve each observation.
Press **Stop learning** when you are done. What it saw succeed is ready to use:

* **verified**: seen to succeed at least twice;
* **uncertain**: seen once; the agent can use it, and the preview tells you.

Passwords, one-time codes and MFA codes are never learned, logged or sent to the AI model.
**Explore automatically (read-only)** visits the screens it can reach without submitting anything.

**3. Use normally.** Nothing else to do. The more you use the application, the more the agent knows.

**4. Ask.** Open **AI Chat & Voice** and type, or press the microphone and speak:

| You say | The agent |
|---|---|
| *Create two trips for tomorrow for driver John* | Works out tomorrow's date, the next two trip numbers and available resources, asks once for anything still missing (for example the route), then shows **one preview** of both trips. |
| *Show pending trips* | Lists them as it last saw them. They become your **selection**. |
| *Approve the selected trips* | Previews the approval of the selection. Before acting on each one, it checks the trip's real status in the application. |
| *Authorize all approved trips* | Previews the authorization of every approved trip, as the role that authorizes. |

Nothing is submitted before you confirm. For an operation that changes the state of records (approve, authorize,
reject, cancel, delete and similar), also tick **I have read this preview**. Several records run one after the other,
with a checkpoint after each. **Pause**, **Resume** and **Stop** work at any time. The result of each record is shown
as *completed*, *failed* (with the application's own message), *uncertain* (check it in the application) or *skipped*.

If the agent must sign in as an account whose password is not saved, it says so and offers the **secure password
form** (Windows Credential Manager). Never type a password in the chat: it is withheld and not stored.
Sign-in pages with MFA or CAPTCHA pause the run so that you complete them yourself.

**Applications & Knowledge** shows what the agent knows. Use **This is wrong** to make it forget something, or
**Use it** to trust a suggestion. **Tasks & History** lists every run and its outcome.

Administrators: **Advanced Administrator Settings** (at the bottom of the AI Agent page) is the full agent page from
earlier versions: knowledge states, rules, locators, audit, and the *auto-trust* setting (on by default; when it is
off, what was learned waits for approval as before).

## Integration Comparison: record the way to a report

In **Integration Comparison**, open an application's **Connect** dialog:

1. Enter the start address and how to sign in (no sign-in, a login form, Windows sign-in, or *I sign in myself* for
   SSO or MFA).
2. Press **Start Guided Recording**. A maximized browser opens.
3. Sign in, open the menus and the report, set its filters and press its Search/Apply button. Go to the next page,
   and open one detail record if the comparison needs the detail lines. Then press **Stop & Save** in SkyGuard.
4. Check what was saved:
   * **Recorded steps**: edit the lines if needed (`click Reports`, `select Region = North`, `fill Date from = 2026-11-01`).
   * **Filters**: change a value here at any time without recording again.
   * **Detail pages**: for example *for every record, open `/orders/{key}` with its Order No*. You can switch this off
     or limit the number of records.
   * **View captured tables** and **Left out of the recording** (sign-in, pagination, sorting, and anything that looks
     like a change, each with its reason).
5. Press **Replay & Verify**. SkyGuard replays the steps in a new browser, reads **every page** and, when a detail page
   was recorded, the detail of **every** record. The dataset says whether it is complete.

**Re-record** starts again. **Import from browser extension** loads a recording made with the SkyGuard extension in
Edge or Chrome (Learn tab, *Record for Integration Comparison*). SkyGuard checks the file again and accepts only steps
on the application's own site.

The comparison only reads. A click that looks like a change (Save, Delete, Approve, …) is never kept, and a typed or
imported step like that is refused.
