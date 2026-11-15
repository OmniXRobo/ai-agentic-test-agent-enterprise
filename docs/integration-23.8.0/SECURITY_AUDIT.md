# 23.8.0 security review

| Requirement | How it is met | Checked by |
| --- | --- | --- |
| Read-only by default, never modify source records | Live access uses the existing read-only extraction (navigation and reading only). The workspace has no step that writes to an application. | code review; `test_live_capture_and_pdf` |
| No passwords or tokens in logs or the database | Passwords go to Windows Credential Manager through the existing `cmp_app` store. The API returns only `has_password`. | `test_security_formula_upload_and_password` (config, API answer and audit trail are searched for the password) |
| Uploaded files validated | Screenshots go through the existing `check_image` (type, size up to 15 MB, pixel limit) and metadata is stripped; non-images are refused. Export files use the existing importers and their limits. | `test_security_formula_upload_and_password` |
| Formulas cannot run code | Calculation rules are parsed with a whitelist (numbers, field references, + − × ÷, SUM/AVG/MIN/MAX/COUNT/ROUND/ABS) and evaluated with Decimal. Names, attributes, calls to anything else are refused. | `test_security_formula_upload_and_password` |
| Authentication and roles kept | All new endpoints are under `/api/comparison/workspace` and go through the existing sign-in and role middleware (viewers: GET only). | `tests/test_regression_pages.py`, existing auth tests |
| Report pages | The HTML report is served with `Content-Security-Policy: default-src 'none'` and every value is escaped. | code review |
| AI | Optional, local model only, suggestion-only; it never changes an approved mapping or rule. | `test_11b_tester_decision_accepts_a_known_difference`, code review |
