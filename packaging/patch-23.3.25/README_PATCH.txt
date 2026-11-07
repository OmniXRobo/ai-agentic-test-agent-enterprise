QA Enterprise AI - patch 23.3.25 (cumulative): Performance Test Data
=====================================================================

File: qa-enterprise-ai-patch-23.3.25-perf-test-data-20261105.zip (for a PC on 23.3.24, 23.3.23 ... 23.3.11, or
23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.24, 23.3.23, 23.3.22 and 23.3.21.

* Performance Testing > Performance Test Data (new page): data sets of user names, passwords, ids or search terms
  for virtual users. Create one, or import a CSV file (UTF-8 or Excel's Windows-1252; comma, semicolon or tab).
  Values are kept exactly: 00123 stays 00123, accents and other scripts stay as they are.
* Column types (text, whole number, number, email, date, true/false, secret) are checked; wrong values are listed
  with their row numbers. Secret columns (passwords, tokens) are kept in the Windows Credential Manager: never
  shown again, exported empty, and written for a run only until JMeter ends.
* Generate rows from rules (running numbers, emails, ranges, lists, dates), or ask the AI set up on this PC for
  invented rows (checked against the column types).
* Script editor > Test data tab: add a data set to a thread group (shared by all users, or read by each user),
  then bind request values (JSON body, form, query, header) to its columns. Press Save to keep it.
* Pre-run checks: the data set exists, has the columns the script reads, valid values and enough rows for the
  users. config.json of a run records which data set and version were used.
Details: app\docs\PATCH_23.3.25.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.25-perf-test-data-20261105.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.25-perf-test-data-20261105.zip -DestinationPath .\qa-patch-23.3.25 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.25\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.24 (cumulative): Record performance scripts from a browser
==========================================================================================

File: qa-enterprise-ai-patch-23.3.24-perf-recorder-20261105.zip (for a PC on 23.3.23, 23.3.22 ... 23.3.11, or 23.3.7
to 23.3.10 with Faster-Whisper already installed). Includes 23.3.23, 23.3.22 and 23.3.21.

* Performance Test Plans > Record a browser journey: a browser window opens on this PC. Use the application as a
  user would; name each step (for example "Log in", "Search") before doing it. Stop, review, Create script.
* Requests to other hosts (analytics, fonts, ads) and static files (images, styles, scripts) are left out; untick
  any other request in the review to leave it out too.
* Token handling: a value the server hands out (a hidden CSRF field, a JSON access token, an id) that a later
  request sends back is extracted with a JSON or Boundary extractor and reused, so each virtual user gets its own.
  Values that look dynamic but cannot be traced are listed under "Check these".
* Typed passwords never go into the script: they become ${__P(password)} and the value is saved in the Windows
  Credential Manager. JMeter gets it only while a test runs; the file is deleted when JMeter ends. A missing value
  fails the pre-run check, and the script editor has a Secrets section to enter it.
* Import a HAR file (saved from the browser's developer tools) instead of recording; the same filtering and token
  handling apply.
* A recorded script starts with 1 user for 60 seconds. Saving or recording never starts a load test.
Details: app\docs\PATCH_23.3.24.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.24-perf-recorder-20261105.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.24-perf-recorder-20261105.zip -DestinationPath .\qa-patch-23.3.24 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.24\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.23 (cumulative): Performance script editor
=========================================================================

File: qa-enterprise-ai-patch-23.3.23-perf-script-editor-20261105.zip (for a PC on 23.3.22, 23.3.21 ... 23.3.11, or
23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.22 and 23.3.21.

* Performance Test Plans > Import a JMX file: a script saved by JMeter 5.x. Every element is kept, also the ones the
  editor has no form for. Nothing is run.
* Open in script editor (on a generated plan): edit the JMX as a tree. Add HTTP requests, transactions, regular
  expression / JSON / boundary extractors, response / duration / JSON assertions, timers, headers, HTTP request
  defaults and variables; rename, enable / disable, duplicate, move and delete elements.
* Raw JMX view: edit the XML directly; Validate shows problems (not XML, no thread group, JMeter plugins, endless
  loops) before it is used.
* Each Save keeps a version with a note; compare two versions line by line, download or restore any of them.
  Saving never starts a test.
* Pre-run checks read the script: hosts from variables and HTTP request defaults, users and duration from its thread
  groups, missing CSV data files, plugins. Listeners are switched off during runs (the saved script is unchanged).
Details: app\docs\PATCH_23.3.23.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.23-perf-script-editor-20261105.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.23-perf-script-editor-20261105.zip -DestinationPath .\qa-patch-23.3.23 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.23\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.22 (cumulative): Performance Testing foundation
=============================================================================

File: qa-enterprise-ai-patch-23.3.22-perf-foundation-20261104.zip (for a PC on 23.3.21, 23.3.20 ... 23.3.11, or 23.3.7
to 23.3.10 with Faster-Whisper already installed). Includes 23.3.21.

* Performance Testing > Performance Scenarios (new page): create, edit, clone, archive and search performance
  scenarios (story, environment, script, run history). Existing performance plans each get a scenario.
* Target environments with allowed hosts and load limits; "Check before run" checks Java, JMeter, the plan, disk
  space, hosts and users before any request is sent.
* Run now queues the run (one at a time on this PC); Stop and Emergency stop keep partial results.
* Each run shows its execution state (Completed, Failed, Cancelled, Interrupted ...) apart from its verdict (Targets
  met, Targets missed, Target not specified, Inconclusive), and keeps config.json with exactly what ran.
* This PC's CPU, memory and disk are recorded during each run and flagged when overloaded (psutil 7.2.2 is
  installed offline from the patch's wheels folder).
* A run left running when the application closed or crashed is shown as Interrupted with its partial results.
Details: app\docs\PATCH_23.3.22.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.22-perf-foundation-20261104.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.22-perf-foundation-20261104.zip -DestinationPath .\qa-patch-23.3.22 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.22\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.21 (cumulative): Scheduled Runs, Outlook email reports, more recorded actions
==========================================================================================================

File: qa-enterprise-ai-patch-23.3.21-schedules-20261104.zip (for a PC on 23.3.20, 23.3.19, 23.3.18, 23.3.17, 23.3.16,
23.3.15, 23.3.14, 23.3.13, 23.3.12 or 23.3.11, or 23.3.7 to 23.3.10 with Faster-Whisper already installed).
Includes 23.3.20.

* Automation Studio > Scheduled Runs (new page): the scripts of AI-Driven, Guided Browser and Hybrid Test
  Automation with filters (page, application, name / test case, only scheduled). Tick scripts, then Schedule
  (once at a future date and time, every day, Monday to Friday, chosen days, or every few hours; until a date or
  continuing) or Run now. Choose browsers (each script runs in each), how many run at the same time, data rows,
  visible or hidden browser, Passed / Failed to TFS test runs, and the email report. Run history shows each result.
  The application must be running at the scheduled time; a missed time runs once when it opens.
* Settings > Outlook Email (new page): Microsoft 365 / Outlook.com / Exchange SMTP (password kept in the Windows
  Credential Manager) or the Outlook app on this PC. Send test email. The report goes after every run or only when
  something failed, with the HTML report and failure screenshots attached.
* Recording: double-click, right-click, hover menus, drag and drop, clicks inside iframes, pop-ups (OK / Cancel),
  new tabs, and "Click element: check text / check visible" are recorded and played back.
* Apply all healed steps. Edit Script: Record before this step / Re-record this step on each step.
Details: app\docs\PATCH_23.3.21.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.21-schedules-20261104.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.21-schedules-20261104.zip -DestinationPath .\qa-patch-23.3.21 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.21\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.20 (cumulative): Record / Pause when recording more steps
========================================================================================

File: qa-enterprise-ai-patch-23.3.20-recordpause-20261104.zip (for a PC on 23.3.19, 23.3.18, 23.3.17, 23.3.16, 23.3.15,
23.3.14, 23.3.13, 23.3.12 or 23.3.11, or 23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.19.

* Record more steps (Hybrid Test Automation) now opens the browser WITHOUT recording. Browse to the screen you need,
  then press Record (on the page, or on the Record / Pause bar at the bottom right of the browser window).
* Pause: browse freely, nothing is recorded. Resume: recording carries on.
* Insert at step N: the recorded steps go before step N. Or choose "replace this step (healing)" and "Replace up to
  step M" to re-record broken steps N to M.
* While paused you can change where the next steps go (Set position), so one session can add steps at several
  places and replace several steps. The steps table shows each part with where it goes.
* "Play the script's steps before this position first": the browser plays steps 1 to N-1 (with saved credentials and
  data row 1), then waits for you to press Record.
* Guided Browser Automation (a new script) still records at once, and now has Pause / Resume too.
Details: app\docs\PATCH_23.3.20.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.20-recordpause-20261104.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.20-recordpause-20261104.zip -DestinationPath .\qa-patch-23.3.20 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.20\APPLY_PATCH.ps1

Then press Ctrl+F5 in the application.

QA Enterprise AI - patch 23.3.19 (cumulative): Data button fix, date / time pickers as rules
=============================================================================================

File: qa-enterprise-ai-patch-23.3.19-databutton-20261104.zip (for a PC on 23.3.18, 23.3.17, 23.3.16, 23.3.15, 23.3.14,
23.3.13, 23.3.12 or 23.3.11, or 23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.18 and 23.3.17.

* 23.3.19: the Data button now opens its column list under the button (it was drawn out of sight), and the
  first click on Data or Rule after typing a value is no longer lost. Details: app\docs\PATCH_23.3.19.md

* Recording: picking a date in a calendar (icon, month, year, day) is saved as ONE date step on the field, named by
  its label (From Date), with a rule: Current system date +/- N days. A time picker gives Current system time +/-
  hours and minutes. OrangeHRM's yyyy-dd-mm format is read correctly.
* Playback: the calendar's month and year drop-downs and arrows are used; without a calendar the date is typed in
  the field's own format.
* Older scripts: "Replace with one date step" under calendar click pairs in Edit Script.
* Data button next to Rule: pick an Excel / CSV column for a step value (no typing of {{DATA:...}}).
Details: app\docs\PATCH_23.3.18.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.19-databutton-20261104.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.19-databutton-20261104.zip -DestinationPath .\qa-patch-23.3.19 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.19\APPLY_PATCH.ps1

Then press Ctrl+F5 and record the Leave List steps again (or use "Replace with one date step").

QA Enterprise AI - patch 23.3.17 (cumulative): date/time rules, file upload and download steps, Excel/CSV data runs
=====================================================================================================================

File: qa-enterprise-ai-patch-23.3.17-files-data-dates-20261103.zip (for a PC on 23.3.16, 23.3.15, 23.3.14, 23.3.13,
23.3.12 or 23.3.11, or 23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.16. Your AI server,
model, settings, scripts and data are kept.

* Dates and times follow the calendar. A date or time typed while recording, or written by AI-Driven Automation,
  becomes a rule: Current system date, +/- N days, current system time, +/- N hours and minutes. A script recorded
  on 3 Nov enters 5 Nov when it runs on 5 Nov.
* Edit Script: a Rule button next to each value (Today, Tomorrow, Yesterday, Now + 1 hour, Now + 4 hours, Now - 1
  hour, or any days / hours / minutes, plus or minus, any format), with what it gives now. Older scripts show
  "Make it dynamic" next to a fixed date.
* Fixed: recorded scripts ignored hours and minutes in a time rule.
* New steps: upload_file, download_file, expect_file_name, expect_file_text (PDF, Excel, CSV, Word, text) and
  expect_excel_cell (B2=Total). Recording picks up file uploads and downloads by itself. Files card in Edit Script.
* Test data (Excel / CSV) card in Edit Script: import, preview, then Run N row(s) = one run per row with a result per
  row. Use {{DATA:column}} in the step values.
Details: app\docs\PATCH_23.3.17.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.17-files-data-dates-20261103.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.17-files-data-dates-20261103.zip -DestinationPath .\qa-patch-23.3.17 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.17\APPLY_PATCH.ps1

Then press Ctrl+F5.

QA Enterprise AI - patch 23.3.16 (cumulative): Delete script on the automation pages
=====================================================================================

File: qa-enterprise-ai-patch-23.3.16-delete-20261103.zip (for a PC on 23.3.15, 23.3.14, 23.3.13, 23.3.12 or 23.3.11,
or 23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.15. Your AI server, model, settings and
data are kept.

* New Delete action on AI-Driven Automation > Script Inventory, Guided Browser Automation > Scripts and Hybrid Test
  Automation > Scripts, and on each script's own page.
* Every delete asks "Delete script?" first; only the Delete button deletes.
* A deleted script leaves the list, the Download zips, Play Selected and Run all. Its versions and evidence are kept
  (generated test files go to automation-workspace\.deleted, recorded scripts to scripts\_archived).
* A running script is not deleted.
Details: app\docs\PATCH_23.3.16.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.16-delete-20261103.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.16-delete-20261103.zip -DestinationPath .\qa-patch-23.3.16 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.16\APPLY_PATCH.ps1

Then press Ctrl+F5.

QA Enterprise AI - patch 23.3.15 (cumulative): AI-Driven Automation no longer times out
=========================================================================================

File: qa-enterprise-ai-patch-23.3.15-autogen-20261103.zip (for a PC on 23.3.14, 23.3.13, 23.3.12 or 23.3.11, or
23.3.7 to 23.3.10 with Faster-Whisper already installed). Includes 23.3.14. Your AI server, model, settings and
data are kept.

Why Generate Automation failed ("On-Premise AI unavailable: The On-Premise AI server did not answer in time"): all
290 selectors were sent to the AI for every Test Case and the application waited only 2 minutes. Now:
* the AI gets every page but only the elements that fit the Test Case (about 60 instead of 290);
* it waits up to the generation timeout (10 minutes), like test case and test data generation;
* the answer limit fits the number of steps and the AI server enforces the answer's shape;
* a call that still times out is asked once more with fewer elements;
* the Test Case log shows how many elements and tokens were sent.
Details: app\docs\PATCH_23.3.15.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.15-autogen-20261103.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.15-autogen-20261103.zip -DestinationPath .\qa-patch-23.3.15 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.15\APPLY_PATCH.ps1

Then press Ctrl+F5, open the failed job on AI-Driven Automation and press Retry on the failed Test Cases.

QA Enterprise AI - patch 23.3.14 (cumulative): new Dashboard and Air Force agent squadron
=========================================================================================

File: qa-enterprise-ai-patch-23.3.14-squadron-20261102.zip (for a PC on 23.3.13 or 23.3.12 or 23.3.11, or 23.3.7
to 23.3.10 with Faster-Whisper already installed). Includes 23.3.13. Your AI server, model, settings and data are kept.

* Dashboard: Azure DevOps and Performance tiles kept; new QA progress bars and pie/bar charts for user stories,
  acceptance criteria, test cases, test data, defects and pass/fail. Agent cards and Recent Runs removed.
* AI Voice Command Center: only the Agent Squadron panel removed (mic and radar, Command Handoff, Radio
  Configuration stay).
* New Air Force squadron agents (pilots and combat drones) everywhere.
* Agent Workflow: defense backdrop, the running agent blinks, Remaining and Total time on every card.
Details: app\docs\PATCH_23.3.14.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.14-squadron-20261102.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.14-squadron-20261102.zip -DestinationPath .\qa-patch-23.3.14 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.14\APPLY_PATCH.ps1

Then press Ctrl+F5.

QA Enterprise AI - patch 23.3.13 (cumulative): test data on the TFS "Test Data" tab
====================================================================================

File: qa-enterprise-ai-patch-23.3.13-datatab-20261102.zip (for a PC on 23.3.12 or 23.3.11, or 23.3.7 to 23.3.10
with Faster-Whisper already installed). Includes 23.3.12. Your AI server, model, settings and data are kept.

When the Test Case type has a custom "Test Data" field (the "Test Data" tab, with "Test Data ID"), the test data
is now written there as well as in the Summary:
* new test cases created by AI Test Design: their test data lines as a Test Data Matrix, Test Data ID US<story>-TC<n>;
* Test Data page > Write to TFS (and the voice command): the generated rows, Test Data ID = the dataset key.
A field TFS refuses (for example a numbers-only Test Data ID) is left out and the rest is written.
Test cases created before this patch: open the Test Data page, select the test case and press Write to TFS.

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.13-datatab-20261102.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.13-datatab-20261102.zip -DestinationPath .\qa-patch-23.3.13 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.13\APPLY_PATCH.ps1

Then press Ctrl+F5.

QA Enterprise AI - patch 23.3.12 (cumulative): test data with a small local model
==================================================================================

File: qa-enterprise-ai-patch-23.3.12-testdata-20261102.zip (for a PC on 23.3.11, or 23.3.7 to 23.3.10 with
Faster-Whisper already installed). Your AI server, model, settings and data are kept.

Why test data failed: the model (qwen2.5-1.5b) wrote about 5 minutes of answer for "rows 1 to 4" in a form the
application could not read; a second "repair" call and the retry with 1 row failed the same way.
Now:
* the AI server (llama-server) is given the exact JSON shape, so the model can only write a valid answer, and
  it stops as soon as the rows are written (a server that does not support this gets plain JSON mode);
* short answer keys and short texts: about half the tokens per row, so each AI call is quicker;
* 3 rows per AI call; each row is checked on its own (a broken row is left out, the others are kept); an answer
  cut off part way keeps its complete rows; no second "repair" call;
* prose, code fences and trailing commas around the JSON are accepted.

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.12-testdata-20261102.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.12-testdata-20261102.zip -DestinationPath .\qa-patch-23.3.12 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.12\APPLY_PATCH.ps1

Then press Ctrl+F5 and generate the test data again. The event log shows "writing ... data rows 1 to 3 of up
to 12", then 4 to 6, and so on. Details: app\docs\PATCH_23.3.12.md

QA Enterprise AI - patch 23.3.11 (cumulative): the application runs the local AI server
====================================================================================

23.3.11: your own llama-server (4 slots of 2,048 tokens) reads prompts at 2 to 8 tokens/s and re-read the whole
1,700-token prompt on most calls. Run the patch with -UseAppServer (your model) or -FastModel (the 4B model): it
stops your llama-server and the application starts it with one slot of 8,192 tokens, which keeps what it has
read; the prompt is ordered so each next call reads only the new criterion. Fixes the "Unexpected UTF-8 BOM"
error. Do not start your own llama-server any more after this. Details: app\docs\PATCH_23.3.9.md (23.3.11).


23.3.10: with 23.3.9, two parallel AI calls failed after about 2.5 minutes with HTTP 500 "Context size has been
exceeded" because your AI server shares one small context between its slots. Now two calls run at a time only when
each gets at least 8,192 tokens, an answer that runs out of context is written again one call at a time, the answer
limit never exceeds the context left, and the run log shows the AI context per call. Everything below still applies.


File: qa-enterprise-ai-patch-23.3.11-appserver-20261031.zip
For a PC that runs 23.3.7 or 23.3.8 (also 23.2.2, 23.3.0 to 23.3.6). No administrator rights. It includes 23.3.8
(no more "The AI server returned HTTP 400") and 23.3.7-r5 (Faster-Whisper with the resumable download and the
reserved-port fix). An installed Faster-Whisper and its model are kept. Settings, wake name, TFS connection, AI
settings, database and reports are kept.

Why it was slow: your AI server gave each request only about 2,300 tokens and wrote 2.5 to 7 tokens per second
(a 7B/8B model on the CPU), so US1177 took one 72 to 217 s call per criterion, one after the other.
Now: the AI server started by the application gets 2 parallel slots with 8,192 tokens each, the GPU when it has
one, no hidden "thinking" text; 2 AI calls run at a time; cases are shorter. Optional: a model twice as fast.
Details: app\docs\PATCH_23.3.9.md

Install (Windows PowerShell)
----------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.11-appserver-20261031.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.11-appserver-20261031.zip -DestinationPath .\qa-patch-23.3.11 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.11\APPLY_PATCH.ps1 -FastModel

   Or keep your current model:  ...\APPLY_PATCH.ps1 -UseAppServer

   -FastModel also switches the local model to Qwen3-4B-Instruct-2507 (downloads about 2.5 GB into Downloads,
   resumable, SHA-256 checked). Leave -FastModel out to keep your current model and only get the speed settings.
   (Or extract the zip and double-click APPLY_PATCH.cmd, which does not switch the model.)

What you should see
-------------------
* "Installed version  : 23.3.8  ->  23.3.9"
* "== Local AI server": "Stopped the AI server the application started before ...", "AI speed settings: ...",
  (with -FastModel) "Local AI model: Qwen3-4B-Instruct-2507-Q4_K_M.gguf ...", and how the AI server is set up.
  If it says "the application does not start the AI server itself", see PATCH_23.3.9.md for the llama-server line.
* "Faster-Whisper packages already installed" and the voice check, as in 23.3.7.
* "== On-Premise AI check with the writing speed": Parallel Requests 2, Context Window 8192 (or more), and
  "Generation Speed  N tokens/s; one test case takes about X s to write, 2 written at a time".
Then press Ctrl+F5 and generate the US1177 test cases again. The event log shows "10 AI calls, 2 at a time" (or 5).

Please send a screenshot of the AI check lines: they show whether the GPU is used and how fast the model writes.

Undo: stop the application and copy the folders from <install>\patch-backups\23.3.8-to-23.3.9-<time>\ back into
<install>\app, and copy <data>\settings.before-23.3.9.json over <data>\settings.json.
Log: <install>\logs\patch-23.3.9-<time>.txt


------------------------------------------------------------------------------------------------------------------
Included from 23.3.7-r5 (unchanged):

QA Enterprise AI - patch 23.3.7: Faster-Whisper speech recognition
==================================================================

For a PC that runs 23.2.2 (after the cumulative 23.2.2 patch) or 23.3.0 to 23.3.6; the earlier 23.3 patches are
not needed first. No administrator rights. Settings, wake name, TFS connection, On-Premise AI, database and reports
are kept.

It replaces the recognizer that wrote down the wrong words with Faster-Whisper, which runs on this PC without
Internet, starts with the application on http://127.0.0.1:8766, and stops with it. Check Voice Setup now really
tests the speech server (HTTP 404 is no longer shown as PASS).

The Python packages are in this zip. The speech model (small.en, about 465 MB) is not, because of its size:
- if this PC has Internet, the patch downloads it from Hugging Face and checks every file's SHA-256;
- if it has no Internet, make the model zip on any PC with Internet first (step A), then install (step B).

A. Only for an offline PC: make the model zip on a PC with Internet (Windows PowerShell)
---------------------------------------------------------------------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.7.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.7.zip -DestinationPath .\qa-patch-23.3.7 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.7\GET_FASTER_WHISPER_MODEL.ps1 -OutDir $env:USERPROFILE\Downloads

   It makes Downloads\faster-whisper-small.en.zip. Copy that file into Downloads on the offline PC.

B. Install on the PC that runs QA Enterprise AI (Windows PowerShell)
--------------------------------------------------------------------

     cd $env:USERPROFILE\Downloads
     Unblock-File .\qa-enterprise-ai-patch-23.3.7.zip
     Expand-Archive .\qa-enterprise-ai-patch-23.3.7.zip -DestinationPath .\qa-patch-23.3.7 -Force
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.7\APPLY_PATCH.ps1

   (Or extract the zip and double-click APPLY_PATCH.cmd.)
   It finds faster-whisper-small.en.zip in Downloads, Desktop, Documents or next to the patch by itself. Elsewhere:
     powershell -ExecutionPolicy Bypass -File .\qa-patch-23.3.7\APPLY_PATCH.ps1 -ModelZip "D:\faster-whisper-small.en.zip"
   Never use the Internet:  add -Offline.     A faster, less accurate model:  add -Model base.en

What you should see
-------------------
* "Faster-Whisper packages installed ... CPU int8: True"
* "Model small.en installed ..." and a self-test: a Windows voice says "Captain, open Test Plans." and the line
  "Heard: ..." shows what Faster-Whisper wrote down.
* "Faster-Whisper: READY. It starts with the application on http://127.0.0.1:8766"
* "Running: http://127.0.0.1:8765/ version 23.3.7" and the voice check:
     Faster-Whisper speech recognition   PASS   Model small.en (int8, cpu) on http://127.0.0.1:8766 ...

Then press Ctrl+F5 once in the browser and click anywhere on the page once.
* The SPEECH badge at the top reads FASTER-WHISPER; Voice & Command Agent shows Speech recognition
  "Local speech server (Faster-Whisper)" and Server type "Faster-Whisper on this PC".
* Say "Captain." -> "Yes, Boss. I'm listening."  Then "Go to Test Plans."  The comms log shows what was heard.
* CHECK VOICE SETUP: "Faster-Whisper speech recognition  PASS".

If it says "Faster-Whisper: NOT READY", your previous speech setting was left as it was and the rest of 23.3.7 is
installed. Do step A, then run step B's last line again. If the packages do not load ("DLL load failed"), install
the Microsoft Visual C++ Redistributable 2015-2022 x64 (vc_redist.x64.exe) and run it again.

The script stops the application, backs up the files it replaces to <install>\patch-backups\, copies the 23.3.7
files, records them in MANIFEST.json, installs Faster-Whisper into <install>\app\runtime\faster-whisper, points the
voice settings at it (a copy of the old settings is kept as <data>\settings.before-23.3.7.json), starts the
application and checks http://127.0.0.1:8765/api/health and the voice setup. If anything fails while application
files are replaced, the old files are restored automatically. Log: <install>\logs\patch-23.3.7-<time>.txt
The Faster-Whisper server writes <data>\logs\faster-whisper.log.

Details: app\docs\PATCH_23.3.7.md (and PATCH_23.3.0.md to PATCH_23.3.6.md). Test evidence: tests\TEST_RESULTS.txt
(the tests in tests\ are not installed).

Undo: stop the application, copy the folders from <install>\patch-backups\<version>-to-23.3.7-<time>\ back into
<install>\app, and copy <data>\settings.before-23.3.7.json over <data>\settings.json. The folder
<install>\app\runtime\faster-whisper can then be deleted.
