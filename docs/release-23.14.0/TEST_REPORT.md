# Test report: SkyGuard QA 23.14.0

Every result below comes from a run that actually happened on 22 November 2026. The JUnit files are in
`test-results/`. The 23.13.3 report is in `previous-23.13.3/TEST_REPORT.md`.

Environment: Linux container (Python 3.13, pytest, Chromium through Playwright), real **ActiveMQ Classic 5.18.6** and
**ActiveMQ Artemis 2.37.0** brokers running locally, NSIS 3 for Setup.exe. Nothing here talked to the office intranet.

Each result is labelled:

* **Real**: run against the real product or protocol.
* **Simulator / mock**: a stand-in built for the test (the protocol is real, the product is not).
* **Not executed**: could not be run here (no device, no Windows).
* **Needs intranet**: must be checked on the office network.

## 1. New in 23.14.0

| Test file | Result | What it covers |
|---|---|---|
| `tests/test_security_integration_231400.py` | **24 passed** | Security, Fuse, ActiveMQ, result actions, email (details below) |
| `tests/test_ui_231400.py` | **17 passed** | The new screens in a real browser (Chromium) |
| `tests/test_agent_workflow_looks_231400.py` | **7 passed** | Agent Workflow looks, Stop / Pause / Resume |
| `tests/test_cumulative_patch_231400.py` | see section 3 | Installer on every earlier release, Arabic voice kept |

### Security Testing

| What | Label | Result |
|---|---|---|
| Authorization needed before a run; Production cannot have state-changing tests | Real (API) | PASS |
| Secrets refused in the configuration, kept in the vault | Real | PASS |
| Discovery, passive and active findings on a deliberately vulnerable app (XSS, SQL error, path traversal, open redirect also after a redirect, CORS reflection, headers, cookies) | Mock app (httpx MockTransport) | PASS |
| Finding review, regression marks fixed only for rules that ran | Mock app | PASS |
| View-only account cannot start or change anything | Real (API) | PASS |
| OWASP ZAP: alerts in scope imported, out of scope dropped; ZAP must be on loopback | **Simulator** (fake ZAP API) | PASS. Real ZAP: **needs intranet** |

### ActiveMQ

| What | Label | Result |
|---|---|---|
| Jolokia whitelist refuses purge / remove / move / retry / pause ... | Real (code) | PASS |
| DLQ / system destinations refused as test destinations; publishing only to test destinations | Real | PASS |
| STOMP header escaping, CR / LF refused | Real | PASS |
| Discovery, publish and verify (JSON path, Arabic text), selector, priority, performance, TTL, security on **Classic 5.18.6** | **Real broker** | PASS (the security test correctly reports a FAILED finding: Classic accepts anonymous STOMP by default) |
| The same on **Artemis 2.37.0** with login required | **Real broker** | PASS (security PASS; TTL UNVERIFIED when the broker had not yet removed the expired message, as designed) |
| Consumer delivery and retry / DLQ on both brokers | **Real broker** | PASS |
| Results with the unified actions in the browser (evidence, email draft) on Classic | **Real broker** + Chromium | PASS |

### Red Hat Fuse

| What | Label | Result |
|---|---|---|
| End-to-end A → route → ActiveMQ → B with correlation ID, comparison rules, DLQ for a poison message, on Classic and on Artemis | Real brokers, **route stand-in** (Python process in place of Fuse) | PASS |
| Discovery and mapping through Camel Jolokia (only `dumpRouteAsXml` executed; no state-changing call) | **Simulator** | PASS. Real Fuse on Karaf / EAP / Spring Boot: **needs intranet** |
| Transformation comparison rules (ignore, mappings, tolerance, dates, order, extra fields, XML without DTD) | Real (code) | PASS |
| An entry that is not a test destination is refused | Real | PASS |

### Result actions and email

| What | Label | Result |
|---|---|---|
| Analyze with AI keeps verified facts apart from hypotheses; history | Mocked local AI | PASS |
| Email draft: recipient policy (403 outside the allowed domains), single send (a second press does not send again), failure keeps the draft as failed | **SMTP test transport** | PASS. Real SMTP: **needs intranet** (Settings > Email / SMTP > Test connection) |
| PDF report for a finding (offline printer) | Real (Chromium) | PASS |
| Path tricks in ids and report kinds refused | Real | PASS |
| Passed results show evidence, PDF and email only; failed show all six | Chromium | PASS |

### Screens, dashboard, voice

| What | Label | Result |
|---|---|---|
| Security Testing menu (8 screens) and Integration Testing group render without script errors | Chromium | PASS |
| Fuse and ActiveMQ pages have the 8 tabs, each opens without errors | Chromium | PASS |
| Transformation comparison in the browser | Chromium | PASS |
| Email / SMTP intranet fields saved (allowed domains, developer directory), send history shown | Chromium | PASS |
| Dashboard section for Security / Fuse / ActiveMQ / shared actions, existing widgets kept, click opens the screen | Chromium | PASS |
| Voice: English and Arabic commands open the new screens; `/api/voice/interpret` executes and stores nothing | Real (code) | PASS |
| **Microphone test** in a browser without a microphone reports FAIL ("No microphone is connected"), never PASS | Chromium | PASS. With a real microphone: **not executed** |
| Arabic transcription with `small.ar` | | **Not executed** (no audio device or model here) |

## 2. Regression

The whole suite (every test file in `tests/`, except the new installer test which is in section 3), with the real
brokers available:

**1,134 passed, 1 failed, 359 skipped** in 41 minutes. The one failure was `test_regression_pages.py::
test_every_page_loads_like_before`: it lists the pages that existed before, and the ten new pages were not in its
list yet. The test was updated to include them and **passed** when run again. Final result: **0 failures**.

The 359 skips are expected and unchanged in kind: the installer tests of earlier releases and of older patches that later
ones replaced (each only runs on its own version: 313), Windows-only tests (16), JMeter not installed (10), the OCR
runtime that ships for Windows (9), IPv6 / display / DNS conditions (3) and a few other environment conditions (8).

The baseline (23.13.3) full run before this work did not finish: the test worker restarted at 79% (one failure had
been printed by then; it could not be identified afterwards). The comparison is therefore made per test file: every
file that existed in 23.13.3 passes in 23.14.0.

## 3. Installer

`tests/test_cumulative_patch_231400.py` builds the patch from this checkout and installs it on real trees of every
release (git archive of each release commit, with a database that has data): **38 passed** (36 in the first run; two
tests failed because of mistakes in the tests themselves, not the installer: one used "23.14.0" as an example of an
unknown newer version, the other looked for the backup in the wrong folder. Both were corrected and passed).

| What | Result |
|---|---|
| Apply, verify, rollback and apply again on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1, 23.11.2, 23.12.0, 23.13.0, 23.13.1, 23.13.2 and 23.13.3 (19 releases) | PASS: version 23.14.0, migration 23, the new tables, every check including "Security, Fuse, ActiveMQ, result actions, voice (23.14.0)" and "Arabic voice kept", no row lost, knowledge database kept, rollback restores the release |
| A working Arabic setup (Faster-Whisper with `small.ar` and `small.en`, Piper with Kareem, Arabic voice settings) is detected, every file and the voice settings are byte-for-byte the same after the upgrade, `VOICE_BEFORE.json` recorded, and applying again keeps it | PASS |
| No Arabic model or voice: reported with the helper to run ("nothing was downloaded"), nothing created | PASS |
| Wrong or unknown version refused, nothing changed; changed patch file refused; failure after copying restores everything | PASS |
| Offline OCR runtime staged, swapped and rolled back; damaged wheel refused | PASS |
| Protected application folder never forced; side-by-side copy works | PASS |
| Setup's detect page on 23.3.35, 23.12.0, 23.13.0 to 23.13.3; repair over 23.14.0; log option | PASS |
| Installer needs no network and no administrator rights (no network import, no machine-wide paths) | PASS |
| Windows: Setup.exe, APPLY_PATCH.ps1 under Windows PowerShell 5.1 | **Not executed here** (the Windows CI job on the pull request runs them on 23.3.35, 23.11.1, 23.12.0, 23.13.0 to 23.13.3) |

## 4. Static security check

bandit on the new modules: see `SECURITY_REPORT.md` section 2 (one high finding fixed, the medium ones reviewed as
false positives).
