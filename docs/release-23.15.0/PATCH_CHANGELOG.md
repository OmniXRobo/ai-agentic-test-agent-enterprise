# Changelog: SkyGuard QA 23.15.0

Cumulative on 23.14.1 (commit `cd0a585`). One upgrade for every release from 23.3.35 to 23.14.1. Everything works
offline: nothing is downloaded during the install or at run time. The main database is unchanged (still
**23 migrations**); the agent's knowledge database gains **migration 3** (what the agent remembers and the references
to the codes it learned). The browser extension becomes **1.5.0** with the same extension ID. The Arabic voice, the
speech model and the voice settings are kept exactly as installed. Earlier changes: `previous-23.14.1/`,
`previous-23.14.0/` ...

## Create TFS Defect works from every result

Reported on 23.14.1: "Create TFS Defect" answered **"Request validation failed"** on Vulnerability Scanning,
Execution History (a failed run and a run stopped by the user) and Performance Results. Root cause and the exact
request that failed: `ROOT_CAUSE_ANALYSIS.md`.

* **The page's own values are accepted.** Severity and priority are sent by the page as numbers (1 to 4); the API
  declared them as text and refused every request before it reached the defect code. Numbers, the TFS labels
  ("2 - High") and plain words ("high") are all accepted now, and a value outside 1 to 4 gets a message that names
  the field instead of "Request validation failed".
* **The defect reads like a defect.** The steps are numbered with their result, and the body carries Expected result,
  Actual result, the error, the environment (browser, address, script and run) and the evidence list. Before, the body
  held an escaped JSON dump (`&quot;steps&quot;: [{...`).
* **The evidence is attached.** The run's screenshots (the failed step first), its trace, video, report and result
  files are listed on the preview with a tick box and uploaded to the work item when the defect is published.
* **A run stopped by the user** is a defect subject like a failed run: the body says it was stopped, at which step.
* **Performance results make defects whether they passed or failed.** The body carries the samples, the error rate,
  the average and 95th percentile, the throughput, the peak users and every SLA check with its limit and actual
  value; `qa-report.pdf` and `metrics.json` are attached.
* **"Approve and send now"** creates the bug in TFS in one step (it still goes through Defect Review: the draft is
  approved and published, and a duplicate is reported instead of silently creating a second bug). Review first,
  approve, publish works unchanged, and the failure path keeps the draft so nothing is lost.
* Local file paths are no longer part of the answer the page receives.

## The Workflow Operator (what 23.14 and earlier called the AI Agent)

The page and the extension are renamed to say what they do. The route (`#ai-agent`) and the extension ID do not
change, so saved links and an installed extension keep working.

* **One preview, then "yes".** A typed or spoken "yes", "go ahead", "run it", "نعم", "نفذ", "تأكيد" or "موافق" runs
  the preview it answers. A chat that waited at WAITING after a preview was confirmed now releases its browser window
  to the next step and picks the run up in the same window.
* **It stops asking what it already knows.** What you typed while teaching, and the values you used last time, fill
  what a new request leaves unsaid; the preview says where each value came from ("while teaching", "your usual
  value"), and your own last values win over the taught ones.
* **One request, many records.** "Create 10 trips with the same data, one per day, drivers: A, B, C" becomes 10
  records with 10 different dates and the drivers in turn, in one preview, without 10 questions.
* **Your usual requests are remembered** and offered as suggestions, with how often you used them.
* **The sign-in password and an authorization code typed while teaching are kept** in the credential vault
  (Windows Credential Manager / DPAPI), masked everywhere as `********`, and a learned code is filled in by the run
  instead of pausing for it. A one-time code (a field the browser marks `one-time-code`) is never captured.

## Voice, in the language you speak

* **Arabic that speech recognition misspells is understood.** "إفتاء الإشارات" (open notifications) opened nothing and
  answered "this operation is not supported"; a word within a letter or two of a known command word, and no other, is
  now taken as that word. The same for English said with a slip ("dashbord", "execution histry", "notification").
* When the local AI's English translation drops the page that was named, the built-in word list is tried before the
  command is called unsupported.
* **"English and Arabic" detects Arabic.** When the recognizer is unsure of the language, or reports Arabic without a
  single Arabic letter, the audio is transcribed again as Arabic and the better result is used.
* **"Listen always"**, in the voice popup and in the extension's chat: each spoken request is transcribed and run with
  no button in between, and the reply is spoken in the language you spoke. The microphone is released between
  requests, listening stops when you press Stop listening or leave the chat, and a request that changes records still
  waits for the confirmation under its preview.
* The Workflow Operator page can be opened by voice under either name.

## Also

* The agent's knowledge database is migrated in place (migration 3) with its data kept; Setup verifies the schema,
  both databases' integrity and 13 feature probes, including a new 23.15.0 probe.
* The user manual, the extension documents and the installer text carry the new name.
