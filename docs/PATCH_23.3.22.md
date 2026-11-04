# Patch 23.3.22: Performance Testing foundation

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.21. This is milestone 1 of 8 of the JMeter performance-testing
module. It adds the base that the later milestones build on: script editor, network recording, data binding, live
dashboard, PDF reports and performance schedules.

## What is new

**Performance Testing > Performance Scenarios (new page)**

* A scenario is one performance journey: name, description, project, target environment, linked user story,
  acceptance criteria (performance), test suite, related functional script, tags, its script (performance plan),
  and its run history.
* Create, edit, clone (the copy gets its own copy of the script, so editing one never changes the other),
  archive / restore, search (name, story, project, tag, description) and filter by test type and environment.
* Every existing performance plan gets a scenario with the plan's name when the application starts. No plan or
  run is lost; earlier runs are linked to it.
* Saving a scenario or a plan never starts a load test.

**Target environments and load limits** (on the Scenarios page)

* Name, base URL, allowed hosts, maximum concurrent users, optional maximum duration, and "public site we do not
  own".
* Before every run, the hosts of the requests must be in the allowed hosts, and the peak concurrent users must be
  within the limit. Scenarios without an environment are limited to 50 users.
* For public demo sites such as saucedemo.com and the OrangeHRM demo, mark the environment as a public site and
  keep the limit at 1 user. These sites belong to other companies and are shared by many testers.

**Pre-run checks** (Check before run on a scenario, and automatically before every run)

* Java (8 or later) and JMeter (5.5 or later), with the versions found.
* JMeter plugins: none needed, because generated plans use built-in JMeter elements only.
* The JMX is valid XML with a test plan, a thread group and at least one request.
* Disk space where results are written (at least 1,024 MB, setting `performance.min_free_disk_mb`).
* Allowed hosts, the users limit, the duration limit and a warning for public sites.
* Any FAIL blocks the run before any request is sent. The run is recorded as Failed, with the checks.

**Run queue and stopping**

* Run now queues the run. Runs start one at a time on this PC (the load generator), oldest first.
  Performance Results shows the queue with each run's position. A queued run can be cancelled.
* Stop: JMeter stops cleanly and writes its results and HTML report (ended after 30 s if it hangs).
* Emergency stop: the JMeter process tree this application started is ended at once. Results so far are kept; the
  HTML report may be missing.
* Execution timeout: the test's duration plus 15 minutes.
* Closing the browser does not stop a run. Closing the application stops the running test and records it as
  Interrupted.

**Execution state and verdict, kept apart**

* State (what happened to the run): Queued, Validating, Running, Stopping, Completed, Failed, Cancelled,
  Interrupted, Skipped.
* Verdict (what the numbers say): Targets met, Targets missed, Target not specified (no limits set; the
  measurements are reported without PASS or FAIL), Inconclusive (the run did not complete, so its partial numbers
  cannot pass or fail it).
* Earlier runs are converted: their single status is split into state and verdict.
* The Results list filters by state, verdict, test type, manual or scheduled, and run ID / plan.

**Run snapshot** (in each run folder, also downloadable from the run page)

* `plan.json` and `plan.jmx`: the exact plan that ran.
* `config.json`: scenario, environment, overrides, application version, Java and JMeter versions, the workload
  (thread groups, peak concurrent users, duration, think time, "closed model: concurrent virtual users, not a
  request-rate target"), the targets, pre-run check result and the SHA-256 of the JMX.
* The plan is captured when the run is queued, so editing the plan while a run waits does not change that run.

**Load generator monitoring**

* CPU, memory and free disk of this PC and the JMeter process, every 2 seconds, in `generator.json` and on the run
  page with charts.
* Flagged as saturated when CPU stays at or above 85% for 6 seconds or more, memory reaches 90%, or less than
  512 MB of disk is left. Response times measured on a saturated generator can be inflated.
* Server and database metrics show "not configured": no collector is set up, and none are invented.
* Needs psutil 7.2.2, which this patch installs offline. Without it runs still work and the page says the
  generator was not monitored.

**Recovery after a restart or crash**

* A run that was validating, running or stopping becomes Interrupted, keeping its partial results (verdict
  Inconclusive).
* If its JMeter is still running, it is ended. It is found by its process ID and its run folder, so another Java
  process is never touched.
* A run still waiting in the queue becomes Skipped. A load test never starts by itself after a restart; use Run
  now again.

## Database

Migration 12 adds `perf_environments`, `perf_scenarios`, and these `perf_runs` columns: state, verdict, partial,
scenario_id, environment_id, trigger, queued_at, overrides, config, checks, generator, pid and state_reason. The
old `status` column is still written (PASSED / FAILED), so reports, analytics and defect drafts work as before.

## API

* `GET/POST /api/performance/environments`, `PUT/DELETE /api/performance/environments/{id}`
* `GET/POST /api/performance/scenarios` (`q`, `kind`, `environment_id`, `archived`),
  `GET/PUT /api/performance/scenarios/{id}`, `POST .../clone`, `POST .../archive`, `POST .../check`,
  `POST .../run`
* `POST /api/performance/plans?scenario_id=` adds a script to a scenario
* `GET /api/performance/queue`, `POST /api/performance/runs/{id}/cancel`,
  `POST /api/performance/runs/{id}/stop` with `{"mode": "graceful" | "emergency"}`
* `GET /api/performance/runs` adds `scenario_id`, `environment_id`, `state`, `verdict`, `trigger` and `q`
* `POST /api/performance/plans/{id}/run` now queues the run (202 with `state: QUEUED`) instead of refusing while
  another run is active.

## Dependencies

* psutil 7.2.2 (new, `psutil-7.2.2-cp37-abi3-win_amd64.whl` in the patch's `wheels` folder, SHA-256
  `eb7e81434c8d223ec4a219b5fc1c47d0417b12be7ea866e24fb5ad6e84b3d988`).
* Unchanged: JMeter 5.6.3 and Temurin JRE 17 (offline package), no JMeter plugins.

## Verification

* `tests/test_perf_foundation_23322.py` was run against a real JMeter 5.6.3 on the bundled local test site. It
  covers: scenario create, edit, clone, archive and search; host, users and duration limits blocking a run before
  any request; the queue order and cancel; Completed with Target not specified; Completed with Targets missed; the
  config.json snapshot; generator monitoring; Stop and Emergency stop with partial results and an Inconclusive
  verdict; application close leading to Interrupted; the upgrade of earlier plans and runs; and a simulated crash,
  where JMeter left running is ended on the next start while an unrelated process is left alone.
* The existing performance tests (`tests/test_performance.py`) pass unchanged, including the real JMeter runs.

## Not in this patch (next milestones)

The script editor and JMX import (23.3.23), network recording and correlation (23.3.24), test data and binding
(23.3.25), workload templates and the one-user validation run (23.3.26), the live dashboard (23.3.27), the
performance PDF, comparisons and defects (23.3.28), and performance schedules with start at Windows sign-in
(23.3.29).
