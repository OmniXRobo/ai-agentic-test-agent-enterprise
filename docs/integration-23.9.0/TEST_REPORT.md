# Test report: 23.9.0

Environment: Linux container, Python 3.13, Playwright 1.63 with Chromium, Xvfb (no window manager), RapidOCR 3.10.0 /
ONNX Runtime on Linux wheels. No Windows machine. Every result below comes from a run that actually happened here.

## Full suite in this container (commit 46dfb49; the final commit ran in CI, below)

    xvfb-run -a -s "-screen 0 1600x1000x24" python -m pytest -q -rs tests/

**862 passed, 77 skipped, 0 failed** in 31 minutes. Skip reasons, as reported by pytest:

| Skipped | Reason |
|---|---|
| 46 | older cumulative-patch tests superseded by a later patch test (23.6.0, 23.7.1, 23.7.2, 23.8.0); the 23.9.0 patch test below covers every baseline |
| 17 | JMeter / Java not installed (performance tests) |
| 13 | Windows only |
| 1 | the host name resolves to loopback |

## Windows (GitHub Actions, on the final commit)

The repository's CI ran the same suite on `windows-latest` with Python 3.11, 3.12 and 3.13: **865 passed, 80 skipped,
0 failed** on each. On Ubuntu (3.11 to 3.13): 858 passed, 87 skipped, 0 failed. The offline-package and
security-audit jobs passed.

On Windows, `test_07_three_screenshots_ocr_and_compare` reads three screenshots with **Windows OCR**
(`Windows.Media.Ocr`); in 23.8.0 it was skipped there. Its first runs failed, and the log showed why: Windows OCR read
"10" as "IO" and returned nothing for a "0" alone in a cell. Both were fixed in the reader (see PATCH_CHANGELOG), each
run in between was a real Windows run, and the final run passes with every value read and compared.

## 23.9.0 acceptance tests (Part 12)

| # | Test | Where | Result |
|---|---|---|---|
| 1 | Table screenshots read exactly (PNG, dark JPEG, two-line "(AED)" headers, GRAND TOTAL), each value checked against `truth.json`; OCR diagnostics | `test_integration_ocr_browser_23900.py::test_01*` | passed |
| 2 | Four-application lineage (table, detailed table, bar, pie, summary): exactly one FAIL, the Dubai bar (8,950.00 vs 9,450.00); Final Amount = Subtotal − Discount + Tax; no MAX/MIN rules; the bar is checked against sales, not quantity | `test_02` | passed |
| 3 | A bar without labels: values from the axis, approximate, NEEDS REVIEW, never PASS | `test_03` | passed |
| 4 | Horizontal bar and donut charts | `test_04` | passed |
| 5 | Live layers: paginated JS table, virtual ARIA grid, iframe with rowspan/colspan and a total, shadow DOM, div grid, DataTables-style API (all rows); the 23.8.0 host rule shown to give 0 records; private addresses and off-site redirects blocked | `test_05*` (8 cases) | passed |
| 6 | Live charts (SVG, d3, aria-label) and a canvas chart read through OCR in a workspace capture; a paginated JS table captured through the workspace | `test_06*` | passed |
| 7 | 23.8.0 screenshots still read, with the title kept apart from the header | `test_integration_workspace_23800.py::test_07*` | passed |
| 8 | Invalid Qwen output: 85 / "85%" / "high", bare list, prose around JSON, invented names, garbage, AI offline, batches; deterministic fallback; no sample values in the log | `test_integration_ai_browser_23900.py::test_08*` | passed |
| 9 | Maximize: launch arguments, context options, every module goes through the policy, a visible window and a popup fill the screen (Xvfb), fixed size respected, headless never maximized | `test_09*` | passed |
| 10 | Installer on real 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2 and 23.8.0 trees: apply, verify, rollback, apply again; wrong version and changed file refused; automatic rollback; OCR runtime staged, swapped and rolled back; damaged wheel refused; protected folder refused, then side by side | `test_cumulative_patch_23900.py` (18) | passed |
| 14 | Filters: discovery and classification, cascading dropdown, date range, apply and fetch with verified values; equivalent-filter links, sync, scope mismatch → NOT COMPARABLE, local filter, record key by name, approved filter sets only | `test_14`, `test_14b` | passed |
| – | Saved mapping flagged after AED → USD; unit mismatch is NOT COMPARABLE | `test_saved_mapping_*` | passed |
| – | Preferences: window mode, OCR engine, timeouts, business vocabulary (validation and use) | `test_preferences_*` | passed |

## End-to-end runs in the browser UI (evidence)

* `evidence/lineage-*.png`, `lineage-report.pdf`, `lineage-report.xlsx`: the five screenshots uploaded through the API
  of a running SkyGuard QA, then Analyze, mapping approved, Run. Result FAIL with 78 comparisons: 77 PASS and 1 FAIL
  (the Dubai bar, shown with the suggested classification "Genuine defect"). The PDF and Excel reports were
  downloaded from the run.
* `evidence/filters-*.png`: the filters panel driven in Chromium. Both applications discovered, links approved,
  values synced (Territory = North, Order State = Paid), Apply filters & fetch data → 3 of 3 records, complete, no
  page errors.

## Not executed here

* Windows: `APPLY_PATCH.ps1/.cmd`, the `win_amd64` OCR wheels (checked by hash and unpacked, not run), Windows OCR,
  Credential Manager, maximizing on a Windows desktop with a window manager and DPI scaling, a standard-user install.
* Public websites and the real DataTables library (the fixture implements its API shape).
* The user's own screenshots (not available; representative fixtures were used).
