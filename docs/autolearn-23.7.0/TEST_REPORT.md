# SkyGuard QA 23.7.0: test report (actual runs)

**Run on:** 14 Nov 2026.
**Environment:** Linux container, Python 3.13, Playwright with Chromium (headless).

Every result below comes from an actual run over the finished code.

## Full regression suite

`pytest tests/` (782 tests collected):

| Result | Count |
|---|---|
| Passed | 741 (+1 added afterwards, see below) |
| Failed | 0 |
| Skipped | 41 |

The skips: 10 Windows-only tests (`test_ops_windows.py`: DPAPI, Credential Manager, PowerShell parsing of every
`.ps1`, including the new `UPDATE_EXTENSION.ps1`), 13 tests of the superseded 23.5.0 and 23.6.0 patch packages (their
installers refuse a 23.7.0 tree; the 23.7.0 package replaces them), and 18 other environment-dependent skips (for
example JMeter not installed).

After the full run, a last small change (the refusal page is no longer listed among the screens a role uses) was
followed by a rerun of every agent, extension and companion test: **107 passed, 1 skipped** (host name resolves to
loopback). One companion test was then added for the extension's learning endpoints (below): **1 passed**.

## Extension companion (`tests/test_companion_security.py::test_learn_while_browsing_through_the_companion`)

Through the real localhost API with pairing and sign-in: `/v1/observe` stores the application's pages and actions and
drops another site's click and page; the password typed on the sign-in page appears in no file of the companion's
data folder; malformed input is refused (400); the final observation triggers the analysis; a viewer can read
`/v1/understanding` but cannot send observations or clear the journal (403); an admin can clear it.

## Learning by watching (`tests/test_passive_learning_23700.py`, 26 tests)

A person (`tests/passive_session.py`) uses the FleetOps test application (`tests/transport_site.py`) in a browser that
SkyGuard opened. They are not told anything about the agent and never name a task:

1. **Entry Operator:** creates trips T100 and T101, tries a duplicate T101 (refused), double-books BUS-101 (refused),
   tries a date in the past (refused), then saves T102, opens the trip list and signs out.
2. **Manager:** opens *New trip* (refused: "Your role cannot create trips"), approves T100 and T101, signs out.
3. **Second-Level Manager:** authorizes T100.

The agent only watches. From that alone, the tests check that it found:

| Check | Result |
|---|---|
| Pages and actions recorded; passwords absent from every stored observation; every secret field stored without a value | pass |
| Screens and their kinds (sign-in, form, list, refused) with `{id}` templates | pass |
| *Create trip*: 6 parameters in screen order, options, example values, steps, success message, key field, verification in the trip list, starting status *Planned* | pass |
| *Approve trip* and *Authorize trip* as row actions with required and resulting status | pass |
| Lifecycle *Planned → Approved → Authorized* with task and role per step | pass |
| Rules from the application's messages: Trip No unique; a bus cannot be booked twice at overlapping times; departure date today or later | pass |
| Roles: Entry Operator, Manager, Second-Level Manager; who did what; what was refused; sign-in page and identity | pass |
| Findings proposed as knowledge: workflows seen consistently are *awaiting approval*, rules *discovered*; nothing approved by itself | pass |
| 13 chat questions answered correctly from the model (summary, how, who, what next, statuses, rules, fields, screens, record status, counts, roles, double booking) | pass |
| Unknown question shapes fall back to retrieved facts; with the LLM on, page text reaches it only as wrapped untrusted data | pass |
| "add 1 trip T300 …" → *needs approval* → approve once → preview → confirmed → **T300 created and verified in the application** | pass |
| A later double booking inside the learned window is flagged in the preview before anything is submitted | pass |
| Single-page app: dialog flow learned; "Add passenger" and stepper clicks stay steps, not tasks | pass |
| Word forms (*authorises* → authorize), helpers | pass |
| SkyGuard API routes: watch start/stop/list, understanding, digest, journal clear (admin) | pass |

The full conversation and run of one such session are in `evidence/learn_by_watching_transcript.txt`.

## Cumulative patch (`tests/test_autolearn_patch_23700.py`, 9 tests)

Built from this tree and installed on real 23.3.35, 23.4.0, 23.5.0 and 23.6.0 trees (git archive of each release
commit), each with a database:

| Check | Result |
|---|---|
| Package contents, manifest (23.7.0, four baselines, extension 1.1.0), CRLF for `.cmd`/`.ps1`, checksums including the extension update | pass |
| Installer has no network imports; no file in the patch folder asks for elevation, services, `HKLM` or machine-wide paths | pass |
| Wrong version refused; a changed patch file refused before anything changes | pass |
| Simulated failure after copying: previous version and database restored automatically | pass |
| Apply → verify → rollback → apply again on each of the four versions; knowledge database migrated to schema 2 with the earlier agent knowledge kept; after rollback 23.5.0 and 23.6.0 open the schema 2 database | pass (4/4) |

## Offline installation (`evidence/offline_install_check.txt`)

`packaging/autolearn-patch/test_offline_install.sh` in a network namespace with only loopback (internet confirmed
unreachable): zip and all 175 files checked; each of the four versions patched and verified with every check PASS;
the patched application started offline and answered `/api/health` with version 23.7.0; rollback and re-apply; and a
fresh 23.4.0 installation owned by an ordinary user (`nobody`) patched as that user.

## What could not be tested here

- **Windows itself:** the `.cmd`/`.ps1` wrappers, including `UPDATE_EXTENSION.ps1`, were not run (no Windows here).
  The patch wrappers call the same `install_patch.py` that was run. `test_ops_windows.py` parses every `.ps1` on
  Windows.
- **Your applications:** learning was tested on the FleetOps test application and a synthetic single-page app. Please
  try *Learn while I browse* on your own test environment and look at the Understanding tab.
- **The extension's automatic observation end to end in Chrome:** the background script's watch-and-flush loop was
  not driven by a test. The page reader it uses and the companion endpoint it posts to are tested (above).
- **A real local Qwen model:** the LLM fallback was tested with a scripted model. Learning and the deterministic
  answers do not use the model.
