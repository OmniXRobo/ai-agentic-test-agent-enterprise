# Patch 23.3.21: Scheduled Runs, Outlook email reports, more recorded actions

Cumulative: includes 23.3.20 and everything before it.

## Automation Studio > Scheduled Runs (new page)

* The list shows the scripts of **AI-Driven Automation**, **Guided Browser Automation** and **Hybrid Test
  Automation** (and generated framework scripts). Filter by page, application, name / test case, or
  **Only scheduled**.
* Tick one or more scripts, then:
  * **Schedule**: Once (a future date and time), Every day, Monday to Friday, Chosen days of the week, or Every
    N hours. **Until** is optional: empty means the schedule continues.
  * **Run now**: runs the ticked scripts at once with the same options.
* Options: browsers (each script runs in each chosen browser: Chromium, Edge, Chrome, Firefox), **Runs at the same
  time** (parallel, 1 to 8), Excel / CSV data (row 1, or every row), visible or hidden browser, **Write Passed /
  Failed to TFS test runs**, **Run a missed time when the app opens**, and the **Email report**.
* The Schedules table: Run now, Pause / Resume, Edit, Delete; next and last run.
* Run history: each run with every script x browser x data row, its result, time, failure and a link to the run.
* Times use this PC's clock. The application must be running at the scheduled time. If it was closed, a missed
  time is run once when it opens (when that option is on), then the schedule continues.
* **Runs at the same time (Run all, Play selected)** at the top of the page also sets how many scripts the
  existing Run all / Play selected buttons run at once (default 1, as before).

## Settings > Outlook Email (new page)

* **SMTP**: Microsoft 365 (smtp.office365.com:587, STARTTLS), Outlook.com, or your company's Exchange relay
  (custom host, port, security). The password (or app password) is kept in the Windows Credential Manager, never
  in settings. Microsoft 365 needs SMTP AUTH allowed for the mailbox, or an app password.
* **Outlook app on this PC**: the Outlook desktop app signed in for this Windows user sends the mail. No password.
* **Send test email**.
* The report email has the results table in the body, the HTML report and failure screenshots attached. Send it
  after every run, or only when something failed.

## TFS test runs

With **Write Passed / Failed to TFS test runs**, each scheduled run creates a TFS test run per test plan that holds
the script's test case and sets each result to Passed, Failed or Not executed. Test cases in no plan go into one
automated run. The run history shows the TFS run ids or the error.

## Recording (Guided and Hybrid)

* **Double-click**, **right-click**, **hover** (when resting on a menu opens it and you then click inside it, a
  hover step goes before the click) and **drag and drop** (HTML5 or mouse drag) are recorded and played back.
* **iframes**: clicks and typing inside an iframe are recorded with the iframe chain and played back inside it.
* **Pop-ups** (alert / confirm / prompt): the bar and the page have **Pop-ups: OK / Cancel**. The answer is
  recorded as a step before the click that opens the pop-up, and playback answers it the same way.
* **New tabs**: actions in a tab the page opened are recorded with the tab number and played in that tab.
* **Click element: check text / check visible** (on the page and on the bar in the browser): the next click in the
  browser picks the element instead of clicking it, and a check step is recorded.

## Healing and Edit Script

* **Apply all healed steps** (Hybrid healing list, Script Studio results): applies the newest healed selector for
  each step in one go and says what was applied and skipped.
* Edit Script: each step has **Record before this step** and **Re-record this step**. They open Hybrid Test
  Automation at that step, with the earlier steps played first.

## Not tested here

The Outlook desktop app mode (Windows + Outlook COM) and real SMTP / TFS servers could not be tested in the Linux
test environment; SMTP and TFS were tested against stand-ins.
