# Patch 23.3.29: Performance schedules and start at Windows sign-in

Cumulative patch for 23.2.2 and 23.3.0 to 23.3.28. This is milestone 8 of 8 of the JMeter performance-testing
module. A saved scenario can now be run on a schedule, with the versions, overlap, missed-run, runtime, report and
notification behaviour stated on the schedule. The application can start itself when you sign in to Windows, so
schedules keep running after a restart, without administrator rights.

## What is new

**Performance Testing > Performance Schedules (new page)**

* Repeat: once at a date and time, every day, chosen weekdays, every N weeks on chosen days, or every N minutes
  (15 minutes to 7 days), with an optional end date. Pause, Resume, Edit, Delete and Run now.
* Each schedule shows the scenario and environment, the next run in its time zone, the version policy, the overlap
  and missed-run policies, the maximum runtime, the report and email options, and its last result. History lists
  every due time: run, skipped (with the reason), missed, or not started (with the reason).
* Time zone: the Windows time zone of this PC by default. Other named zones are offered where the PC has the time
  zone data. There is no time server or GMT service.
* Run now also works from the scenario page (Run now as before; Schedule… opens a new schedule for the scenario).

**Versions (nothing unsaved ever runs)**

* Latest saved: each run takes the latest saved script version, workload and targets; the data set versions are
  resolved when the run is created, and the run is refused if a data set changes before it starts.
* Pinned: the script version, data set versions, workload and targets are taken when the schedule is saved ("Pin
  the current versions again" refreshes them). If a pinned version no longer exists, or a pinned data set changed,
  the run is skipped and the history says why. Drafts in the script editor are never run: only saved versions exist
  on the server, and pinning refuses a script that has never been saved.

**Overlap, duplicates, missed runs and restarts**

* Overlap: Skip (default) or Queue (at most one waiting run of the schedule, never a backlog). Load runs never run at
  the same time: this PC is one load generator. Skip also applies when another run on the same environment is
  active.
* Each due time is a job with a unique identity in the database. Two checks, a restart, or a second copy of the
  application cannot start a second run for the same time.
* Missed times: when the application was closed, or the PC was off or asleep, the times that passed are recorded as
  missed; no run is claimed for them. A time found up to 5 minutes late (a busy or briefly sleeping PC) still runs.
  Policy "Record them, run nothing" (default) or "Record them, run the latest once": only the latest missed time
  runs, once. Up to 20 missed times are listed one by one; more are counted in one entry.
* On start, a scheduled run still queued from before the shutdown follows the same policy (cancelled with the
  reason, or the newest one runs once). A job claimed when the application stopped is marked as not started.

**Maximum runtime**

* Each schedule has a maximum runtime (default: the planned duration with a margin, or 60 minutes when the script
  sets its own duration). It cannot be shorter than the planned workload. A run still going past it is stopped
  cleanly; its results up to the stop are kept and the run says why it stopped.

**Report and notifications**

* "Make the PDF report after each run" (default on): the run report of 23.3.28, made after the run.
* The bell (in-app notifications) shows finished runs as before, and also skipped, missed and not-started times.
* Email through Outlook (Settings > Outlook Email): after every run, or only when it failed or missed targets, with
  the PDF attached when it was made.

**Start at Windows sign-in (no administrator rights)**

* The tick box on the Performance Schedules page puts a small file, `QA Enterprise AI.cmd`, in your Startup folder
  (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). At sign-in it runs `START_QA_PLATFORM.cmd` minimised
  and without opening the browser. Untick it (or delete the file) to stop. Only the file this application created
  is ever removed; an existing file with that name is left as it is.
* `START_QA_PLATFORM.cmd` does not open the browser when `QA_NO_BROWSER=1` is set.

**What closing things does**

* Closing the browser: nothing stops. Runs and schedules continue in the application.
* Closing the application (its window): a running load test is stopped and kept as INTERRUPTED with its results,
  and no schedule starts until the application runs again. The log records how many schedules are affected and the
  next due time. Times that pass meanwhile are recorded as missed.
* Schedules run only while QA Enterprise AI runs on this PC, signed in as the Windows user who started it.

## Windows setup

1. Apply the patch (APPLY_PATCH.ps1). No administrator rights are needed.
2. Start the application as usual (START_QA_PLATFORM.cmd, or the desktop shortcut).
3. Performance Testing > Performance Schedules: tick "Start at Windows sign-in" if the schedules must run after the
   PC restarts. Keep the PC signed in and awake at the scheduled times (Windows power settings: do not sleep while
   plugged in). A locked screen is fine; signing out stops the application.
4. Optional: Settings > Outlook Email to receive the results by email.

## API

* `GET /api/performance/schedules` (with the scheduler host status), `POST /api/performance/schedules`
* `GET/PUT/DELETE /api/performance/schedules/{id}`, `POST .../{id}/pause`, `.../{id}/resume`, `.../{id}/run`
* `GET /api/performance/schedules/jobs`, `GET /api/performance/schedules/host`
* `GET/PUT /api/performance/startup` {enabled}
* Runs have `schedule_id`, and a scheduled run's configuration includes the schedule, the due time and the
  versions used (database migration 16: `perf_schedules`, `perf_schedule_jobs`).

## Tests

`tests/test_perf_schedules_23329.py`: due times for every repeat type, in a named time zone and the system zone;
validation (interval, past time, days, time, time zone, email, no parallel overlap, maximum runtime shorter than the
workload); pause and resume (times passed while paused do not run); one run per due time across two ticks and a
second scheduler on the same database; overlap skip and queue (at most one waiting); missed times recorded with skip
and run-once (the latest only), and a ten-day gap of a 15-minute schedule recorded without a backlog; restart
recovery of queued scheduled runs and of a claimed job; pinned script, workload and targets used after the plan
changed, latest versions after switching, and a pinned data set that changed refused with the reason; the Startup
folder file (written, removed, someone else's file left alone); and real JMeter 5.6.3 runs: a scheduled run that
completes with its PDF and an email with the PDF attached, and a run stopped at its maximum runtime with partial
results kept.

The page was checked in Chromium at desktop and phone width.

Not tested here: a real Windows sign-in (the Startup folder file is checked; Windows runs it at sign-in), and sleep
or power-off of a real PC (simulated by moving the clock).
