# Integration Comparison: quick guide

1. Open **Integration Comparison** in the left menu. Click **New comparison**, give it a name.
2. On the **Application A** card, click **Upload screenshots** and choose the screenshots of the first application.
   Do the same for **B** (and **C** if you need it). Instead of screenshots you can use **Connect to application**
   (URL and sign-in) or **Upload export file**.
   - Tick **Screenshots show all records** only if the screenshots really show every record.
3. Click **ANALYZE & DETECT DATA**. Check the preview. Values with a yellow background were read with low
   confidence: click one to correct it, then click **Analyze** again.
4. Check the **mapping** table:
   - *Auto-mapped* rows are usually right; *Needs review* rows need a look; *Unmapped* fields are hidden behind
     *Show N unmapped field(s)*.
   - Change a field with its drop-down, untick **Same** to drop a row.
   - Check the **status groups**, **calculations** and **charts** under the table. **Edit** a calculation or
     **Add a calculation** if needed.
   - Click **APPROVE & SAVE MAPPING**. Next time the saved mapping is used again; **Review saved mappings** shows the versions.
5. Click **RUN COMPARISON**.
6. In the results, use the filters (Failed, Warnings, A vs B, ...). Click a row to see the details and the evidence.
   - Choose a decision (valid business difference, expected calculation difference, false positive, actual defect,
     requires investigation), add a note.
   - **CREATE DEFECT / REVIEW** writes a defect draft; **Send to TFS** creates it in TFS.
7. Download the **PDF** or **Excel** report from the results header.

Sample data: the three pages in `tests/site/integration/` (A: customer and monthly sales tables, B: KPI cards and a
summary table, C: a bar chart and a table with a different completion formula) are test data only. Use them to try
the page; they are not used by real comparisons.
