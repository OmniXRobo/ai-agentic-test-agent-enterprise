# SkyGuard QA 23.6.0: test report (actual runs)

**Run on:** 13 Nov 2026.
**Environment:** Linux container, Python 3.13, Playwright 1.63 with Chromium. Visible-browser tests ran under Xvfb.

Every result below comes from an actual run. Nothing is simulated or copied from earlier releases.

## What could not be tested here

- **Live sites:** trip.com, makemytrip.global and saucedemo.com. The build environment's network proxy blocks them
  (HTTP 403).
  - Their behaviour was reproduced with local test pages (`tests/site/components/`): no ids, generated or duplicate or
    changing ids and classes, portal lists, delayed suggestions, two-month calendars with prices in the labels,
    disabled past days, a guests pop-over, an SPA route with a spinner, an iframe, a shadow root and a modal.
  - The Swag Labs case is reproduced with a snapshot of its six look-alike "Add to cart" buttons.
  - **Please re-run your two recordings on your PC after installing.** Use Semantic upgrade, or record again.
- **Windows itself:** the `.cmd` / `.ps1` wrappers were not run (no Windows available). They call the same
  `install_patch.py`, which was run. `tests/test_ops_windows.py` parses the new `.ps1` on Windows CI.
- **A real local Qwen model:** AI Test Design was tested against a scripted OpenAI-compatible server (`tests/fake_llm.py`)
  that can return slow, malformed or timed-out answers. The generation code under test is the real code.
- **No real booking or purchase** was made. The booking test stops at a local "Book now" button, and the guard blocks
  it.

## Full regression suite

`pytest tests/` (745 tests collected), run once over the finished code:

| Result | Count |
|---|---|
| Passed | 709 |
| Skipped | 25 (Windows-only, or optional tools not installed; the same skips as before the change) |
| Failed | 1: `test_ops_patch_install::test_apply_verify_rollback_and_apply_again`. This builds the old 23.5.0 package from the working tree, which now says 23.6.0. It is now marked superseded and covered by the cumulative patch tests below. |
| Error | 1: `test_voice_security::test_spoken_secrets_are_never_stored_or_logged`. My run used `-p no:logging`, which removes pytest's `caplog` fixture. Re-run normally: **passed**. |

Before the change, the baseline run on the same machine had **667 passed, 25 skipped, 0 failed**. The 23.6.0 run adds
the new tests and has no regressions.

## New tests (all passed)

| Group | File | Tests | What they prove |
|---|---|---|---|
| A. Component recognition | `test_adaptive_components_23600.py` | 21 | Native select and date input. React Select, MUI, Ant, Bootstrap and async multi-select. An ambiguous search choice is reported with its options, and an unknown option is reported as missing. Lists inside a modal, an iframe and a shadow root. Duplicate ids. A number-only calendar with month navigation. Past and disabled dates are refused. A hotel date range. A full legacy hotel and flight recording replayed through the real Runner. The booking guard. A past recorded date fails clearly. The semantic-upgrade endpoint. A visible run that pauses with choices, gets an answer and passes. |
| B. AI Test Design | `test_design_generate_23600.py` | 8 | The 600 s generation timeout and schema are used on every call. 7 criteria are generated in parts with unique R and TC ids. A timeout names the stage and the setting to change. Malformed output fails honestly with its stage. Empty story and invalid ids are rejected. A missing story fails. The result is saved and reopens. |
| C. Open script | `test_open_script_23600.py` | 8 | `FWS-` routing. A helpful API error. Unicode and space paths. BOM and Windows-1252. Missing file, folder, permission denied, binary file. Traversal refused. Opening writes nothing, never recreates a missing file, reports `.txt` as invalid, and leaves a binary file untouched. |
| D. Ops agent | `test_ops_agent_choices_23600.py` | 5 | An exact unique `data-test` picks the right "Add to cart" of six. Similar ids alone do not decide. True duplicates are named by product. A chat choice is validated, used once and not remembered. The chat page is simplified and its text is not rendered as HTML. |
| E. Self-healing | `test_healing_stable_23600.py` | 2 | Prices and "N left" do not lower the score. `healing.py` has no platform imports. |
| F. Cumulative patch | `test_cumulative_patch_23600.py` | 8 | Package contents and checksums. No network and no admin in the installer. Wrong version refused. Changed file refused. Automatic rollback after a failure. Apply, verify, rollback and apply again from **23.3.35, 23.4.0 and 23.5.0**, each keeping every database row, reaching 18 migrations and creating the knowledge database. |

The 8 cumulative patch tests were added after the full run started, so they were run separately: 8 passed.

## Offline and no-administrator install check

`packaging/cumulative-patch/test_offline_install.sh`. Full output: `evidence/offline_install_check.txt`. It ran in a
network namespace with no internet:
- The zip checksum matches, and 116 files match `SHA256_CHECKSUM.txt`.
- **23.3.35** (schema 16), **23.4.0** and **23.5.0** installations were each patched to 23.6.0 with every verify line
  PASS (63 PASS, 0 FAIL).
- The patched app started offline:
  - `/api/health` reports 23.6.0;
  - the dashboard returns HTTP 200;
  - `GET /api/automation/scripts/FWS-B211AC4E14` returns the new helpful `SCRIPT_ID_WRONG_PAGE` message.
- Rollback to 23.3.35 and apply again both worked.
- A 23.4.0 installation owned by the unprivileged user `nobody` was patched **as that user**.

## Security checks

See `SECURITY_AUDIT.md`:
- `pip-audit`: no known vulnerabilities.
- bandit: 0 high severity; the 10 medium findings were reviewed and none are exploitable.
