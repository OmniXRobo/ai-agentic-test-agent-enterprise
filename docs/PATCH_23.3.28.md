# Patch 23.3.28: Reports, comparisons and defects

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.27. This is milestone 7 of 8 of the JMeter performance-testing
module. Every performance run now has a report, runs can be compared with a clear statement of what differs, and
defect drafts carry what a developer needs to reproduce and judge the problem.

## What is new

**Run report (PDF)**

* Made automatically after every run that ended: completed, failed, stopped or interrupted. A run that did not
  complete is marked as partial, with its evidence called incomplete.
* Run details > Run report: Open PDF, Open as a page, Download PDF, and Make again (from the saved results; nothing is
  re-run). The JTL, the executed JMX (plan.jmx), the saved script, the logs and JMeter's HTML dashboard are listed
  and downloadable as before.
* Sections: title, run ID, test type, start and end with the time zone; executive summary; objective and scope (from
  the scenario); target environment and application build; script version, test data versions and workload; actual
  load and duration; performance targets with the result of each; request and transaction results (average, 50th,
  90th, 95th, 99th percentile, maximum, rate) with errors and assertion messages; charts with units and legends;
  load-generator telemetry; a test-specific section; baseline comparison; findings with the measurement behind each;
  AI recommendations (only when an analysis was requested, labelled as analysis); defect references; result files;
  limitations and missing information; execution state and the separate performance verdict.
* Test-specific sections, measured per planned period from the JTL (requests only, by start time):
  * Load: behaviour at the expected workload.
  * Stress: each step, the scaling efficiency between steps and where signs of saturation start. Recovery is
    reported as not tested (the stress shape only steps up).
  * Spike: before, during and after the spike, and whether response times and errors returned to the level before.
  * Soak: thirds of the hold compared. A rise is reported as degradation; a memory leak or other cause is never
    stated as confirmed, because server-side measurements are not collected.
  * Scripts whose duration depends on loop counts are split into thirds of the observed run.
* Baseline: the latest earlier completed run of the same scenario and test type, with the change in each measure and
  the comparability notes.
* Secret scan: the PDF text is checked for the stored secret values of the run's script before it is saved. If one is
  found, the PDF is not saved and the run page says why. Error messages are also redacted.

**Comparison and consolidated reports**

* Performance Results: tick 2 to 6 runs (the first ticked is the baseline), then Compare, Comparison PDF or
  Consolidated PDF. Both PDFs are listed under Reports.
* Compare shows the runs side by side (with the change against the baseline), each request's 95th percentile and
  error rate, the targets, and what differs between each run and the baseline:
  * Not comparable: different test types, scripts or environments, or a run that did not complete.
  * Caution: the executed JMX changed, different peak users, workload shape, load model, duration or test data, a
    saturated load generator, actual durations more than 20% apart.
  * Note: different builds (expected when comparing builds), application or JMeter versions, or targets.
* The consolidated report puts runs of different test types (for example load, stress, spike and soak) in one PDF:
  an overview and then each run's full report.

**Run history**

* The build under test is recorded on every run: the build typed next to Run now on the scenario, otherwise the
  environment's "Application build deployed" (new field on the environment).
* Performance Results filters: scenario, environment, build, from and to dates, as well as state, verdict, test type
  and scheduled or manual. The search also finds builds.

**Defect drafts**

* A draft from a run that missed its targets now contains: summary; environment and build; the workload to reproduce
  it (users, duration, load model, thread groups, script and data versions); the affected transaction or request
  with its figures; expected against actual for each missed target; the run ID with start and end times; the result
  files and the run report (charts); and a suggested severity with the reason (1 when 10% or more of requests failed,
  2 for errors above the limit or a response time at least twice the limit, 3 otherwise).
* Drafts are still reviewed before they are created in TFS, online or offline, as before. When it is created, the
  run report PDF and metrics.json are attached.

## API

* `GET /api/performance/runs/{id}/report?format=pdf|html&download=` and `POST /api/performance/runs/{id}/report`
* `POST /api/performance/compare` {run_ids}
* `POST /api/performance/reports` {kind: comparison|consolidated, run_ids, title}
* `GET /api/performance/runs` adds `build`, `date_from`, `date_to` (local dates, YYYY-MM-DD)
* `POST /api/performance/plans/{id}/run` and `POST /api/performance/scenarios/{id}/run` accept {build}
* Environments have `build`. Runs have `build` (database migration 15).
* Setting `performance.auto_report` (default on) controls the report after each run.

## Tests

`tests/test_perf_reports_23328.py`: planned periods from thread groups; period figures read from a JTL (transactions
left out, rate over the time covered); comparability levels; defect draft content and severity; and real JMeter
5.6.3 runs against the local test site: the report made after the run with every section (read back from the PDF),
history by build and date, a second run compared with the first (baseline and build note), comparison and
consolidated PDFs, a failed run whose defect draft is listed in its report, the report made again on request, and a
stored secret printed in the report stopping the PDF.

PDFs were also rendered page by page and inspected (run reports of a passing and a failing stress run, comparison and
consolidated reports).

Not tested here: Windows (the same code runs in CI on Windows), TFS creation against a live server (unchanged).
