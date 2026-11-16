# Change manifest: 23.8.0 → 23.9.0

Baseline: commit `c838bf8` (23.8.0, merge of PR #53). The patch is cumulative from 23.3.35 (commit `6d448fa`). Its
`PATCH_MANIFEST.json` lists every application file with its SHA-256. This page lists what 23.9.0 changes.

## Application (`app/`)

| File | Change |
|---|---|
| `qa_platform/__init__.py` | Version 23.9.0. |
| `qa_platform/comparison/ocr.py` | Engine detection and order (bundled RapidOCR, Windows OCR, Tesseract); configurable paths; worker subprocess with a timeout; `diagnose()`; Windows OCR without `-ExecutionPolicy`; no duplicate Tesseract words. |
| `qa_platform/comparison/ocr_worker.py` (new) | Isolated OCR worker: image validation (formats, 80 Mpx limit), preprocessing (upscale, dark-theme inversion, tiling), RapidOCR with word boxes, chart graphics (bars, swatches, pie/donut slices), engine probe. |
| `qa_platform/comparison/screen_reader.py` | Rewritten table reader: column gutters from body-word coverage, segments (titles, KPI cards, tables), multi-line headers with units, totals/subtotals/grand totals with Decimal checks, wrapped cells, extraction status and issues; chart reconstruction (bar, horizontal bar, multi-series, pie, donut, axis-only = approximate). |
| `qa_platform/comparison/semantics.py` (new) | Business meaning of a field (concept, money role, unit), `same_meaning`, `can_combine`, `aggregate_allowed`; extensible vocabulary. |
| `qa_platform/comparison/mapping.py` | Lenient, validated AI answers (confidence 85 / "85%" / "high", bare list, alias keys); grammar enforced; batches of 20; safe logging; deterministic fallback. |
| `qa_platform/comparison/formula.py` | Calculation suggestions limited by meaning; no MIN/MAX for totals; a − b + c only for discount and tax. |
| `qa_platform/comparison/workspace.py` | Meaning-aware mapping and caps, saved-mapping signatures, record key by name when the scopes differ, chart and summary checks by meaning, six result statuses, scope / unit / key rows, filters (discover, configure, apply & fetch, links, sync, sets), compare filtered data, report columns. |
| `qa_platform/comparison/filters.py` (new) | Filter discovery and classification, dependent dropdowns, date ranges, apply with verification, CAPTCHA/login pause, equivalent-filter suggestion, value mapping, local filter, scope check, browser session thread. |
| `qa_platform/comparison/extract_web.py` | Request policy (public sub-resources, same-site navigation, private networks blocked unless intranet), credentials restricted to the application's origin, wait for data, layered table reader (frames, shadow DOM, ARIA/div grids, DataTables API, colspan/rowspan, totals), more chart libraries, record counts. |
| `qa_platform/comparison/service.py` | Single "page" capture (tables, KPIs, charts, screenshot, OCR fallback) used by Connect and by Apply Filters & Fetch Data. |
| `qa_platform/api/workspace.py` | Filter, filter-link, sync, filter-set and OCR diagnostics endpoints; view-only roles refused for changes. |
| `qa_platform/api/core.py` | Settings validation for window mode `fixed`, viewport size, OCR engine, timeouts and business vocabulary. |
| `qa_platform/config.py` | Defaults: playback `window_mode`, `viewport_width/height`; comparison OCR and capture settings. |
| `qa_platform/automation/browser.py` | One window policy: `window_mode`, `context_options`, `maximize()` (CDP), `new_context()` maximizing first pages and popups. |
| `qa_platform/automation/runner.py`, `recorder.py`, `discovery.py`, `live_check.py`, `site_diagnostic.py`, `performance/recording.py`, `opsagent/service.py`, `skyguard_agent/drivers/playwright_driver.py` | Open their contexts through the central policy. |
| `qa_platform/automation/pytest_export.py`, `dsl.py`, `framework/template/fixtures/browser.py` | Exported and generated scripts open maximized (`--start-maximized`, no fixed viewport, CDP maximize). |
| `web/js/pages/integration-workspace.js`, `web/css/app.css` | Filters panel (per application, links, sync, sets), result statuses, extraction and scope pills, detail fields. |
| `web/js/pages/settings.js` | Preferences: browser window mode and size; Integration Comparison OCR engine, paths, data wait, business vocabulary. |

No database migration (still 19). Filters, links and sets live in existing JSON settings columns.

## Patch tooling and runtime

| File | Change |
|---|---|
| `install_patch.py` | 23.9.0; OCR wheels checked (SHA-256), unpacked to a staging folder and swapped in, previous runtime kept for rollback; protected folders refused or patched side by side in `%LOCALAPPDATA%`; new verify checks (comparison 23.9.0, browser policy, OCR probe). |
| `APPLY_PATCH.ps1/.cmd`, `ROLLBACK_PATCH.cmd`, `VERIFY_PATCH.cmd` | 23.9.0 texts, `-SideBySide`, logs fall back to `%LOCALAPPDATA%`. |
| `build_patch.py` | Name `SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch`, OCR wheels with `ocr-runtime/WHEELS.json`, 23.9.0 docs and tests. |
| `ocr-runtime/wheels/*.whl` | Offline OCR runtime for 64-bit Windows / Python 3.13 (see OFFLINE_DEPENDENCIES.md). |

## Tests

New: `tests/test_integration_ocr_browser_23900.py`, `tests/test_integration_ai_browser_23900.py`,
`tests/test_cumulative_patch_23900.py` (replaces the 23.8.0 patch test), fixture sites `tests/site/integration_ocr`
and `tests/site/integration_live`. Updated: `tests/test_integration_workspace_23800.py`, with the new statuses and the
OCR skip condition.
