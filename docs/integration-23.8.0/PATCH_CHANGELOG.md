# SkyGuard QA 23.8.0: Integration Comparison Workspace

Cumulative patch: 23.4.0 + 23.5.0 + 23.6.0 + 23.6.1 + 23.7.0 + 23.7.1 + 23.7.2 + 23.8.0.
Installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1 or 23.7.2.

## What changed

The integration comparison is now **one page**: **Integration Comparison** in the left menu. It replaces the 18
separate comparison pages (discovery, extraction, transformation, mapping, filters, navigation, comparison and
execution configuration, and so on). The flow on the page is:

1. **Applications.** Cards for Application A, B and C, plus *Add another application*. Two applications are enough.
   On each card you can:
   - upload one or more screenshots (preview thumbnails, remove any of them);
   - **Connect to application**: URL, sign-in method, user name and password (kept in Windows Credential Manager,
     never in the database or logs), optional navigation steps; the existing Playwright engine opens the page and
     reads its tables and figures (read-only);
   - upload an exported file (Excel, CSV, JSON, the existing importers);
   - tick *Screenshots show all records* when the screenshots really show every record.
2. **ANALYZE & DETECT DATA.** OCR (Windows OCR or Tesseract, as before) reads every screenshot and finds tables,
   columns, values, IDs, dates, statuses, percentages, KPI cards and charts with data labels. The preview shows them
   per application; values read with low confidence are highlighted, and a click on any value lets you correct it.
3. **Review mapping.** The mapping is suggested from the field names, their meaning (synonyms such as Sales and
   Revenue), the data type, and whether the sample values actually agree record by record. Names alone are not
   enough: two fields with similar names whose values never agree stay *Needs review* or *Unmapped*. Each row shows
   *Auto-mapped*, *Needs review* or *Unmapped*; you can change any field, rename the row or untick it. The page also
   suggests:
   - **status groups** (for example Completed = Successful = Done), with lower confidence when unsure;
   - **calculations** (for example Completion % = Completed / (Completed + Pending) × 100, totals as SUM or COUNT).
     These are evaluated with exact decimal arithmetic, not by the AI. You can edit or add a rule.
   - **charts** and the field each chart should match.
   **APPROVE & SAVE MAPPING** saves a new version. The next analysis starts from the saved mapping. *Review saved mappings* shows
   every saved version, who saved it and the note.
4. **RUN COMPARISON.** Compares every pair (A vs B, A vs C, B vs C) and shows whether B and C are each consistent
   with A.
5. **Results.** Totals, PASS / FAIL / WARNING, consistency per pair, filters, and a comparison table. A click on a row
   opens the details: the values with where each came from (screenshot, live or file), expected, actual and
   difference, the mapping and calculation used, the evidence screenshot with the value boxed in red, and
   **Explain with AI**. For failures: **CREATE DEFECT / REVIEW** (and *Send to TFS*), and the tester decisions
   *Valid business difference*, *Expected calculation difference*, *False positive*, *Actual defect* and
   *Requires investigation*. Every decision is kept in the history. Report: HTML, **PDF** and **Excel**.

## Rules the comparison follows

- Missing records, duplicate records and different values are reported per record key.
- A difference between applications that both follow an approved calculation rule is shown as *Expected*, not a
  defect.
- A chart whose values cannot be read gets a **visual check WARNING**, never a PASS.
- Data read only from screenshots is partial unless you tick *Screenshots show all records*: a record missing from a
  screenshot is a WARNING (*not on the screenshots*), and the result says the validation is partial.
- The result always says which data came from screenshots and which from live access or files.
- The AI is optional and only suggests. It never changes an approved mapping or rule by itself; approvals and
  decisions are recorded for learning.

## Database

Migration 19 adds four tables; nothing existing is changed:
`cmp_ws_screens` (what was read from each screenshot), `cmp_ws_state` (the last analysis), `cmp_ws_configs`
(saved mapping versions) and `cmp_ws_decisions` (tester decisions). Rolling back leaves them in place; older
versions ignore them.

## Kept as before

TFS integration, offline operation, the local LLM, OCR, Playwright, credential storage, evidence, the audit trail,
sign-in and role permissions (viewers can look but not change), and the existing comparison API.
