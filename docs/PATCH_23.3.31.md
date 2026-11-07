# Patch 23.3.31: step screenshots and AI analysis reports with PDF and Outlook email

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.30.

## What is new

**A screenshot after every step (Recent Runs)**

* A run now saves the page after every step (`step-01.png`, `step-02.png` ... in the run's evidence folder), as well as
  the failure screenshot and the final page it already saved. The screenshot is taken after the step has finished and
  shows the tactical cursor where the step acted.
* AI-Driven Automation and Guided Browser Automation > a script > **Recent runs** (and Execution History > a run) show
  a **Screenshot** column with a small image on each step's row. Click it to open it large, with **Previous** /
  **Next** (or the left and right arrow keys) and **Open full size**. Other evidence (the final page, self-healing,
  screenshot steps) is shown under the table as before.
* Runs made before this patch have only their failure and final screenshots; the table says so.
* Each screenshot adds about 0.1 to 0.3 seconds to a step. It can be turned off with `playback.step_screenshots: false`
  in settings.json.

**AI analysis report of a run (AI-Driven Automation, Guided Browser Automation, Execution History)**

* Open a run in Recent Runs and press **Generate AI analysis report**. The On-Premise AI is given the steps, their
  results and times, errors, self-healed locators and the script's ten earlier results (passwords are never in them),
  and writes a summary, observations, issues and risks, recommendations, and what cannot be told from the run.
* **Edit** changes the wording (one point per line); **Analyze again** asks the AI for a new one. The report says when
  it was edited.
* **Generate PDF** makes the PDF from the analysis as it is now: the result, run details, the AI analysis, and every
  step with the screenshot of the page after it. **Open PDF** and **Download PDF** (`<run id>-ai-analysis.pdf`).
  When the analysis is edited or made again after the PDF, the PDF is marked out of date and made again before it is
  emailed.
* **Email PDF** (its own button): see *Email* below.

**Performance Results: AI performance analysis**

* The card keeps **Analyze with On-Premise AI** / **Analyze again** and adds **Edit**, **Generate PDF**, **Open PDF**,
  **Download PDF** (`<run id>-ai-performance-analysis.pdf`) and **Email PDF**. The PDF has the run details, the headline
  figures, the AI analysis, the requests table and the SLA checks. The full run report (23.3.28) is unchanged.

**Dashboard: AI dashboard analysis**

* A card at the top of the Dashboard: **Generate AI analysis** reads the Dashboard figures (TFS user stories, test
  cases and open defects, acceptance criteria, automation coverage, test data, latest pass / fail, executions,
  defects and drafts, performance runs and SLA) and writes what they show, the gaps and risks, and next steps in
  priority order. A figure that is not available is given to the AI as missing, never as zero.
* **Edit**, **Generate PDF** (the analysis and the figures it was given), **Open / Download PDF** and **Email PDF**.
  The card is outside the figures, which reload when work finishes, so an edit in progress is not lost.

**Email**

* **Email PDF** opens a form with **To** (the default recipient from Settings > Outlook Email), **Cc**, the **Subject**
  and the **Message** already written from the analysis: the result, the summary, the key figures, up to three key
  points and three next steps. The subject starts with the result, e.g. `[PASSED] Test run analysis: login
  (RUN-...)`, `[Targets met] Performance analysis: ...` or `QA dashboard analysis: <project> (07 Nov 2026)`.
* Everything can be changed before **Send**. The PDF is attached (made first if it is missing or out of date). The
  mail goes through Settings > Outlook Email (SMTP / Microsoft 365, or the Outlook app on this PC). If Outlook Email
  is not set up yet the form says so, with a link. Nothing is sent until you press Send.
* The subject and message are written from the analysis without asking the AI again, so the form opens at once.

All of this works offline: the local AI (qwen2.5) writes the analysis and the bundled Chromium prints the PDF (Edge or
Chrome if it cannot start, as in 23.3.30).

## API

* `GET /api/ai-reports/{kind}/{id}`: `kind` is `run` (a functional run id), `perf` (a performance run id) or
  `dashboard` (id `current`); returns `analysis`, `has_pdf`, `pdf_made_at`, `email_configured`
* `POST /api/ai-reports/{kind}/{id}/analyze`: the AI writes the analysis
* `PATCH /api/ai-reports/{kind}/{id}`: `summary`, `observations`, `issues`, `recommendations`, `undetermined`,
  `suspected_bottlenecks` (perf, "Area (medium confidence): evidence")
* `POST /api/ai-reports/{kind}/{id}/pdf`, `GET /api/ai-reports/{kind}/{id}/pdf[?download=true]`
* `GET /api/ai-reports/{kind}/{id}/email`: the draft (to, cc, subject, body, attachment name)
* `POST /api/ai-reports/{kind}/{id}/email` {to, cc, subject, body}
* Files: `<data>\reports\ai\<kind>\<id>\` (`analysis.json` for runs and the dashboard, `report.html`, `report.pdf`).
  The performance analysis stays on the run, as before.

## Tests

`tests/test_ai_reports_23331.py`: a screenshot after each of the 8 steps of a real Chromium run, kept with its step;
the run analysis prompt (steps, result, no password), an edit, the PDF with the edited text and all 8 screenshots,
download name, the PDF out of date after an edit and made again for the email; the email draft (default recipient,
subject, body) and the sent mail with the edited subject and the PDF attached; the dashboard analysis with its figures,
PDF and email; the performance analysis shared with the run page, a bottleneck edit, its PDF and email; invalid kinds,
ids and runs; no PDF or email before an analysis; email refused when Outlook Email is not set up.

The pages were checked in Chromium at desktop and phone width: AI-Driven Automation and Guided Browser Automation
Recent Runs with step screenshots and the large view, the run report panel (analysis, edit, PDF, email form),
Performance Results and the Dashboard.

Not tested here: sending through a real Outlook / Microsoft 365 account (the mail is checked as it would be sent), and
the speed of the analysis with qwen2.5 on a PC without a GPU (expect up to a minute).
