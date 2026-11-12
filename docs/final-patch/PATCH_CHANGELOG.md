# SkyGuard QA 23.4.0: final consolidated upgrade patch, changelog

Baseline: **SkyGuard QA 23.3.35**. This one patch holds the four development requests (master defect list, Hybrid AI
Application Data Comparison & Defect Investigation Agent, UI enhancement, Source-to-Aggregate Reconciliation).
Nothing that existed in 23.3.35 was removed.

## 1. TFS integration (defects 1 and 3)

* **Settings > TFS Integration** (new page): Test Connection, CA bundle, Discover Project Metadata / Refresh Metadata
  (every work item type with its fields, mandatory flags and allowed values, read from the project), default values
  per type, Validate Mandatory Fields, Test Work Item Creation (`validateOnly=true`, nothing is saved in TFS).
* New `qa_platform/tfs/fields.py`. Before any work item is created, mandatory fields are completed from: the value
  the user gave, the project default saved in Preferences, a safe automatic choice (a single allowed value, or a
  "0 Not Started" style value for a result field), else the type's own default. Values are matched to allowed values
  ("uat" becomes "UAT", "2" becomes "2 - High").
* **Defect 1**: creating Test Cases no longer fails with HTTPS 400 when the project has a mandatory *Test Result*
  field. It gets "0 Not Started" (or the saved default).
* **Defect 3**: bugs carry Environment and System Info (`Microsoft.VSTS.TCM.SystemInfo`); a field the project
  enforces only by a rule (TF401320) is read from the error, filled the same way and the create is tried once more.
  A field that needs a person is asked for by name with its allowed values (`TFS_FIELDS_REQUIRED`), never shown as
  "HTTP 400". The created work item id is shown.

## 2. AI Test Design diagnostics (defect 2)

* When the On-Premise AI fails, the page shows why (configuration, the local model managed by SkyGuard QA, server
  reachability, the loaded model) next to the error.
* The managed local model is started (or waited for while it loads) when it does not answer; configured fallback
  models are tried in order. No cloud AI is used.

## 3. Browser Manager, sign-in fields, window placement (defects 4, 5, 6, 8, 9)

* **Settings > Browser Manager** (new page): Chromium, Microsoft Edge or Google Chrome for every run, recording,
  discovery and comparison; Check browsers (installed, starts, version). The chosen browser is never switched silently;
  a browser that does not start fails with the reason and what to do (fix for "Could not start chromium").
* Visible windows open maximized on the monitor chosen in Preferences (auto = the second monitor when present).
  Headless or headed is configurable for runs and for recording separately.
* **Application sign-in fields** (Login URL, User ID, Password, sign-in method: login form, Windows/NTLM, Basic,
  manual SSO/MFA, none) next to Generate Automation (defect 4), Start Intelligent Discovery (defect 8) and Hybrid
  recording (defect 9). Saved as an Application Credential: the password in the Windows Credential Manager only,
  never filled back into the page. Windows and Basic sign-in are passed to the browser as HTTP credentials.
* Database migration 18: `app_credentials.auth_method`.

## 4. Advanced locators and self-healing (defect 7)

* Element fingerprints now also record where the element sits: parent, ancestors, siblings, the nearest text before
  it, its form and its shadow host. Candidates inside open shadow roots are found too.
* A best candidate between 0.45 and the 0.62 threshold, or two candidates that look equally right, is no longer used
  silently: the run stops with "low-confidence candidates", the event is recorded as **needs confirmation**, and the
  Self-healing table offers **Use this** per candidate or **None of these**. A confirmed choice updates the script
  (the old selector kept as an alternate) and is stored in AURA as an approved correction.
* `POST /api/automation/healing/{id}/confirm`. Page Object Model generation and AI analysis are unchanged.
* Fixed: `aura.service()` clashed with the `qa_platform.aura.service` module name, so the AURA locator lookup raised
  "'module' object is not callable" during self-healing. Renamed to `aura.current()`.

## 5. Performance (defect 10)

* **Parameters to review** (script editor > Test data): recorded dates, user names, ids, passwords and server tokens
  are listed. Dates can be made dynamic in one click (`${__timeShift(yyyy-MM-dd,,P7D,,)}`, keeping the recorded
  distance from today); users and ids go to data columns (one row per virtual user); passwords to secret columns;
  tokens are flagged for correlation.
* Performance data sets import **Excel (.xlsx)** as well as CSV (cell text kept, leading zeros kept) and export to
  Excel. Secret columns are exported empty.
* Failed samples are classified by cause (authentication, correlation, assertion, rate limited, not found, client
  error, server error, timeout, connection, TLS) from response codes and messages, without AI; the table is shown on
  the results page and given to the AI analysis.
* Load, stress, spike and soak shapes, per-user data and reporting were already present in 23.3.26 to 23.3.31.

## 6. AURA learning memory

* New `qa_platform/aura/` and **Settings > AURA Agent Memory**: a SQLite memory in a folder you choose (any drive),
  check folder, migrate, enable/disable, retention, integrity check, backup, restore, export, import, reset (typed
  confirmation and an automatic backup first), activity log.
* AURA learns application profiles, page structures, components, field mappings, healing history and approved
  corrections. Secrets are removed before anything is stored, and entries that still look like a secret are refused.
  AURA never changes an approved script on its own.

## 7. Application Data Comparison Agent (request 2) and Source-to-Aggregate Reconciliation (request 4)

* New menu group with 18 pages: Dashboard, Projects, Applications, Discovery, Extract Data, Screenshots & OCR, Import
  Files, Field Mapping, Filters, Rules, Aggregates, Run Comparison, Results, Aggregate Report, Defect Review Center,
  AI Investigation, Reports, Evidence.
* Sources: web applications (login, navigation steps, tables, grids, pagination, scrolling, KPI cards, charts),
  Windows desktop applications (UI Automation), RDP/Citrix windows (capture plus OCR), screenshots (Windows OCR or
  Tesseract), and CSV/XLSX/JSON files.
* Field mapping with confidence. Below 0.80 or AI-suggested mappings need review; low-confidence mappings are never
  approved automatically. Normalization of numbers, dates, currencies, text.
* Record reconciliation (missing, extra, mismatched, duplicates) and **deterministic aggregate checks**: COUNT, SUM,
  AVG, MIN, MAX, DISTINCT COUNT, GROUP BY, percentages, monthly, running totals and pivots computed from the source
  records, compared with the totals, KPI cards and chart values the target shows: *Metric | Source calculated |
  Target displayed | Difference | Result*. Filter or chart configuration differences are reported as
  CONFIG_MISMATCH. AI-suggested aggregate definitions must be approved before they are used. AI explains results; it
  never produces the expected values.
* Defect Review Center: drafts only for product categories, duplicate check against open TFS bugs, AI investigation
  that cites only recorded evidence, reproduction steps built only from the recorded navigation, TFS bug creation
  with an offline queue, "not a defect" learned by AURA.
* Reports: HTML, PDF, Excel (no formulas), evidence package with SHA-256 manifest. Evidence files are read-only and
  checked on every read (`EVIDENCE_TAMPERED`). The audit trail is a hash chain the database refuses to update or
  delete.
* Database migration 17: `cmp_*` tables (new tables only).

## 8. UI (request 3)

* Collapsible sidebar: the hamburger button collapses it to icons with tooltips; the choice is remembered
  (`ui.sidebar_collapsed`). On narrow screens the button opens and closes the menu as before.
* Light theme: AI Voice Command Center, the Agent Workflow header and aeroplane, and Voice & Command Agent are
  readable, using the theme tokens. The dark theme is unchanged.

## 9. Installer

* `install_patch.py` with `APPLY_PATCH`, `VERIFY_PATCH` and `ROLLBACK_PATCH`: detect, verify the baseline and the
  patch files, check disk space and permissions, back up files, database and settings, migrate, verify (version,
  migrations, integrity, row counts), automatic rollback on any failure.
