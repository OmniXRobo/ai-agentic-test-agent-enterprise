# Integration Comparison: quick guide (23.9.0)

1. Open **Integration Comparison**. Choose a comparison or click **New comparison**.
2. On each application card, add the data: **Upload screenshots**, **Connect to application** (URL and sign-in;
   the password is kept in Windows Credential Manager) or **Upload export file**. Tick **Screenshots show all
   records** only if the screenshots really show every record.
3. **Filters (optional).** For a connected application, click **Discover & configure filters**. Tick **Show the
   browser** to watch, or to sign in yourself.
   - The page's filters are listed (Region, City, Status, dates…). Language, page-size and theme selectors are not
     filters. A cascading dropdown shows "depends on …". Untick a control that is not a filter, then **Save filter
     configuration**.
   - Choose values and click **Apply filters & fetch data**. SkyGuard QA sets the values in the page, clicks its Apply
     button, waits for the data, checks the values and reads every page of the result. If the page asks for a
     sign-in, MFA or a CAPTCHA, the session pauses: finish it in the browser window and click **Resume**.
   - **Equivalent filters** links Region with Territory, Status with Order State, and so on. Each link is suggested
     from the meaning of the labels and their options. Tick **Approved** to use it. If an application has no such
     filter, choose a column under "filter the fetched data on" to filter it locally.
   - **Sync equivalent filters** fills the other applications' values from one application (Paid → Paid,
     Complete → Completed when the options say so). Check the values, then apply them in each application.
   - **Filter sets** save scenarios such as "North / Paid". **Run all approved sets** fetches and compares each one.
     Only the sets you approved run, never every combination.
4. Click **ANALYZE & DETECT DATA** and check the preview. Each input shows how it was read: *Read completely*,
   *Partly read*, *Read, needs review* or *Could not be read*, with the reasons. Click a value to correct it.
5. Check the **mapping**. Fields are matched by meaning, unit and values, not by name alone. A quantity is never
   mapped to an amount. Total and Final Amount are suggested together only when their values agree. A saved mapping
   whose field changed (for example AED → USD) is shown as *Needs review*. Approve and save.
6. Click **RUN COMPARISON**, or **Compare filtered data** in the filters panel.
7. The results use six statuses:
   - **PASS**: the same value. "Normalized" means the same value after formatting (AED 1,200.00 = 1200).
   - **FAIL**: a real difference.
   - **WARNING**: a difference within an accepted rule.
   - **NEEDS REVIEW**: approximate (read from a chart axis), low OCR confidence or not approved.
   - **NOT COMPARABLE**: different units or different filter scopes.
   - **INCOMPLETE**: data missing, extraction partial, or no record key.

   Click a row to see the source and target application, the record, the values, the rule, the difference, the
   evidence and the suggested classification. Then record your decision: **Genuine defect**, **Expected difference**,
   **False positive** or **Further review**.
8. Download the **PDF** or **Excel** report. Both include the extraction status and scope of each input, and your
   decisions.

Preferences > Integration Comparison sets the OCR engine (automatic: bundled offline OCR, then Windows OCR, then
Tesseract), optional paths, how long to wait for live data, and your own business words (for example
`location: depot, hub`). Preferences > Playback > Browser window chooses maximized (default), a fixed size, or the
browser's own size for every visible browser.
