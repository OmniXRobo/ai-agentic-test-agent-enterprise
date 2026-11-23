# Test report: SkyGuard QA 23.14.1

Run on Linux (Python 3.13, Chromium), 2026-11-22, on the 23.14.1 tree (commit after `55ff668`).

## Full regression

`pytest tests/` (the extension end-to-end test excluded: it needs a desktop browser with the extension loaded):

| Result | Count |
|---|---|
| Passed | 1,182 |
| Failed | 1, then 0 (see below) |
| Skipped | 402 (installer tests of other versions, Windows-only tests, JMeter) |

The one failure was in the new installer test `test_cumulative_patch_231401.py::test_wrong_version_is_refused`: it
used "23.14.1" as an example of a version that is not a release, but 23.14.1 is now this release (Setup rightly
treated it as a repair). The test now uses 23.14.2 and passes. No application code changed for it.

## New tests

* `tests/test_tfs_speed_231401.py`: 15 passed.
  * one TFS client reused for every call; the TLS context built once
  * no credential-store reads on warm calls; a changed PAT or project used at once
  * four concurrent requests share one set of TFS calls; an error is shared and not cached
  * slow TFS: the Dashboard answers within its budget with every non-TFS figure, then the TFS figures arrive
  * each person's own project is used (Dashboard and background threads); a plain thread falls back to the shared one
  * Connection Test: six checks, six requests (no home page), unreachable server, wrong PAT
  * 100 request threads; TFS status light per person
* `tests/test_cumulative_patch_231401.py`: 40 passed (after the fix above), including apply / verify / rollback / apply
  again and Setup's detect on a real **23.14.0** tree (the version installed on your PC) and on every earlier release
  from 23.3.35; the installer's new check "TFS speed and personal PAT / project in background work (23.14.1)"; the
  Arabic voice kept.

## Measurements

Simulated Azure DevOps, 200 ms per request, signed-in person with their own PAT and project:

| | 23.14.0 | 23.14.1 |
|---|---|---|
| Connection Test | 7 requests, 1.51 s | 6 requests, 0.65 s |
| Dashboard, empty cache | 17 requests, 70 credential reads, 2.85 s | 10 requests, 0 credential reads, 0.81 s |
| Dashboard, cached | 40 credential reads, 0.47 s | 0 credential reads, 0.01 s |

Browser (Chromium, signed in as the administrator with a personal PAT and project), TFS at 3.5 s per request:

| | 23.14.0 | 23.14.1 |
|---|---|---|
| Dashboard fully shown | 36 s on "Loading..." | 9 s |

With TFS slower than the 8 s budget the Dashboard shows everything else at once with "TFS figures loading…" and fills
the figures in when TFS answers (checked in the browser, no page errors).

## Not executed here

* Real Azure DevOps Online / Azure DevOps Server (no access from this environment) and your PC (it could not be
  reached), so the real-world gain was not timed.
* Windows Setup itself (the CI job runs it on Windows, now including the 23.14.0 baseline).
