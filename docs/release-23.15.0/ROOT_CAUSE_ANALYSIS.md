# Root cause: "Request validation failed" on Create TFS Defect, and an agent that asked the same things twice (23.15.0)

Two reports on 23.14.1, from screenshots of the installed system: **Create TFS Defect** answered "Request validation
failed" on Vulnerability Scanning, Execution History (a failed run and a run stopped by the user) and Performance
Results; and the **AI Agent** (now the Workflow Operator) asked for the first name, last name and ZIP it had already
been taught, then stopped at WAITING after a taught workflow was requested by name.

## 1. "Request validation failed" (every result page)

The page sends what its own fields hold:

```
POST /api/test-actions/exec/RUN-20261125-072622-A7FFEA/defect
{"title": "...", "severity": 3, "priority": 3, "repro_text": "...", "attach": true}
```

The request model declared both fields as text:

```python
class DefectIn(BaseModel):
    severity: str | None = None     # qa_platform/api/test_actions.py, 23.14.x
    priority: str | None = None
```

pydantic v2 in strict-by-default mode does not coerce `3` to `"3"`, so FastAPI rejected the request **before any
defect code ran** and the global error handler answered with its generic text, "Request validation failed". Nothing
reached TFS, nothing was written, and the message named no field, so the page looked broken on every result type at
once. The severity dropdown is numeric on all four screens, which is why all four failed and why typing a label by
hand appeared to "sometimes work".

**Fix.** `severity` and `priority` accept `int | str`, and a validator maps `3`, `"3"`, `"2 - High"` and `"high"` to
1 to 4; anything else raises a message that names the field ("severity - Choose a level from 1 to 4."). The validation
error handler now lists the field and its message instead of only "Request validation failed".

### Why the body was unreadable

`defect_preview` built the reproduction text by dumping the run's facts with `json.dumps` and then HTML-escaping it,
so the work item carried `&quot;steps&quot;: [{&quot;index&quot;: 0, ...`. The facts are now rendered as text: numbered
steps with their status, Expected result, Actual result, the error, the environment and the evidence; the HTML
version is built from that same text.

### Why nothing was attached, and why performance runs could not make a defect at all

* The preview listed file names but the draft was created without them, so publishing uploaded nothing. The draft now
  carries the evidence paths (up to 20) and the publisher uploads them.
* `_facts_exec` only knew functional runs, and refused anything whose status was not FAILED: a run stopped by the user
  ("STOPPED") and a performance run were both "not a defect". There is now a performance fact builder, a stopped run
  is treated as a failed one, and a performance run can become a defect even when it passed (the team wanted a
  defect for an SLA that passed only just).

## 2. The agent asked twice and then stopped at WAITING

Three separate causes, all visible in the user's saucedemo recording:

1. **Nothing was remembered between a lesson and a request.** The values typed while teaching (first name, last name,
   ZIP) were stored as part of the recording but never read back when preparing a run, so every required field with
   no value in the request became a question. Fixed by filling unsaid required fields from the person's own last
   values first and the taught values second, each named in the preview, and by remembering the values of every run
   (`remembered_values`) and the requests themselves (`remembered_prompts`).
2. **A confirmation needed a form, not a word.** "yes" was treated as a new request, which produced a new preview, so
   the run never started. A typed or spoken confirmation (English or Arabic) now confirms the latest preview of that
   conversation by its hash.
3. **The chat's browser window was held by the waiting run.** The hosted task that produced the preview kept the one
   chat window while waiting for the confirmation, so the next step could not use it and the conversation sat at
   WAITING. The waiting task now steps aside (releases the window) and the confirmed run picks it up again.

## 3. Arabic that was heard correctly but mapped to nothing

"إفتاء الإشارات" is what the recognizer writes for "افتح الإشعارات" (open notifications): two words, each one or two
letters away from the word in the list. The word list matched neither, the local model translated it as "open the
signals", and no page is called signals, so the answer was "this operation is not supported". Fixes: a fuzzy pass over
the word list (edit distance 1, or 2 for a long word, and only when a single word wins), a fuzzy page match for
English, a retry through the word list when the model's English leads nowhere, and a second Arabic transcription pass
when the recognizer's own language confidence is below 0.75 or it reports Arabic for text with no Arabic letter.
