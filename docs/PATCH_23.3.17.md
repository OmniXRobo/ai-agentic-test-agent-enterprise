# Patch 23.3.17: date and time rules, file upload and download steps, data-driven runs

Cumulative: includes 23.3.16 and everything before it. No database change. Your AI server, model, settings, scripts
and data are kept.

## 1. Dates and times that follow the calendar (run-time test data)

A script recorded on 3 Nov 2026 used to keep "03/11/2026". Played on 5 Nov, the application refused the past date.
Now dates and times are **rules**, worked out from the Windows system clock of the PC when the test runs:

| Rule | Means | On 5 Nov 2026 at 14:00 |
|---|---|---|
| `{{TODAY:%d/%m/%Y}}` | current system date | 05/11/2026 |
| `{{TODAY+1d:%d/%m/%Y}}` | current system date + 1 day | 06/11/2026 |
| `{{TODAY-3d:%d/%m/%Y}}` | current system date - 3 days (past date) | 02/11/2026 |
| `{{NOW:%H:%M}}` | current system time | 14:00 |
| `{{NOW+1h:%H:%M}}`, `{{NOW+4h:%H:%M}}` | current time + 1 / + 4 hours | 15:00, 18:00 |
| `{{NOW+2h+30min:%H:%M}}` | + 2 hours 30 minutes | 16:30 |
| `{{NOW-1h:%H:%M}}`, `{{NOW-45min:%I:%M %p}}` | past time | 13:00, 01:15 PM |

* **While recording** (Guided, Hybrid, Discovery): a value typed into a field that is a date, a date and time, or a
  time becomes a rule. The same date as the recording day = Current system date; another date = Current system
  date +/- the days between; a time = Current system time +/- the hours and minutes from the moment it was typed.
  The format typed is kept (03/11/2026, 2026-11-03, 3 Nov 2026, 10:30, 10:30 AM, date-time fields). Dates more
  than a year away (a date of birth) stay as typed. Checks (expect_text, expect_value) keep their values.
* **AI-Driven Automation**: fixed dates and times in the generated test data become rules; the Test Case log lists
  each one. The AI is also told never to write a fixed date or time. The classic AI generation does the same.
* **Edit Script**: a **Rule** button next to the Value of every typing or checking step: Current system date, date
  and time, or time; No change / Plus (future) / Minus (past); days, months, hours, minutes; the format. Quick
  buttons: Today, Tomorrow, Yesterday, Today + 7 days, Now, Now + 1 hour, Now + 4 hours, Now - 1 hour, Now + 30 min.
  Under each rule: what it gives now and the recorded value. A fixed date in an older script shows
  "Make it dynamic: ..." to convert it in one click. Save to keep the change.
* AI-Driven Automation > Test Data tab: the same Rule dialog.
* **Fixed**: recorded scripts ignored hours and minutes (`{{NOW+1h}}` was typed as text). They now work in the
  runner and in the exported Python tests.

## 2. File upload and download steps

New step actions in Edit Script:

* `upload_file`: Value = the file name(s) from the script's **Files** card (several: `a.pdf; b.png`), or a full path.
  Works on a file field and on a button or drop zone that opens the file dialog.
* `download_file`: clicks the element and saves the file in the run's evidence (`evidence\<run>\downloads`).
  Value = save-as name (optional).
* `expect_file_name`: the downloaded file name contains the Value (`.pdf`, `Invoice`).
* `expect_file_text`: the downloaded file contains the Value; `||` for several texts. Reads PDF, Excel (.xlsx),
  CSV, Word (.docx), text, JSON, XML and HTML. Case and extra spaces are ignored.
* `expect_excel_cell`: `B2=Total` or `Summary!C5=10` (numbers compare by value).

While recording, a file you pick is copied to the script (up to 50 MB) and becomes an `upload_file` step; a click
that downloads a file becomes `download_file` followed by a check of the file type. Files are in
`data\automation\<script id>\files` and go into **Download Python tests (.zip)** (`tests\files\...`).

PDF text is read with pypdf 6.21.4 (BSD licence), shipped in `qa_platform\vendor` (no installation needed).

## 3. Data-driven runs from Excel or CSV

Edit Script > **Test data (Excel / CSV)** > Import: the first row holds the column names, each other row is one
run. A preview shows the rows, the columns (`{{DATA:column}}`), the `{{DATA:...}}` the steps use but the file lacks,
and the sheet to use. "Make dates and times dynamic" (on by default) turns dates and times in the cells into rules;
a cell can also hold a rule itself. Old .xls files: save as .xlsx or CSV first. Up to 1,000 rows.

**Run N row(s)** runs every ticked row, one after the other, each as its own run with its own evidence; the
results table shows PASSED / FAILED per row with the run and the error, and **Stop** ends the remaining rows. The
plain **Run** button uses the first row. The exported Python test has one test per row.

## Files
* `qa_platform/automation/date_rules.py` (new), `files.py` (new), `vendor/pypdf` (new)
* `qa_platform/automation/dsl.py`, `runner.py`, `recorder.py`, `js/recorder.js`, `store.py`, `service.py`,
  `generator.py`, `pytest_export.py`, `framework/studio.py`, `qa_platform/api/automation.py`
* `web/js/date-rules.js` (new), `web/js/pages/automation.js`, `web/js/pages/script-studio.js`, `web/css/app.css`
* `tests/test_files_data_dates_23317.py` (new)
