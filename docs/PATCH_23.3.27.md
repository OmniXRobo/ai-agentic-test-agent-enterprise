# Patch 23.3.27: Live dashboard

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.26. This is milestone 6 of 8 of the JMeter performance-testing
module. A running test now has a live dashboard that shows what JMeter has recorded so far, every 2 seconds.

## What is new

**Performance Testing > Live Dashboard**

* Opens the running test, or the next queued one. With nothing running, it lists recent runs and opens the live
  view by itself as soon as a test is queued.
* Starting a test from a plan or the script editor now opens its live dashboard. The run details page has a "Live
  dashboard" button while the run is queued or running. One-user validation runs still open the run details.
* Header: state (queued with its position, pre-run checks, running, stopping, ended), environment, start time, a
  progress bar against the planned duration (when the duration is known), and the Stop and Emergency stop buttons.
* Tiles: elapsed time, active users (the threads JMeter reports) next to the users planned at this moment, requests
  per second, 95th percentile and error rate over the last 30 seconds, with totals so far.
* Charts, one point per second (long runs are merged so at most 600 points are drawn):
  * active users, with the planned users from the workload shape as a dashed line;
  * requests per second, with the request-rate target as a dashed line when the plan has one;
  * 95th percentile response time per second, with the plan's 95th percentile target as a dashed line;
  * errors per second.
* Targets, last 30 seconds: each target of the plan (average, 90th, 95th, 99th percentile, error rate, throughput)
  and the request-rate target, shown as within or above. **This is a live indication only**: the verdict is decided
  from the complete results after the run, as before.
* Requests table (samples, errors, average, maximum, requests per second and 95th percentile now), top errors and
  the latest errors with their time, and the load generator's CPU and memory with the saturation warning.
* When the run ends, the last figures stay on the page with a link to the run details (verdict, percentiles, JMeter
  report).

**Where the figures come from**

* Everything is read from the results file JMeter is writing (results.jtl). Nothing is estimated or filled in.
* Samples are counted at their start time, so the last seconds fill up as slower requests finish; those seconds are
  marked as still open.
* Transaction controller samples are counted apart from requests (they would count every request twice).
* A second in which no request started shows as 0 requests per second, not as a gap.
* Early in a run the requests per second are over the seconds seen so far, not the whole 30-second window.
* Bounded for soak runs: figures are kept per second; raw response times only for the last 30 seconds and for the
  seconds still open.

## API

* `GET /api/performance/runs/{id}/live`: `run` (state, queue position, environment, times), `dashboard` (while
  JMeter runs, otherwise null: elapsed, progress, active and planned users, totals, `window`, `watch`, `timeline`,
  `planned_curve`, `requests`, `top_errors`, `recent_errors`) and `generator` (CPU and memory samples and flags).
* The short live summary kept on the run (run lists, events) has the same keys as before.

## Tests

`tests/test_perf_live_23327.py`: a synthetic results file read in two parts (a half-written last row is not read):
totals, transactions counted apart, a second with no samples, open seconds, per-second 95th percentile, rolling
window percentiles and error rate, target indications, per-request figures, top and latest errors; long runs merged to
at most 600 points with bounded memory; the rate early in a run; planned users from thread groups; unknown run 404;
and a real JMeter 5.6.3 run against the local test site, polled while it runs (samples, users, planned curve,
transactions, targets) and after it ends.

UI checked in Chromium (desktop and phone width): no test running, a 3-step stress run while it runs, and after it
ended. No script errors.

Not tested here: Windows (the same code runs in CI on Windows), long soak runs in the browser.
