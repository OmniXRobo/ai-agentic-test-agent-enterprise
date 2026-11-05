# Patch 23.3.26: Workload and validation run

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.25. This is milestone 5 of 8 of the JMeter performance-testing
module. A script now gets its load from a ready-made shape instead of hand-edited thread groups, can hold a
request-rate target, and can be checked with one user before any load is sent.

## What is new

**Script editor > Workload tab**

* Shapes:
  * **As in the script**: the users, ramp-up and duration set on the script's own thread groups (as before).
  * **Load**: ramp up to the users you expect, then hold.
  * **Stress**: add the same number of users in steps (for example 3 users every 60 s, 4 steps: 3, 6, 9, 12).
  * **Spike**: a steady baseline with a burst of extra users at a chosen moment.
  * **Soak**: a steady load for hours (the tab says so when the hold is shorter than an hour).
* A chart shows the planned concurrent users over time, with the peak and the total duration. "Thread groups in the
  run" lists each phase (users, start, ramp-up, how long it runs).
* The shapes use only JMeter's normal thread group. In the copy JMeter runs, each of the script's thread groups is
  copied once per phase, with all its requests, and given that phase's users, ramp-up, start delay and duration.
  When a script has several thread groups, the users are split in proportion to the users each has in the script.
  **The saved script is not changed**, and no new script version is made.
* Saving a workload never starts a run. The test type of the plan follows the shape.
* The environment's limits (most users, longest duration) and the allowed hosts are checked against the shape.

**Request-rate target (load and soak shapes)**

* Choose "Request-rate target", then the requests per second for all requests together and the user pool (the most
  users busy at once).
* JMeter's built-in Precise Throughput Timer paces the user pool to that rate. Users are never added to reach the
  rate: when responses slow down the achieved rate drops, and the run page shows the achieved rate (requests per
  second after the ramp-up, transaction samples left out) next to the target. Concurrent users and requests per
  second are kept apart everywhere; config.json records the load model in words.
* After a validation run the tab estimates the rate one user can make (requests in one iteration over the time it
  took) and how many users the target needs. If the pool is too small, the pre-run checks warn.
* Stress and spike tests use concurrent users only.
* Open Model thread groups stay blocked: they start users at a rate with no upper limit on concurrent users, so the
  load limit cannot be checked before the run.

**One-user validation run**

* On the Workload tab ("Run validation (1 user)") or on a scenario ("Validate (1 user)").
* Every thread group runs once with one user, one after another (setUp and tearDown groups too). Throughput timers
  are switched off; think-time timers stay. The run goes through the same pre-run checks (tools, hosts,
  environment) as a load run and is bounded to 10 minutes.
* Correlation check: for each request with its own extractor (regular expression, JSON or boundary), a check is added
  to the run's copy that fails the request when the extractor found nothing and used its default value. Extractors
  on a controller or thread group are not checked (they read many responses). Added in the run's copy only.
* The run page shows "Validation PASS / FAIL" instead of a performance verdict, and lists every request with its
  status code, time and why it failed ("Correlation: sid not found in the response", assertion messages).
  Transactions are listed but not counted as requests. Response bodies are not kept (they can hold personal data).
* The result is kept on the script with its version. A load run of a version that has not passed a validation run
  gets a WARN pre-run check (it is not blocked).

## API

* `GET /api/performance/workload/shapes`
* `POST /api/performance/workload/preview` {workload, plan_id}: planned users over time, peak, duration, problems
* `PUT /api/performance/plans/{id}/workload` {shape, model, users, ramp_up_s, hold_s, steps, step_s, spike_users,
  spike_at_s, spike_s, spike_ramp_s, target_rps}
* `POST /api/performance/plans/{id}/validate`, `POST /api/performance/scenarios/{id}/validate`
* Plans carry `workload` and `validation`. Run metrics carry `validation` (validation runs) and `rate` (rate runs).
  The run folder has `validation.json`; config.json has `workload.load_model`, `workload.shape`, `workload.applied`
  and `validation_run`.

## Tests

`tests/test_perf_workload_23326.py`: shapes as thread group phases (stress steps, spike overlap, peak and duration),
problems (spike after the end, a rate with stress, no target), applying a shape keeps every request and splits users
and rate over two thread groups, the validation copy (one user, one loop, serialized, throughput timers off,
correlation checks), saving a workload makes no version and no run, environment load limit on the shape, and three
real JMeter 5.6.3 runs against the local test site: a validation run that reports the missing correlation (the
server saw exactly one session and one profile request) and passes after the fix; a stress run with 3 steps that
peaks at 3 users; a rate run of 3 users paced to 6 requests per second for 20 s (the server saw about 120 requests).

Not tested here: long soak runs, and Windows (the same code runs in CI on Windows).
