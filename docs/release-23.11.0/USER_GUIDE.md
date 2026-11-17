# User guide: what changes for you in 23.11.0

After the upgrade, press **Ctrl+F5** once in the browser so the new pages load.

## Edit the Python test of a recorded script

1. Open **Guided Browser Automation** and choose a script.
2. Scroll to **Python test (Pytest + Playwright)** and press **Edit Script**.
3. Change the code. Use **Find / Replace**, **Undo / Redo** (Ctrl+Z / Ctrl+Y), and **Format** to tidy indentation and
   blank lines.
4. Press **Validate**. Every error is listed as *Line N, column M*, and clicking it jumps to that line.
5. Type a short note (what changed) and press **Save** (or Ctrl+S). You now have version 1. From now on:
   * **Run**, scheduled runs, bulk runs and *Run data rows* run this saved version. With Excel or CSV data, each row
     is its own test.
   * **Download** gives this saved version. *Download ready-to-run .zip* adds `pytest.ini`, `requirements.txt`,
     `README.txt` and `RUN_TESTS.cmd`.
6. **Version History** shows every version. **View** shows it, and **Restore** makes it current again (as a new
   version).
7. **Revert** goes back to the Python generated from the recorded steps. **Regenerate** writes it again from the steps;
   if you have edits, it asks first. Your edits always stay in Version History.

Your recorded steps and the TFS Test Case link are not touched by any of this. Passwords are never written in the
file: the test reads them from environment variables that SkyGuard fills from Windows Credential Manager when it runs.

Who can do this: Admin and Tester accounts. Developer and Manager accounts can view the code.

## Make playback wait for the application (Smart Wait)

**When the whole script should wait.** Above the steps, press **Wait settings**:

* **Enabled.**
* **Mode:**
  * *Smart* waits until the page is quiet. That means no AJAX or fetch requests, no `jQuery.active`, no `aria-busy`,
    no visible loader, and no DOM changes for a moment.
  * *Fixed* always waits the same time.
  * *Smart + Minimum* waits until the page is quiet, and at least the minimum.
* **Timeout:** 30 seconds by default.
* **Scope:** all steps, or only the steps you choose.
* **Include navigation.**

The settings are saved with the script and used in SkyGuard and in the downloaded Python.

**When only some steps should wait.** Press **Insert waits…** and choose after which steps: all, selected, clicks,
dropdowns, navigation or data steps. Press it again and nothing is added twice. The same dialog updates or removes the
waits it inserted.

**One wait of a particular kind.** Press **+W** on a step and choose one of these types:

* Page Load
* AJAX/Data Load
* Element Visible
* Element Hidden
* Text/Value Change
* Fixed Delay
* Network Response (part of the URL)
* Table Refresh

**While recording.** When the page loads data after something you do, for example a dependent dropdown or a filter,
the recorder adds an *AJAX/Data Load* wait after that step for you. It is marked "seen while recording".

If a wait times out, the step's result says what was still busy and how long it waited.

## Review failures before they reach TFS

Open **Quality → Defect Review**. Every failed run becomes a draft there, from:

* AI-Driven Automation;
* Guided Browser Automation (recorded, edited Python, data rows, bulk, scheduled);
* the Integration Comparison, via *Open in Defect Review* on a confirmed mismatch.

You are notified when a draft needs review.

For each draft:

* **Classification** is one of six classes, with a confidence and the evidence behind it:
  * application defect
  * automation script defect
  * test data defect
  * environment/connectivity failure
  * timing/synchronization issue
  * inconclusive

  **AI Analyze** asks your local Qwen (if configured) for a second opinion. It sees only the redacted facts, and its reasons are marked "AI:".
* **View Failure** shows the step, the expected and actual result, the locator, the dataset and the traceback.
  **View Evidence** shows the screenshots, the Playwright trace and the log.
* **Edit** and **Save Draft** let you change the title, severity, priority and repro steps. Editing an approved draft
  needs a new approval.
* **Rerun** runs the script again. The original run and draft are kept.
* **Approve**, then **Send to TFS**. SkyGuard checks the mandatory fields (from Preferences → TFS Integration), the
  connection, the attachments and likely duplicates. It then creates the bug and reads it back. Only then does it show
  **Published to TFS** with the real ID, a link (**Open in TFS**) and the time.
* If sending fails, the status is **Publication Failed**, with the reason and a correlation ID. Press **Retry**. If
  the bug was created but the answer was lost, Retry finds it by the correlation ID instead of creating a second one.
* **Reject** (with a reason) when it is not a defect. **Duplicate of another defect** links the draft to an existing
  TFS bug.

Nothing is ever sent to TFS without an approval, not even in the "automatic" defect mode.
