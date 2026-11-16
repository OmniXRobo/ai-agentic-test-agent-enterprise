# SkyGuard QA 23.9.0: final report

**Deliverable:** `SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch.zip` with its `.sha256` file. It is one
cumulative patch (23.4.0 to 23.9.0) for 23.3.35 to 23.8.0 installations. It works offline and needs no
administrator rights.

## What was wrong and what changed

The root causes are in [ROOT_CAUSE_REPORT.md](ROOT_CAUSE_REPORT.md), with the files and functions of 23.8.0. In short:

1. **Screenshots:** no OCR engine was shipped, and the table reader merged columns, stopped at empty cells and
   counted totals as records. 23.9.0 ships a per-user offline OCR runtime, a new table and chart reader, and an
   extraction status for every input.
2. **Live URLs:** CDN scripts and API calls were blocked, so JavaScript tables stayed empty, and the reader missed
   frames, shadow DOM and grids. 23.9.0 adds a request policy, a wait for data and a layered reader, with OCR as the
   fallback.
3. **Mapping:** names alone, and Qwen answers rejected for "85" instead of "0.85". 23.9.0 maps by meaning, unit and
   role, validates AI answers leniently and falls back to the deterministic rules.
4. **Calculations:** MAX could explain a grand total, and unrelated fields could be added together. 23.9.0 only
   suggests calculations that make sense, and computes them in Decimal.
5. **Charts:** a chart could be checked against Quantity instead of Sales. 23.9.0 chooses the measure by meaning and
   checks pie shares.
6. **Results:** six statuses, scope / unit / key rows, suggested classification and tester decision in the reports.
7. **Filters:** discover, apply and fetch, equivalent-filter sync, scope check and approved filter sets.
8. **Browser:** one maximize policy for every visible window, including popups.
9. **Installer:** OCR runtime with staging and rollback, and a side-by-side install for protected folders.

## Verified here

See [TEST_REPORT.md](TEST_REPORT.md) for the commands and the counts.

* **Four-application lineage, read from rendered screenshots by the real OCR engine:** 78 comparisons, 77 PASS and
  exactly 1 FAIL, the Dubai bar (8,950.00 shown, 9,450.00 expected). The pie shares, the summary Share % and Final
  Amount = Subtotal − Discount + Tax all pass. Evidence: `evidence/lineage-*.png`, `lineage-report.pdf/.xlsx`.
* Live capture on local pages: a paginated JS table, a virtual ARIA grid, iframes with merged cells and totals, shadow
  DOM, a div grid, a DataTables-style API, SVG/d3/aria charts, and a canvas chart through OCR.
* Filters end to end in the browser UI: discovery (Language and page size excluded, City depends on Region, a date
  range, the Apply button), apply and fetch (3 of 3 records, values verified), links, sync, scope mismatch → NOT
  COMPARABLE, a local filter, and approved filter sets only. Evidence: `evidence/filters-*.png`.
* Invalid Qwen output (Test 8), the maximize policy (Test 9, under Xvfb), saved-mapping invalidation after a unit
  change, and the Preferences validation.
* The installer on real 23.3.35 to 23.8.0 trees: apply, verify, rollback, apply again, automatic rollback, the OCR
  runtime staged, swapped and rolled back, a damaged wheel refused, and a protected folder refused or installed side
  by side.

## Not verified (see KNOWN_LIMITATIONS.md)

Windows itself was not available here, so these were not run: the PowerShell wrappers, the Windows OCR wheels and
Windows OCR, maximizing on a real Windows desktop, and a standard-user Windows install. Public websites and the real
DataTables library were also not tested. Your original screenshots were not in the project files, so representative
fixtures were generated and read by the real OCR engine.
