# SkyGuard QA 23.4.0: final release notes

**Package:** `QA_Enterprise_AI_Final_Upgrade_Patch.zip`, one cumulative offline patch for **SkyGuard QA 23.3.35**.
Install with `APPLY_PATCH.cmd` (see INSTALLATION_GUIDE.md). Rollback with `ROLLBACK_PATCH.cmd`.

## What is new

* **Application Data Comparison Agent** (18 pages): compare the data two applications show, from web pages, Windows
  desktop applications, RDP windows, screenshots or files; record reconciliation; deterministic aggregate checks
  (counts, sums, averages, groups, percentages, monthly and running totals, pivots) against the totals, KPI cards and
  charts the target shows; a Defect Review Center with AI investigation that cites real evidence; HTML, PDF and Excel
  reports and an evidence package; TFS bugs with an offline queue.
* **AURA learning memory** with its own folder, backup, restore, export, import, integrity check and reset. It never
  stores secrets and never changes approved scripts.
* **Settings > TFS Integration**: discover fields, mandatory flags and allowed values; defaults per type; validate;
  test creation without saving. Fixes the HTTPS 400 errors when creating Test Cases (mandatory Test Result) and Bugs.
* **Settings > Browser Manager**: one browser choice for everything (Chromium, Edge, Chrome), never switched
  silently; maximized on the chosen monitor; headed or headless; Check browsers.
* **Application sign-in fields** next to Generate Automation, Start Intelligent Discovery and Hybrid recording, with
  sign-in method (form, Windows/NTLM, Basic, manual SSO/MFA, none).
* **Self-healing asks when unsure**, finds elements inside shadow DOM and uses where an element sits (parent,
  siblings, nearby text, form).
* **Performance**: parameters to review with one-click dynamic dates, Excel import and export of test data, failed
  samples grouped by cause.
* **AI Test Design** explains why the On-Premise AI failed and restarts the managed local model.
* **UI**: collapsible sidebar with icons and tooltips (remembered); light theme fixes for AI Voice Command Center,
  Agent Workflow and Voice & Command Agent; dark theme unchanged.

## Fixed

* TFS Test Case creation: HTTPS 400 because of a mandatory Test Result field (defect 1).
* TFS Bug creation: HTTPS 400, missing Environment and System Info, work item id not shown (defect 3).
* AI-Driven Automation: no Login URL / ID / Password / method fields; "Could not start chromium" (defect 4).
* Browser not maximized on the second monitor; headed/headless not configurable (defects 5 and 6).
* Self-healing used an AURA lookup that raised "'module' object is not callable" when an approved correction was
  looked up (found and fixed in this release).

## Known limits

* Windows OCR, UI Automation, RDP capture, two-monitor placement, Edge/Chrome launch, the Windows Credential Manager
  and a real TFS server were not available in the build environment (Linux). Their code paths ran against test
  doubles; they need a check on a Windows PC (see REGRESSION_TEST_REPORT.md, "Not verified").
* Automation runs keep ignoring TLS certificate errors as in earlier versions (SECURITY_AUDIT.md, finding 1).
