# Patch 23.3.23: Performance script editor

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.22. This is milestone 2 of 8 of the JMeter performance-testing
module. A performance plan can now be a script: a JMX that you import or open from a generated plan, then edit as a
tree or as raw XML, with a version kept on every save.

## What is new

**Import a JMX file** (Performance Test Plans, and "Import a JMX for it" on a scenario)

* A script saved by JMeter 5.x is imported as it is. Every element and property is kept, including the ones the
  editor has no form for (JSR223 scripts, controllers, config elements, comments in the file). Exporting gives the
  same script back.
* The plan name comes from the Test Plan element. The plan gets its own scenario, or joins the scenario it was
  imported for. Importing never starts a test.
* Refused: files that are not XML, files that are not a JMeter test plan, files with DOCTYPE or ENTITY declarations
  (JMeter never writes them), and files over 20 MB.

**Open in script editor** (on a generated plan)

* The plan's generated JMX becomes its script (version 1). From then on, requests, users and duration are edited in
  the script; the old form only changes the name, the test type and the targets.

**Tree editor**

* The script is shown as JMeter shows it: test plan, thread groups, controllers, requests and what belongs to them.
  Disabled elements are shown struck through, and everything under them is left out, as JMeter does.
* Select an element to edit it: name, comments, every simple property (with readable labels for the common ones),
  and forms for request URL and method, request body or parameters, headers, variables and assertion patterns.
  Settings without a form are shown read only and can be changed in the Raw JMX view.
* Add, under the selected element: HTTP request, transaction, thread group, regular expression extractor, JSON
  extractor, boundary extractor, response assertion, duration assertion, JSON assertion, constant timer, uniform
  random timer, header manager, HTTP request defaults, cookie manager, user defined variables. Only built-in JMeter
  elements, and only where JMeter accepts them (a thread group only under the test plan, a request only in a thread
  group or controller).
* Duplicate, move up or down, enable or disable, and delete.
* Edits change a working copy. "Unsaved changes" is shown until you press Save.

**Raw JMX**

* The whole JMX as text. "Validate and use" checks it and updates the tree. Problems are listed before anything is
  used: not XML, no thread group or request, JMeter plugins (this application installs none), a thread group that
  loops forever without a duration, a user count that is not a number, Open Model thread groups (they arrive with the
  workload milestone).

**Versions**

* Each Save keeps a version with an optional note (saving the same text again does not). Compare any two versions line
  by line, download any version, or restore one (it is saved as a new version, so nothing is lost).
* If the script was saved elsewhere since you opened it, Save is refused, so no change is overwritten.

**What the script does** (shown in the editor, and used by the pre-run checks)

* The HTTP requests with their full URLs, resolved from the Test Plan's variables, User Defined Variables elements,
  `${__P(name,default)}` defaults and HTTP Request Defaults, the transaction and thread group each request is in.
* Peak concurrent users and duration from the thread groups (setUp and tearDown groups do not overlap the main
  groups). When a thread group runs a number of loops instead of a duration, the duration is "not known before the
  run".

**Pre-run checks on scripts** (added to those of 23.3.22)

* The script's problems (above) block the run.
* Hosts are checked against the environment's allowed hosts after variables are resolved. A host only known at run
  time (a variable or property without a default) blocks the run when the environment lists allowed hosts, and is a
  warning otherwise.
* Users are counted from the thread groups, including property defaults, against the environment's limit.
* A loop-based duration blocks the run when the environment has a duration limit; otherwise the run is ended after
  2 hours.
* A CSV data file that is not found blocks the run (relative paths are read from the run folder; test data
  management arrives in 23.3.25).
* Listeners (View Results Tree and similar) are switched off in the copy JMeter runs; the run records its own
  results. The saved script is unchanged and is also kept in the run folder as `script.jmx`.
* Users and duration overrides at run time do not apply to scripts; they are set on the thread groups.

## Run snapshot

`config.json` adds `script` (version, SHA-256 and how many listeners were switched off). The run folder keeps
`plan.jmx` (exactly what JMeter ran) and `script.jmx` (the script as saved).

## Database

Migration 13 adds `perf_plan_versions` (plan, version, script, note, SHA-256, time). Plans gain `script` and
`script_version` in their stored JSON; generated plans are unchanged.

## API

* `POST /api/performance/plans/import` `{jmx, filename, name, kind}` (`?scenario_id=` to add it to a scenario)
* `POST /api/performance/plans/{id}/script/convert`
* `GET/PUT /api/performance/plans/{id}/script` (PUT: `{script, base_version, note, name, kind, sla}`; 409 when the
  script changed since `base_version`)
* `POST /api/performance/script/analyze` `{script}`, `POST /api/performance/script/apply` `{script, ops}`,
  `POST /api/performance/script/node` `{script, node}`: validate, edit and inspect a working copy, nothing is saved
* `GET /api/performance/plans/{id}/versions`, `GET .../versions/{n}` (the JMX), `GET .../diff?a=&b=`,
  `POST .../versions/{n}/restore`
* `GET /api/performance/plans/{id}/jmx` returns the script for script plans.

## Dependencies

None new. JMeter 5.6.3, Temurin JRE 17 and psutil 7.2.2 (from 23.3.22, installed again by this patch) are unchanged.

## Verification

* `tests/test_perf_script_23323.py` (10 tests): import keeps every element (canonical XML comparison, comment and
  JSR223 script included) and export returns it; invalid, DOCTYPE and non-JMeter files are refused; plugins and
  endless loops are problems that block the run; tree edits (transaction, assertions, extractor, timer, rename,
  disable, move, request with body, placement rules), save, conflict on a stale version, versions, diff and restore;
  raw JMX edits, header and variable forms; a generated plan opened in the editor; script checks for hosts from
  variables, unknown hosts, users from property defaults, loop-based duration and a missing CSV file; disabled
  elements and listeners.
* A real JMeter 5.6.3 run of an imported script against the bundled local test site: a JSON extractor takes an id
  from one response, the next request uses it, and the added assertion passes on every sample; the transaction is
  reported as its own label; the listener is switched off in the run copy only.
* The 23.3.22 and earlier performance tests pass unchanged.

## Not in this patch (next milestones)

Network recording and correlation (23.3.24), test data and binding (23.3.25), workload templates and the one-user
validation run (23.3.26), the live dashboard (23.3.27), the performance PDF, comparisons and defects (23.3.28), and
performance schedules with start at Windows sign-in (23.3.29).
