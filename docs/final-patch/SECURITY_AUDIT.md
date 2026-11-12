# Security audit, SkyGuard QA 23.4.0

Scope: everything this patch adds or changes, reviewed against the rules given for the release. Each finding says
what was checked and how. "Test" means an automated test in `tests/` that ran in this release's regression run;
"Review" means a reading of the code only.

## Rules and how they are met

| Rule | How | Evidence |
|---|---|---|
| Never store passwords in plain text | Application sign-in passwords, comparison application passwords and performance secret columns go to the Windows Credential Manager (`keyring`) only. The database keeps the user name and sign-in method. The save is refused (HTTP 503) before any row is written when the Credential Manager is unavailable. | Test: `test_saved_login_keeps_sign_in_method_and_never_returns_password`, `test_comparison_agent_final.py` |
| Never return a saved password | The API returns `has_password` only; the login fields never fill the password back in and clear it after saving. | Test (API); Review (`web/js/login-fields.js`) |
| Never store secrets in AURA memory | `aura.scrub` removes keys and fields that look like passwords, tokens, cookies, sessions, authorization headers and API keys, and drops form field values; an entry that still matches a token shape (JWT, bearer, long hex/base64) is refused. Export and backup contain only stored entries. | Test: `test_aura_never_stores_secrets_and_backup_export_reset` |
| Never expose credentials in logs, screenshots, AI prompts or reports | Comparison screenshots mask password inputs before capture; navigation logs keep URLs without query strings; desktop UI Automation skips password controls; AI prompts are built from redacted facts (`investigate.redact`); performance scanner shows `***` for passwords and a 4-character prefix for tokens; run logs mask resolved credentials (existing `ValueResolver.mask`). | Tests: comparison end to end, `test_scanner_finds_dates_users_ids_and_secrets_and_makes_dates_dynamic` |
| Do not disable TLS certificate validation (comparison browser) | The comparison `WebExtractor` never sets `ignore_https_errors`. | Review: `qa_platform/comparison/extract_web.py` |
| Restrict navigation to approved hosts (SSRF) | Comparison browsing allows only http(s) to the application's host and hosts listed in the project; every request from the page is routed through a host check (`context.route`); cloud metadata addresses and link-local ranges are always blocked. | Test: `test_ssrf_and_upload_validation` |
| Validate uploads by signature and size | Screenshots: PNG/JPEG magic bytes, 15 MB, at most 12,000 px a side and 60 MP, header parsed without decoding; metadata chunks (EXIF, text) removed. Files: CSV/XLSX/JSON, 50 MB. Performance Excel import: ZIP signature then the workbook parser. | Test: `test_ssrf_and_upload_validation`, `test_excel_import_keeps_text_and_export_round_trips` |
| Evidence immutable | Evidence files are written once, made read-only, recorded with SHA-256 and checked on every read (`EVIDENCE_TAMPERED`, HTTP 409). Annotations are separate SVG copies. | Test (tamper case) |
| Audit immutable | Comparison audit is a hash chain; SQLite triggers refuse UPDATE and DELETE on `cmp_audit`; `verify_audit` re-computes the chain. | Test (end to end checks the chain) |
| Do not auto-approve low-confidence mappings | Mappings below 0.80 and every AI-suggested mapping are saved as `needs_review`; AI aggregate definitions need approval before a run uses them. | Test: comparison engine tests |
| Deterministic expected values | Aggregates are computed with `Decimal` from the source records; AI only explains. | Test: `test_comparison_engine_final.py` |
| Never fabricate reproduction steps | Steps come only from the recorded navigation log of the datasets involved; with no navigation recorded the defect says so. | Test (end to end) |
| AI cites only real evidence | Investigation drops evidence ids that do not exist and forces "not a product defect" for non-product causes. Prompts treat page text as data (prompt-injection wording in the system prompt). | Test; Review |
| No cloud AI | Only the configured On-Premise AI endpoint is used; OCR is local (Windows OCR or Tesseract). | Review |
| Access control | Comparison projects are visible to their owner and members only (the local program user and admins see all); only the owner or an admin changes members; view-only roles cannot approve definitions. | Test: `test_project_access_limited_to_members` |
| Self-healing does not change scripts silently | Low-confidence or ambiguous matches need a person (`needs_confirmation`); only an offered candidate is accepted. | Test: `test_ambiguous_shadow_dom_match_waits_for_confirmation` |
| Dynamic performance values cannot inject code | The bind expression accepts only `${__timeShift(<known format>,,P<n>D,,)}`; anything else (for example `__groovy`) is refused. | Test |
| Reports cannot run script | HTML reports escape every value and are served with `Content-Security-Policy: default-src 'none'`; Excel files contain inline strings only (no formulas, so no formula injection). | Review; Test (report download) |
| Settings validation | New settings keys are type-checked (sidebar flag, window mode, monitor 0 to 16, headless flags, comparison browser, Tesseract path must end in `tesseract.exe`). Login URLs must be http(s) (no `javascript:`). | Test: `test_settings_validation`, credentials test |
| Installer | Verifies every file's SHA-256 before copying; refuses another baseline; backs up before changing anything; never deletes data on failure or rollback (a replaced database is kept renamed). | Offline installation test report |

## Findings

1. **Existing (not introduced by this patch): automation runs, recording and discovery ignore TLS certificate
   errors.** `qa_platform/automation/runner.py`, `recorder.py`, `discovery.py` and the generated framework's
   `fixtures/browser.py` pass `ignore_https_errors=True`, as since earlier releases, so test environments with
   internal certificates work. It was left unchanged to avoid breaking existing scripts. Recommendation: add a
   Preferences switch "Check TLS certificates in test runs" (default on for new installations) and trust the
   company CA through the browser instead. The new Comparison Agent and performance recording check certificates.
2. **Credential Manager availability.** On a PC where the Windows Credential Manager cannot be used (some locked-down
   service accounts), passwords cannot be saved at all (by design, HTTP 503 with an explanation). Sign-in method
   "manual" still works.
3. **OCR output is untrusted text.** OCR results are stored as data and shown escaped; low OCR confidence (digit and
   letter confusion) is scored and marked for review rather than compared silently.
4. **Remote desktop windows.** UI Automation does not read inside RDP/Citrix windows (it is refused); only capture
   plus OCR is used, so values come from pixels and carry OCR confidence.
5. **Dependency review.** No new third-party packages. Pinned versions in `requirements.txt` are unchanged.

## Not verified here

* Behaviour with a real Windows Credential Manager, Windows OCR, UI Automation, RDP windows, two physical monitors,
  installed Edge or Chrome, and a real TFS / Azure DevOps Server. These need Windows and were exercised only through
  their test doubles in this Linux build environment.
