# Root cause: slow TFS and a Dashboard that stays on "Loading..." (23.14.1)

**Reported on 23.14.0 (installed over 23.13.2):** Connection Test takes a long time but passes every check against
Azure DevOps Online; the Dashboard stays on "Loading..." (TFS figures and the AI analysis panel); every TFS page is
slow. The account has its own PAT and project (23.13.0), and the organization has several projects.

The TFS code itself did not change in 23.14.0. The slowness comes from how 23.13.0's personal PAT / project was added
on top of a TFS layer that opened a new connection for every call, multiplied by the Dashboard asking for the same
figures several times at once. 23.14.0 also made the Dashboard wait for one more request, sent only after the others
had answered. We could not reach the reporting PC, so the cause was found in the code and confirmed with a simulated
Azure DevOps (measurements at the end), not on the PC itself.

## What happened on every TFS call (before 23.14.1)

| Step | Cost on a Windows PC talking to dev.azure.com |
|---|---|
| Look up the signed-in person's PAT in the Windows Credential Manager and their project in the database | 2 to 4 credential reads and 1 to 2 database reads **per call** (the cache key alone did it once more). A cold Dashboard made **70** credential reads, a warm one **40**. |
| Build a new TLS context: read the Windows certificate store and the certifi list | tens to hundreds of milliseconds, **per call** |
| Open a new HTTPS connection (TCP + TLS handshake to Azure DevOps) | one or more network round trips, **per call**; nothing was kept open |
| The request itself | one round trip |

So every TFS figure paid for a new connection, and the Dashboard needs about 15 of them (work item types, categories,
iterations, areas, 3 to 4 counts, the user stories with acceptance criteria, the criteria items with their links).

## Why the Dashboard never finished

1. **The same work was done two to four times at once.** The Dashboard sends `/api/dashboard/summary` and
   `/api/analytics/summary` together; both need the same TFS counts. The 60-second cache only helps *after* the first
   answer, so with an empty cache both requests (and the status strip, and every other open tab) made every TFS call
   themselves, in parallel. A cold Dashboard made 17 TFS calls where 10 are needed, each with its own connection.
2. **Everything waited for TFS.** The Dashboard showed nothing until both requests (and, since 23.14.0, the security /
   integration figures, which were asked only *after* the other two) had answered, so one slow TFS figure kept the
   whole page on "Loading...".
3. **The request threads ran out.** Each API request that is not async runs on a worker thread; the default is 40.
   Requests waiting on slow TFS calls (up to 30 s each, 30 s more to give up on an unreachable server) held those
   threads, and the browser itself only opens 6 connections to one site. Pages that never touch TFS (the AI analysis
   panel, notifications) and **Connection Test** queued behind them. That is why Connection Test eventually passed,
   but only after a long wait.
4. **Connection Test made an extra slow call.** It first fetched the organization's home page (an HTML sign-in page on
   Azure DevOps Online) only to see that the server answered, and then made its six checks one after another.

## Related: background work used the administrator's PAT and project

Test design, test case generation, test data generation, script generation, voice commands, reports, defect analysis
and agent runs start work on another thread. The signed-in person is not carried to a new thread, so that work used
the shared (administrator's) PAT and project. With several projects, a person working in their own project got
"not found" or another project's items. This is item 1 of the Multi-User Isolation plan
(`fix-plans/MULTI_USER_ISOLATION_FIX.md`); it is fixed here because it is part of "TFS does not fetch my data".
The other items of that plan (no silent fallback to the admin's PAT, owners on scripts / runs / schedules, one test
design run per user) are not in this patch.

## Fix (23.14.1)

| Where | Change |
|---|---|
| `tfs/client.py` | The TLS context is built once and reused (built again only when the CA file changes). Connect timeout 10 s (was 30 s). Connections are kept open (keep-alive pool). |
| `tfs/service.py` | One open client per server / project / PAT, reused by every TFS call; a failed connection is dropped so the next call starts fresh. The person's PAT and project (and the shared PAT) are read once every 30 s, and at once again after any change (Save, Users page, Remove PAT). The cache makes **concurrent requests for the same figure share one TFS call** (errors are shared too, and never cached). The Dashboard counts run in parallel and the total number of defects comes with them; the four project reads (work item types, categories, iterations, areas) go out together. |
| `tfs/service.py` Connection Test | Reachability and authentication come from one call; the collection, project, work item and test plan checks run at the same time. Same six checks, same results, one round trip fewer and three run together. |
| `reports/analytics.py`, `api/core.py`, `api/reports.py` | The Dashboard waits at most 8 s for TFS. If TFS is slower, everything else is shown and the TFS figures say "loading"; the TFS query carries on in the background, as the signed-in person, and fills the cache. Both Dashboard requests share the same figures. |
| `web/js/pages/dashboard.js` | The three Dashboard requests go out together; while TFS figures are loading the page asks again every 5 s (at most 20 times) and fills them in. |
| `main.py` | 100 request threads instead of 40, so slow TFS calls cannot block the rest of the application. |
| `health.py` | The TFS light in the status strip is kept per person and project. |
| `auth.py` (`carry_user`) and the places that start background work | Background work runs as the person who started it (their PAT and project). |

## Measured (simulated Azure DevOps with 200 ms per request, signed-in person with their own PAT and project)

| | Before | After |
|---|---|---|
| Connection Test | 7 requests, 1.51 s | 6 requests, 0.65 s |
| Dashboard, empty cache | 17 TFS requests, 70 credential reads, 2.85 s | 10 TFS requests, 0 credential reads, 0.81 s |
| Dashboard, cached | 40 credential reads, 0.47 s | 0 credential reads, 0.01 s |
| New TLS context + connection per TFS call | yes | no (one per server / project / PAT) |
| Dashboard with TFS at 3.5 s per request (browser test) | "Loading..." for 36 s | complete in 9 s |
| Dashboard when TFS takes longer than 8 s | "Loading..." until every TFS call ends | shown at once; TFS figures follow |

The simulation has no TLS handshakes and no Windows certificate store, so on a real PC the gain is larger: the
per-call connection and certificate store costs are gone entirely.
