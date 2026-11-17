# What is new in 23.11.0

23.11.0 builds on 23.10.0. The cumulative upgrade still starts from SGQA-23.3.35 and includes every release in between
(RELEASE_INCLUSION_MANIFEST.md).

## 1. Editable Python test in Guided Browser Automation

Open a Guided Browser script. The **Python test (Pytest + Playwright)** card now has a toolbar:
**Edit Script, Save, Validate, Run, Format, Revert, Regenerate, Version History, Download** and **Download ready-to-run .zip**.

* The editor shows syntax colours, line numbers, Tab and Enter indentation, Find / Replace / Replace all, and Undo / Redo
  (Ctrl+Z / Ctrl+Y). Ctrl+S saves and Ctrl+F finds.
* **Validate** lists every syntax error with its line and column. Click an error to jump to that line. Save refuses a
  file with errors.
* Validate also refuses code a browser test does not need: imports outside an allowlist (`subprocess`, `socket`,
  `ctypes` and similar), `eval`, `exec`, `os.system`, deleting files, `__dunder__` access and `sys.modules`.
* Every save is a **version** with a note, an author and a time. **Version History** views or restores any version, and
  a restore is saved as a new version.
* Once you save an edit, **the saved version is what Run runs and what Download gives**. That applies to the main Run
  button, scheduled runs, and data-driven and bulk runs of that script. The recorded steps and the TFS Test Case link
  are not changed.
* **Regenerate** writes the Python again from the steps. If you have edits, it first asks before it overwrites them,
  and your edits stay in Version History. **Revert** goes back to the generated Python.
* Only Admin and Tester accounts can edit, save or run the Python. Setting: `automation.python_roles`. Developer and
  Manager accounts can view it.
* A failed run of the edited Python keeps the pytest output, the traceback, a screenshot and a Playwright trace. The
  trace is kept only when no password or other secret was typed. The failure becomes a Defect Review draft
  (section 4).

## 2. Smart Wait

One Smart Wait service (`qa_platform/automation/smart_wait.py`) is used by:

* the recorder: an action after which the page started AJAX or fetch requests gets an **AJAX/Data Load** wait after it,
  marked "seen while recording";
* the runner (playback);
* the exported Python. The same code is embedded in the file between `# --- SMART WAIT BEGIN/END`, so the downloaded
  test waits exactly like SkyGuard does.

What it waits for:

* the document's ready state;
* AJAX and fetch requests started by the action (long polling is reported, not waited for);
* `jQuery.active`;
* `aria-busy`;
* visible loaders and overlays (spinner, BlockUI, DataTables "processing", autocomplete loading);
* a quiet period without DOM changes;
* for the targeted kinds, the element state, its text or value change, or the table rows.

It does not just sleep, does not rely only on `readyState` or `networkidle`, and does not change Playwright.

* **Wait settings** (above the steps): enabled, mode (Smart, Fixed, Smart + Minimum), timeout (default 30 s), scope
  (all steps or selected steps), include navigation. The settings are saved with the script, and the runner and the
  exported Python use them.
* **Insert waits…**: inserts, updates or removes waits after all steps, selected steps, clicks, dropdowns, navigation or
  data steps. A wait is never inserted twice after the same step. Remove deletes only waits that were inserted
  automatically, unless you also choose manual ones.
* **+W** on each step adds a wait of one of 8 types: Page Load, AJAX/Data Load, Element Visible, Element Hidden,
  Text/Value Change, Fixed Delay, Network Response, Table Refresh.
* A wait that times out is reported on the step: what was still busy, how long it waited, and whether the run
  continued. The page's busy state is saved with a failure, and the classifier uses it (timing/synchronization).

## 3. Integration Comparison → TFS fixed

**Root cause of "Unexpected Server Error":** the comparison's TFS code read `exc.code` from SkyGuard's own error
type, which has `error_code`. So any TFS refusal ended as a 500 error instead of a clear message: a mandatory field,
a duplicate, or a connection problem. The same mistake was in the comparison's filter and workspace code.

Now:

* Integration Comparison defects go through the shared publisher (section 5).
* Mandatory fields come from **TFS Preferences**, and missing ones are asked for.
* The evidence is complete: the field values on both sides, the rule, the record key, the screenshots and the source
  files.
* Only **confirmed mismatches** are offered as defects. Results that are incomplete, uncertain, missing a key, or
  depend on an unapproved rule are labelled as such.
* **Open in Defect Review** shows the draft first.

## 4. Drafts for every failure

* **AI-Driven Automation:** every failed run becomes a draft with the status **Needs Review**. You are notified, and the
  run stays Failed.
* **Guided Browser Automation:** a draft is made for recorded scripts, edited Python, data-driven rows, bulk runs and
  scheduled runs.
* Each draft carries the evidence: failed step, expected and actual result, locator, frame, previous step, dataset,
  browser, traceback, screenshot, trace and log. Passwords and tokens are masked.
* A repeated failure adds an occurrence to the existing draft instead of making a new one.
* Each draft is classified into one of six classes, with a confidence and the evidence behind it: **application
  defect, automation script defect, test data defect, environment/connectivity failure, timing/synchronization
  issue, inconclusive**. Rules decide first. A local Qwen (on-premise) may refine it: it gets only the redacted facts and must
  answer one of the fixed classes, or it is ignored. Its reasons are shown as "AI:" next to the rules' view.
* A suggested change (for example "add a Smart Wait") is never applied without you.
* A rerun creates a new run and keeps the original record.

## 5. One Defect Review page and safe TFS publishing

* **Statuses:** Draft, Needs Review, Approved, Publishing, Published to TFS, Publication Failed, Rejected, Duplicate.
* **Actions:** View Failure, View Evidence, AI Analyze, Edit, Save Draft, Rerun, Approve, Reject, Send to TFS, Retry,
  Open in TFS.
* **Nothing is sent to TFS without approval.** This includes the "automatic" defect mode, which now only prepares
  drafts. Editing an approved draft needs a new approval. Approving and sending need a role in
  `defects.approver_roles` / `defects.publisher_roles` (default Admin and Tester).
* Before it sends, SkyGuard checks:
  * the mandatory fields (validate-only call to TFS),
  * the approval,
  * the connection,
  * the attachments (each file exists and is at most 20 MB),
  * likely duplicates.
* Each send carries a **correlation ID** (TFS tag `SkyGuard-<id>`). If the connection drops after TFS received the
  item, SkyGuard searches for that tag before trying again, so a lost response never creates a second bug.
* SkyGuard says "Published" only after it **reads the work item back** from TFS. It then shows the real ID, URL and
  time. A failure shows the reason and the correlation ID, and **Retry** is offered.
* **Link to an existing defect** records the TFS ID and URL without creating anything.
