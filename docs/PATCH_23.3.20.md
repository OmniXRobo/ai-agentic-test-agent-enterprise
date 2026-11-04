# Patch 23.3.20: Record / Pause when recording more steps

Before 23.3.20, **Record more steps** (Hybrid Test Automation) started recording the moment the browser opened, so
every click needed to log in and reach the right screen was recorded too and had to be deleted afterwards.

## Record more steps

1. Open a script and choose **Record more steps** (or pick the script on Hybrid Test Automation).
2. Choose **Insert at step N**. *Recorded steps* = "are inserted before this step", or "replace this step (healing)"
   with **Replace up to step M** to re-record broken steps N to M.
3. Optional: **Play the script's steps before this position first**. The browser plays steps 1 to N-1 (saved
   credentials and data row 1 are used), so it is already on the right screen. If a step cannot be played, the
   note says which; browse there yourself.
4. **Open browser**. The browser opens and records **nothing** (state: NOT RECORDING).
5. **Record** (on the page, or on the Record / Pause bar at the bottom right of the browser window, which can be
   dragged). Only what you do now is recorded.
6. **Pause**: browse freely, nothing is recorded. **Resume** carries on.
7. While paused you can change **Where the next recorded steps go** and press **Set position**. The next Record
   starts a new part at that place. One session can add steps at several places and replace several steps.
   Step numbers always mean the script's steps as they were when the browser opened.
8. **Stop and save**. The message says what was done, for example "inserted 2 steps at step 8; replaced step 6
   with 1 recorded step". If nothing was recorded the script is unchanged. Every save is kept as a version.

Add a check works after Record has been pressed once (the check goes into the current part).
Spoken browser commands while paused are carried out but not recorded.

## Guided Browser Automation (new script)

Still records at once from the Start URL, and now has **Pause** / **Resume** as well.

## API

* `POST /api/automation/recordings` takes `start_paused` (default: true for Record more steps, false for a new
  script), `replace` (number of steps from `insert_at` to replace) and `replay` (play the steps before `insert_at`).
* `POST /api/automation/recordings/{run_id}/control` with `{"action": "record" | "pause" | "position",
  "insert_at": 0-based, "replace": n}`.
* `GET /api/automation/recordings/{run_id}` also returns `state` (ready, recording, paused, replaying), `where`,
  `count` and `segments` (each with `at`, `replace`, `steps`). Stop returns `summary`.

Files: qa_platform/automation/recorder.py, qa_platform/automation/service.py, qa_platform/api/automation.py,
qa_platform/automation/js/recbar.js (new), qa_platform/automation/js/recorder.js, qa_platform/automation/js/fingerprint.js,
web/js/pages/automation.js, qa_platform/__init__.py (version). Tests: tests/test_record_controls_23320.py.
