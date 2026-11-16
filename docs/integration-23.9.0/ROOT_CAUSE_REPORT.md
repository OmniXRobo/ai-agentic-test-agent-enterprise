# SkyGuard QA 23.9.0: root-cause report

Integration Comparison, offline OCR, live capture, mapping, calculations, charts, results, filters and browser windows.

Baseline: 23.8.0, the merge of PR #53 (commit `c838bf8`). The line numbers below are from that commit
(`git show c838bf8:<file>`). Each cause was found by reading the code and reproducing the failure on local fixture
pages before any change was made. The application was not rebuilt; every fix is in the existing modules.

**About the evidence.** The screenshots mentioned in the request were not in the project files. The `image*.png`
files in the repository root are older, unrelated Record & Play pages. I built representative fixture pages instead
(`tests/site/integration_ocr`, `tests/site/integration_live`). Chromium renders them, and the real offline OCR engine
reads the result. The expected values come from `truth.json`, which `build_fixtures.py` computes with Decimal; the
application never reads that file. No rule in the code is tuned to one of these pages.

## 1. Screenshot OCR and table extraction (Part 1)

| # | Symptom | Root cause (23.8.0) | Fix (23.9.0) |
|---|---|---|---|
| 1.1 | "No offline OCR engine is available" on most PCs | `qa_platform/comparison/ocr.py` `engines()` (l.70) only knew Windows OCR and a Tesseract on PATH. No engine was shipped. `read()` (l.135–145) failed when neither was present. | A bundled offline engine: RapidOCR 3.10 with ONNX Runtime. The models are inside the wheel, so nothing is downloaded. It is unpacked into `runtime\ocr` or `%LOCALAPPDATA%\SkyGuardQA\ocr` and runs in a separate worker process (`ocr_worker.py`). The order is configurable (`comparison.ocr_engine`: auto / rapidocr / windows / tesseract), and `diagnose()` plus `GET /api/comparison/workspace/ocr/diagnostics` explain each engine. |
| 1.2 | Windows OCR "not available" although Windows 10/11 has it | `engines()` used `shutil.which("powershell")`, and the launcher starts the app with a short PATH. The call also passed `-ExecutionPolicy Bypass` (l.75). | `_powershell()` falls back to `System32\WindowsPowerShell\v1.0`. The `-ExecutionPolicy` flag is removed (the script runs through `-Command`, and no security setting is weakened). Constrained Language Mode and `OcrEngine.MaxImageDimension` produce clear messages. |
| 1.3 | Two columns read as one ("City Product"), repeated words | `screen_reader.py` built column bands from header cells merged by a gap of up to 0.95 × line height. Two header words close together became one band, and every body word in it was appended with `rec[name]["value"] += " "` (l.160). | Columns come from the x coverage of body words, using slightly shrunk boxes. A gutter is a gap that no line crosses. Header words are assigned to bands by overlap. |
| 1.4 | The table stops at the first row with an empty cell | `_fits()` (l.104) required a row to fill at least half the bands. | Rows belong to a table by vertical segment, not by cell count. A missing cell gives the table status PARTIAL; the table does not end. |
| 1.5 | "Total" / "Grand Total" rows counted as records; totals never checked | No total detection. | A TOTAL regex detects total, subtotal and grand total rows. They go to `totals`, are checked against the column sums (Decimal), and are offered as values named "Line Total (total)". |
| 1.6 | Page titles and KPI cards merged into the table header | Every text line above the numbers was taken as header. | Segmentation: a single-cell title line is dropped, at most 3 header lines are allowed, a large gap or a larger font starts a new block, and fewer than 2 body rows leaves the block to the KPI reader. |
| 1.7 | "(AED)" read as part of the field name; dark themes unreadable | The unit was not parsed; there was no preprocessing. | `_header_name` separates units (AED, %, other currencies). The worker upscales small images, inverts dark images for Windows/Tesseract and tiles very large ones. |
| 1.8 | Charts in screenshots ignored | The reader handled text only. | `ocr_worker.graphics` finds bars, legend swatches and pie/donut slices (OpenCV). `screen_reader` turns them into charts with labelled values, values read from the axis (approximate, NEEDS_REVIEW) and pie shares checked against 100%. |
| 1.9 | Text read upside down ("Dubai" → "uqna") | The RapidOCR angle classifier rotated short words. | `use_cls` is off; screenshots are upright. |
| 1.10 | No extraction status | A screenshot was only "read" or "nothing_found". | Status per table, chart and input: COMPLETE / PARTIAL / FAILED / NEEDS_REVIEW, with the issues listed. It is shown on the application card and in the report. |

## 2. Live URL capture returning 0 records (Part 2)

| # | Root cause | Fix |
|---|---|---|
| 2.1 | `extract_web.py` `_route()` aborted every request to a host that was not in the allowed list. Scripts and APIs from a CDN or an API host never loaded, so JavaScript tables stayed empty: 0 records. | A request policy. A main-frame navigation is allowed only to the application's own host, site or approved hosts. Public sub-resources (scripts, styles, XHR) are allowed. Private-network addresses are blocked unless the application itself is on the intranet. `strict_hosts` restores the 23.8.0 rule. Blocked requests and redirects are reported. |
| 2.2 | There was no wait for data. The table was read before the XHR answered. | `wait_for_data()` polls for rows or chart data (`comparison.data_timeout_ms`). |
| 2.3 | `TABLE_JS` (l.33) read only `td` in `tbody`. It missed `th` in body rows, colspan/rowspan, iframes, shadow DOM, ARIA and div grids. | A new `TABLE_JS` covers frames, shadow roots, a grid matrix with colspan/rowspan, multi-level headers, ARIA grids (with rowIndex/rowCount for virtual scrolling), div grids, the DataTables API (all rows, not just the visible page) and totals in `tfoot`. |
| 2.4 | `expected_total()` (l.177) read "Show 50 entries" as 50 records. | It prefers "Showing … of N" and ignores the page-size `select`. |
| 2.5 | `capture()` started two separate jobs ("table" and "dashboard"), each with its own browser and login. A failed job was hidden in `app_data`. | One "page" job in one session: tables, KPIs, charts, a screenshot and OCR fallback (no records, or canvas charts). The input status is shown, and failures are explained instead of hidden. |
| 2.6 | `http_credentials` (l.238) were sent to every origin. | Restricted to the application's own origin. |
| 2.7 | Canvas charts were invisible to the DOM reader. | CHARTS_JS adds ApexCharts, d3 `__data__` and aria-label charts. Canvas without library data falls back to a screenshot and OCR. |

## 3. Field mapping and Qwen output (Part 3)

| # | Root cause | Fix |
|---|---|---|
| 3.1 | Mapping compared names only. Territory/Region, Order Volume/Orders and Share %/Contribution % were not linked. Quantity and Amount could be matched because their values looked alike. | New `semantics.py`: a field's concept (id, location, date, status, quantity, money, percent …), its money role (unit price, subtotal, discount, tax, final, sales) and its unit, read from the label, type and values. `_pair_score` raises the score for the same meaning and caps it for a different concept (0.30), a different unit (0.35) or a different money role (0.40). Total vs Final Amount is capped at 0.70 unless values agree on at least 2 records. The vocabulary is extensible in Preferences. |
| 3.2 | "did not return valid structured output" | `mapping.py` `refine_with_ai` (l.208) called `complete_json(..., max_tokens=1500)` without a grammar. `AIMapping.confidence` was `Field(ge=0, le=1)` (l.175), so Qwen's `85` or `"85%"` failed the whole answer, as did a bare list or `source`/`target` keys. | `enforce_schema=True` (llama-server grammar). Lenient validators accept 85 → 0.85, "high", a bare list and alias keys. Batches of 20 fields. An invalid batch only loses the AI's help for that batch: the deterministic suggestions stand and a log entry gives the error code without sample values. AI mappings are always "needs review" and capped at 0.79. Invented field names are ignored. |
| 3.3 | A saved mapping stayed approved after the application changed a column's unit or type. | Each saved member stores a signature (type, unit, concept). A change sets the row to needs_review with the reason, and a unit difference at run time is NOT COMPARABLE. |

## 4. Calculations (Part 5)

| # | Root cause | Fix |
|---|---|---|
| 4.1 | `formula.suggest_total` (l.273–277) tried SUM, COUNT, AVG, **MAX** and MIN on every column and kept whichever matched the displayed total. A grand total equal to the largest value became "MAX(...)". | `semantics.aggregate_allowed`: MIN/MAX only for "lowest/highest" style labels, AVG only for averages, COUNT for counts, SUM for totals. |
| 4.2 | `formula.suggest` (l.199) combined any numeric fields, including identifiers and percentages added to amounts. | `_meaningful()` uses `can_combine` / `result_kind` / `compatible_target`. `a − b + c` is allowed only when b is a discount and c is a tax. Same-application rules are allowed only for derived targets (final, subtotal, percent). All arithmetic is in Decimal. |

## 5. Charts (Part 6)

| # | Root cause | Fix |
|---|---|---|
| 5.1 | `workspace._suggest_charts` (l.1167) picked the value column with only a 0.1 hint from the title. A "Sales by City" chart was checked against Quantity. | The value field is chosen by meaning (money for sales, quantity for orders, percent for shares). The categories must overlap. Pie and donut charts are checked as shares of the total, and summary tables with Share % are recognised. |
| 5.2 | Values read from an axis (no label) were compared as exact. | They are marked approximate. Within the axis tolerance the result is NEEDS REVIEW ("approximate_match"); outside it, FAIL. |

## 6. Results (Part 7)

`compare()` knew PASS / PASS* / FAIL / WARNING only. A unit mismatch or a filter-scope mismatch was a FAIL or a
WARNING, and a run with no comparable rows was a PASS. Fixed with:

* the six statuses, each mapped from its category (`CATEGORY_RESULT`), with PASS* → PASS + `normalized`;
* an overall result in this order: FAIL, INCOMPLETE, NEEDS REVIEW, WARNING, PASS;
* "no rows" → INCOMPLETE;
* "no record key" → an INCOMPLETE "Record key" row instead of an empty PASS;
* rounding-only differences reported as such, never FAIL;
* the suggested classification and the tester's decision (genuine defect, expected difference, false positive,
  further review) on every open row, in the detail view, the PDF and the Excel export.

## 7. Filters (Part 14)

There was no notion of the page's own filters. A comparison could put "Region = North" in one application against
everything in another, and the result was a long list of false "missing record" FAILs. New `filters.py`:

* discovery and classification of real filters (language, page size and theme selectors are excluded);
* dependent dropdowns, date ranges and the Apply / Reset buttons;
* apply and fetch in the application's own browser session, which pauses for sign-in, MFA or CAPTCHA (these are never
  bypassed);
* verification of the applied values;
* equivalent-filter links between applications, suggested by meaning and options and approved by the tester;
* value mapping, and a local filter for an application without the filter;
* a scope check: different scopes are NOT COMPARABLE;
* filter sets: only approved sets run, never every combination.

## 8. Browser windows (Part 8)

| # | Root cause | Fix |
|---|---|---|
| 8.1 | `browser.launch_args` (l.140–151) added `--start-maximized`, but every `new_context` received a fixed viewport, and pages opened in a new context or a popup were not maximized. `live_check.py:303` and `site_diagnostic.py:170` used a fixed 1366×860 context. The Ops agent driver, `pytest_export._launch` and the DSL template launched on their own. | One policy in `browser.py`: `window_mode` (maximized / fixed / default), `context_options` (maximized → `no_viewport`), `maximize(page)` through CDP `Browser.setWindowBounds`, with a fallback to the screen's work area when no window manager answers, and `new_context()`, which maximizes the first page explicitly and every popup through `ctx.on("page")`. Every call site uses it. Headless browsers are never maximized. The generated framework and the exported scripts maximize on their own. |

## 9. Installation (Parts 9, 10, 13)

The 23.8.0 installer stopped with "cannot be written" on a protected application folder. It had no way to carry an
OCR runtime. 23.9.0:

* refuses clearly, or with `--side-by-side` patches a copy in `%LOCALAPPDATA%\SkyGuardQA\app-23.9.0` (the original
  is untouched);
* unpacks the OCR wheels into a staging folder, swaps it in and keeps the previous runtime for rollback;
* verifies with an OCR engine probe, without administrator rights and without network access.
