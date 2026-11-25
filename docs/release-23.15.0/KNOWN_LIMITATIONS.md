# Known limitations: 23.15.0

Stated plainly so that nobody has to discover them. The first section is new in 23.15.0; everything in
`previous-23.14.1/KNOWN_LIMITATIONS.md` and the earlier `previous-*` folders still applies.

## New in 23.15.0

### Create TFS Defect

* **Not run against your real TFS here (no access from this environment).** The defect creation, the attachments and
  the duplicate check were run against a faithful Azure DevOps simulator (the protocol is real, the service is not).
  Check one defect on your own TFS after the upgrade.
* The work item is created with the fields SkyGuard knows: title, severity, priority, reproduction steps, area and
  iteration, assignee, and the attachments. A **mandatory custom field** your TFS process requires and SkyGuard does
  not know will make TFS refuse the creation; the draft is kept with the TFS message so it can be added by hand.
* At most **20 evidence files** are attached to one defect, largest-first by relevance (the failed step's screenshot,
  the report, then the rest). A video is attached only when its file is under the TFS attachment limit of your server.
* A performance run creates a defect whether it passed or failed. Nothing stops ten defects for ten passed runs: it is
  a person's decision each time, and a duplicate of an open defect is reported rather than created.

### The Workflow Operator

* The remembered values are **per application, per workflow and per field**, and the last value wins. It is not a
  profile: changing the ZIP you usually use means using it once.
* A batch request produces one record per day from the date in the request ("one per day"). Other patterns ("every
  Monday", "two per day", "between the 5th and the 15th") are not parsed yet and a question is asked instead.
* The number of records in one request is bounded by the per-run record limit in Advanced Administrator Settings
  (default 50). A larger batch is refused with that number, not silently cut.
* A learned authorization code is filled in by the run. If your application rotates that code, remove it ("Forget") or
  the run will pause when the application refuses it.
* **One-time codes are never kept**, by design: a run that reaches an OTP field pauses and asks the person, as before.
* A taught workflow still needs its lesson to be approved (the knowledge review) before it runs, as in 23.14.x.

### Voice

* Fuzzy Arabic matching takes a word one or two letters away from a command word, **and only when a single word
  wins**. A word that is equally close to two command words is left alone and the question comes back, in Arabic.
* Arabic transcription needs the multilingual speech model on the PC (`small` or larger, not `*.en`). With an
  English-only model, "English and Arabic" still cannot produce Arabic text; Setup reports which model is installed.
* The second Arabic transcription pass costs one extra pass over the same audio when the recognizer is unsure; on a
  slow PC that is about half a second more for that sentence.
* **Listen always** needs the microphone permission to be granted to the page (or the extension's microphone page)
  once, in the browser. It is off by default and does not survive a page reload unless turned on again.
* Hands-free listening sends each sentence as one request. It has no "stop talking" word: say "stop listening" is not
  a command, press **Stop listening** (or leave the chat) to end it.
* **Not executed here:** the microphone, real Arabic speech and the spoken replies (no audio device in this
  environment). The text path, the language choice and the wiring are tested; the sound itself must be checked on the
  PC.

### The rename

* The page, the menu entry, the extension and the manual say **Workflow Operator**. The route (`#ai-agent`), the API
  paths (`/api/simple/...`, `/v1/chat`), the database tables and the extension ID are unchanged, so links, scripts and
  an installed extension keep working. Older release documents in `previous-*` still say "AI Agent".
