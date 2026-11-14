# SkyGuard QA 23.7.1: security review

**Scope:** the files changed for 23.7.1 (`automation/runner.py`, `automation/components.py`, `js/components.js`,
`automation/dsl.py`, `automation/pytest_export.py`, `web/js/pages/automation.js`, `automation/site_diagnostic.py`,
`skyguard_agent/learning.py`, `web/js/pages/ops-agent.js`, the cumulative installer and its build script), the merge with 23.7.0, and the dependency set. The 23.6.1 and 23.7.0 reviews are in `previous-23.6.1/`
and `previous-23.7.0/`.
**Tools:** manual review; `bandit` (medium and high) on the changed Python files; `pip-audit --strict` on
`packaging/requirements-offline-win.txt`.

| Area | Check | Result |
|---|---|---|
| Dependencies | `pip-audit --strict` on the offline wheel set | No known vulnerabilities found (`opentelemetry-api` 1.45.2, CVE-2026-126360 fixed) |
| Dependencies | New packages | None |
| Static analysis | bandit, high | 0 |
| Static analysis | bandit, medium | 4, all present before 23.7.1 and unchanged: two `f"SELECT ... {where}"` in `runner.py` (`where` is one of two fixed strings, values are bound parameters), the session list in `skyguard_agent/learning.py` (a fixed `WHERE application_id = ?` is appended; the value is a bound parameter) and the row count in the installer (table names read from `sqlite_master`). Not exploitable. |
| Choosing after typing | Wrong click | Only rows of a pop-up list drawn next to the field are candidates; the row must hold the typed words as a phrase; several matches are reported, never guessed; plain text fields with no list are left alone. |
| Counter row clicks (older recordings) | Wrong count | Played only when exactly one of + and - is enabled at the recorded number; otherwise the run stops with the fix. At most 30 presses; every press is checked. |
| set_count step | Input | Value parsed with a fixed pattern (`label=number`, number up to 3 digits); the label is matched against counters on the page, never run as code. In the exported pytest file the label is passed as a Python string literal (`repr`) to a fixed script. |
| Step editor | Data loss | Actions not in the list are shown as they are, so Save no longer turns `choose_option` / `dismiss` / `set_count` into `goto`. Values are still escaped (`esc`) before being put in the page. |
| Booking / payment guard | Unchanged | The guard for "Book now", "Pay now" etc. still applies to every recorded click. The new handlers only click suggestion rows and counter controls. |
| Installer | Admin rights, network | No administrator rights, no registry, no services, no network imports (`test_installer_needs_no_network_and_no_administrator`). Every patch file is checked against SHA-256 before anything is copied; any failure restores the backup. |
| Test stand-in | Content | `tests/site/tripcom/flights.html` is a local test page (not shipped in `app/`), contains no Trip.com code or images, and makes no network requests. |
| Finding the list field by name | Wrong field | The name comes from the recording; a field is used only when exactly one visible element matches; inside frames only the recorded locators are used; self-healing keeps its confidence rules. |
| Site diagnostic | Data collected | Runs only when the person starts it, in a visible browser; never clicks, types or books by itself. Saves screenshots, the accessibility tree and the page HTML with scripts removed and password fields blanked, the engine's view (input values of password fields shown as "[secret]"), and the person's own scripts and run results for the site. No cookies, storage or saved sign-ins. The ZIP stays on the person's Desktop; nothing is uploaded. |
| Learning tab | Error handling | The session list tolerates both stored forms and damaged rows; error cards are built with `esc()`; no change to who may read learning sessions. |

