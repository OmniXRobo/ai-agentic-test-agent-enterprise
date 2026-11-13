# SkyGuard QA 23.6.0: security review

**Scope:** every file changed for 23.6.0, the new endpoints, the cumulative installer and the dependency set.
**Tools:** manual review; `bandit 1.9.4` (whole `qa_platform` and `skyguard_agent`, medium and high); `pip-audit` on
`requirements.txt`.

## Results

| Area | Check | Result |
|---|---|---|
| Dependencies | `pip-audit -r requirements.txt` | No known vulnerabilities found |
| Dependencies | New packages in 23.6.0 | None (the offline wheel set is unchanged) |
| Static analysis | bandit, high severity | 0 |
| Static analysis | bandit, medium (10) | Reviewed; none exploitable (see below) |
| New endpoint `GET/POST /api/automation/runs/{id}/intervention` | Authentication and roles | Requires sign-in. View-only users may answer (continue/skip/stop/choose) but **not** `allow` (payment or booking click), which is refused with 403. The body is validated: `action` from a fixed list, `choice` ≤ 300 characters and one of the listed choices. |
| New endpoint `POST /api/automation/scripts/{id}/semantic-upgrade` | Authentication and roles | Write path: view-only users are refused by the existing role rules. It saves a **new** version only with `apply=true`, so older versions stay restorable. |
| Ops Agent `resume` with `choice` | Input validation | Integer 0–4 and must index a listed choice (`CHOICE_INVALID`). A chosen control is used for that step only and never stored as a general location. |
| Script ids and paths | Path traversal | Script ids are matched against `^SCR-[A-Z0-9]{10}$` and `^FWS-[A-Z0-9]{10}$`. Framework file paths refuse `..`, absolute paths, drive letters and the internal `.versions` / `.staging` folders (tested). |
| Opening a script | Integrity | Reads only. It never creates, truncates or overwrites a file. Binary or unknown-encoding content is refused, not decoded into garbage (tested). |
| Web pages | XSS | New HTML is built with `esc()`. Pause messages, intervention reasons and AI text are set with `textContent`. Links use `encodeURIComponent`. |
| Injected page script `components.js` | Exposure to the page under test | No `eval`, no network calls, no secrets. It only reads the DOM and marks one target attribute. Values typed by steps are not stored in it. |
| Payments and bookings | Safety | A visible run cannot click "Pay now", "Book now", "Complete booking", "Place order"… without a separate "Allow this once". The Operations Agent keeps its preview, confirmation and commit authorization. OTP, CAPTCHA and sign-in stay with the person. |
| Secrets | Logging and storage | Fingerprints and choices exclude values (tested). The design pipeline prompt still demands synthetic data and `{{PASSWORD}}`-style placeholders. |
| AI Test Design errors | Information disclosure | Errors name the stage and the setting to change. Prompts and model output are not echoed back. |
| Installer | Privileges | No elevation, services, `HKLM` or Program Files. It writes only inside the installation folder and runs as the installing user. This was tested as an unprivileged user (`nobody`). |
| Installer | Offline / integrity | Standard library only, with no network imports. Every payload file is checked against `PATCH_MANIFEST.json` (SHA-256) before anything is changed. A changed file aborts the install (tested). |
| Installer | Failure safety | Backup before copy; atomic file replace; automatic restore on any failure; the half-migrated database is kept, not deleted (tested). |

## Bandit medium findings (reviewed, not exploitable)

- **B608, SQL built with f-strings** in `skyguard_agent/executor.py`, `auth.py`, `api/automation.py`,
  `automation/framework/studio.py` and `aura/service.py`. The interpolated parts are column lists or `WHERE` clauses
  assembled from fixed names in code. Every value is passed as a `?` parameter, and no user input reaches an
  identifier.
- **B314, `xml.etree` parsing of uploaded .xlsx** in `automation/files.py`. Python's expat (2.9.0 here) does not load
  external entities and limits entity expansion ("billion laughs"), so XXE and expansion attacks do not apply. Moving to
  `defusedxml` would add a new dependency to the offline set, so it was left as a recommendation.
- **B104, bind to 0.0.0.0** in `api/auth.py`. This only happens when an administrator turns on "Network access" in
  Settings. The default is 127.0.0.1.

## Residual risks and recommendations

- **Unattended runs are not guarded by default.** The payment and booking guard is opt-in for scheduled runs
  (`playback.guard_commits_unattended`), because existing regression suites on test shops click "Place order" on
  purpose. Turn it on for any schedule that can reach a production payment page.
- **Look-alike controls on the page under test.** `components.js` runs inside the page under test, so a hostile page
  could show confusing controls to it. It cannot read SkyGuard data, but runs against untrusted sites should still use
  a separate browser profile, which is already the default.
