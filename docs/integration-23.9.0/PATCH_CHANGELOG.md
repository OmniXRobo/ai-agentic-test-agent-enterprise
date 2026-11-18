# SkyGuard QA 23.9.0: Integration Comparison, offline OCR and browser final patch

Cumulative patch: 23.4.0 to 23.9.0. It installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2 or
23.8.0. One zip: **SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch.zip**.

## Screenshots (offline OCR)
* A bundled offline OCR engine (RapidOCR with ONNX Runtime), installed per user without administrator rights. Windows
  OCR and Tesseract remain available. Engine order and paths are set in Preferences, and the diagnostics say which
  engine is used and why.
* A new table reader: separate columns even when they are close together, multi-line headers with units (AED, %),
  empty cells, wrapped cells, and totals / subtotals / grand totals that are checked and never counted as records.
  Titles and KPI cards are kept apart from tables. Dark themes and large screenshots are handled.
* The bundled OCR engine carries the Microsoft C++ runtime it was built with and loads it first. On a Windows 10
  1909 PC, the older msvcp140.dll in System32 ended the OCR process without an error ("stopped without an answer"),
  and the patch rolled back. The installer's check now reads a test image (not only imports), and a failure names
  the step, the exit code and the error output. If the bundled engine still cannot run while Windows OCR can, the
  check is a WARN: the patch stays and screenshots are read with Windows OCR.
* Windows OCR (found on real Windows runners): a number read with letters in a number column ("IO" for 10) is
  corrected and flagged for review, keeping what was read; an empty cell of a number column (Windows OCR skips a "0"
  that stands alone) is read again, enlarged, as copies that must agree. A cell that still reads as nothing stays
  empty and is reported. Nothing is guessed.
* Charts in screenshots: bar, horizontal bar, multi-series, pie and donut. Values read from an axis are approximate
  and need review. Pie shares are checked against 100%.
* Every table, chart and input has a status: COMPLETE, PARTIAL, FAILED or NEEDS REVIEW, with the reasons.

## Live applications
* Pages whose data comes from scripts or APIs are read again: CDN scripts and API calls are no longer blocked.
  Private networks stay blocked unless the application is on the intranet.
* Tables in frames, shadow DOM, ARIA and div grids, DataTables (all rows), merged cells and totals. Virtual scrolling
  and pagination are handled. Data is read after it appears.
* One capture reads tables, KPIs, charts (Chart.js, ECharts, Highcharts, Plotly, ApexCharts, d3, SVG) and a
  screenshot. Canvas charts and pages without a table fall back to OCR.

## Filters
* Discover and configure the page's filters, then apply them and fetch the data in the application's own session,
  with the values verified.
* Link equivalent filters across applications (Region = Territory), sync their values, or filter locally. A
  comparison of different scopes is NOT COMPARABLE.
* Filter sets: only the approved scenarios run.

## Mapping, calculations, charts
* Fields are matched by meaning, unit and values. Money roles are kept apart (unit price, subtotal, discount, tax,
  final amount). Saved mappings are flagged when a field changes.
* Local AI answers (Qwen) are validated and accepted in their usual variations. A bad answer no longer stops the
  analysis.
* Calculations are suggested only when they make sense: no MAX for a grand total, no amount plus percentage. They are
  computed in Decimal.
* Charts are compared with the right measure (sales, not quantity) and with the shares of the total for pie charts.

## Results
* Six statuses: PASS, FAIL, WARNING, NEEDS REVIEW, NOT COMPARABLE and INCOMPLETE. A difference that is only rounding
  is never a FAIL.
* The details show the source and target, the rule, the difference, the evidence and a suggested classification. The
  tester's decision (genuine defect, expected difference, false positive, further review) is in the PDF and Excel
  reports.

## Browser windows
* Every visible browser opens maximized, including popups and new tabs, through one policy. Headless never. A fixed
  size can be chosen in Preferences.

## Installation
* The OCR runtime is unpacked into a staging folder and kept for rollback. A protected application folder is never
  forced: the patch can install side by side in the user's own folder.
