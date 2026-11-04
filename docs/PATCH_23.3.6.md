# Patch 23.3.6: voice permissions, privacy, audit log and interruptions (milestone 9)

One patch for a PC on 23.2.2 or 23.3.0 to 23.3.5. It includes everything from the earlier 23.3 patches
(milestones 1 to 8). No Python packages, no database change, no Internet.

## Voice permissions (Voice Settings, "Voice permissions")

The application has no user accounts (one person, on this PC), so the voice permissions are a profile for what the
voice agent may do. It is checked before every command runs, and checked again when you say "confirm", so changing
it while a question waits takes effect at once. It does not replace Windows sign-in or TFS permissions: TFS still
decides what your account may change.

* **Viewer**: look things up and navigate. Creating, changing, deleting, running tests, the guided browser and
  changing settings are refused ("Your voice permissions are set to Viewer, which doesn't allow ...").
* **Tester**: also create and generate, save, run tests, drive the guided browser and change the run settings.
  Deleting is refused.
* **Full** (the default, the behaviour before this version): everything. Deletions, renames, saving to TFS,
  production runs, form submissions, payments and orders still ask "Please confirm" first, as before.

## Privacy

* Spoken passwords, PINs, one-time codes, security codes, tokens, API keys and card numbers are masked as
  `[hidden]` before the transcript is stored, written to the log or shown in the event feed
  ("my password is [hidden]", "enter [hidden] in the card number").
* A password or code said aloud is never typed into an application. The agent says "I don't type passwords or codes
  said aloud" and uses the password saved in Application Credentials when you say "enter the password".
* "Keep what I said" (on by default): when off, the transcript is not stored ("(transcript not kept)"); the command,
  its result and the reply are still kept in the audit log.
* "Keep voice history for N days" (0 = keep): older voice commands are removed automatically.
* "Delete voice history" removes the stored voice commands (asks first). Commands still running are kept.

## Command audit log

"Download command audit log" (Voice Settings) gives a CSV with the time, what was said (masked), the command, its
parameters (masked), the status, the reply and the final result of every voice command. Also
`GET /api/voice/audit` (JSON) and `GET /api/voice/audit?format=csv`.

## Interruptions and follow-ups

* "Stop", "Wait", "Hold on", "Pause": drops the question the agent was waiting on, stops a running test or
  performance test, and stops a script generation after its current test case. The guided browser stays open
  (say "stop recording" to end it). The reply says what was stopped and that anything already saved stays saved:
  nothing that was already written to TFS or the database is undone.
* "Repeat that", "Say that again": repeats the last reply.
* "Continue", "Go on": if a question is waiting, says so ("I'm waiting for your confirmation. Say confirm to
  continue, or cancel."); otherwise says nothing is paused.
* "What are you doing?": lists what is still running and when it started, or the last thing it did.

## Reliability

* Each conversation (each browser tab) keeps its own context: one tab's test plan or question is never used in
  another.
* Waiting for a script generation job stops after 45 minutes with "I stopped waiting" instead of waiting forever;
  the job itself keeps its results.

## Not changed

TFS connection, On-Premise AI settings, the offline recognizer, the wake name, the speech settings and the existing
pages work as before. The Windows system clock is still used for all times.

## Files (in addition to 23.3.5)

qa_platform/voice: `privacy.py` and `commands_safety.py` (new), `service.py`, `intents.py`, `registry.py`,
`commands_guided.py`; qa_platform: `config.py`, `api/voice.py`, `__init__.py`; web/js/pages/voice.js.
