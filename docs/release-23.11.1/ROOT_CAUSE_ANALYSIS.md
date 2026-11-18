# Root-cause analysis: the AI Agent issues reported on 23.10.0

The issues were reproduced on the 23.10.0 code with a stand-in online store (`tests/shop_site.py`). The store
behaves like the one in the report:

* its page titles end with "Test Labs";
* the checkout page asks for First Name, Last Name and Zip/Postal Code;
* it has a **Cancel** button and a **Continue** button;
* an overview page has **Finish**.

The person signed in, added a product, opened checkout, typed the values, pressed **Cancel**, came back, pressed
**Continue** with the names empty, completed the form and finished the order. Each cause below was confirmed in that
reproduction, and each fix is covered by a test listed in TEST_REPORT.md.

## Bug 1: "navigate to online store" became a "Cancel lab" preview

| # | Cause (23.10.0) | Where | Fix (23.11.1) |
|---|---|---|---|
| 1 | No intent classification. Every command went to task matching, so a navigation request was matched against the learned tasks. | `autopilot.command`, `agent._operation` | New `skyguard_agent/intent.py` sorts each command into one of these classes: navigation, read/search, data entry, create, edit, approve, authorize, authenticate, record, cancel/delete, explanation, unknown. A navigation request is answered with the page; it never reaches the task router. |
| 2 | Bag-of-words task matching. "online **store**" shared words with the learned task, and when the only learned task was "Cancel lab" it won. | `planner.match_workflow` | A task is only considered when its operation is compatible with the command's intent (`intent.compatible`). Approve and reject are never mixed. Relevance needs the task's entity, and is 0 when the command names a different entity. |
| 3 | The model prompt said "choose one of the given workflow ids". When the deterministic match found nothing, the model's choice was accepted even for an unrelated task. | `planner.parse_with_llm`, `agent._operation` | The model may answer "no task". It is offered only the tasks compatible with the intent, and an incompatible choice is rejected. |
| 4 | A pending question was glued onto every later message with no expiry (`combined = pending + new text`). The old "Cancel lab" question absorbed "navigate to online store". | `autopilot.command` | A pending question is kept only for the same application, at most 30 minutes, and only when the new message is data entry or an answer. A new command drops it. |

## Bug 2: the wrong or a stale workflow was chosen (the "Cancel lab" task itself)

Learning made a task out of a dismiss button. On the checkout page the person pressed **Cancel**. In 23.10.0 that went
wrong as follows:

* "cancel" was in the list of commit words, so the **Cancel** click was taken as a submission.
* The page heading or title named the entity ("Test **Labs**"), so the task was called "Cancel lab".
* The task was trusted automatically after a single sighting.
* The validation messages from the failed **Continue** ("First Name is required") were attached to it, which explains
  the reported preview: UserName, zip 123, first_name / last_name required, WILL NOT RUN.

At the same time the real task was lost. Two things caused this:

* The **Continue** step and the **Finish** step were never put together, because the Finish page has no fields.
* The browser's *submit* event that follows a Continue click was labelled with the first commit word on the form, which
  was again "Cancel".

Fixes in `skyguard_agent/understanding.py`:

* A bare dismiss button (Cancel, Close, Back, Reset) outside a table row is not a commit.
* Continue, Next and Proceed are steps of a multi-step form. Fields are collected across the steps up to the final
  commit (Finish). The task is named after its first page and the record that the success message reports, giving
  "Checkout order". A step that the application refused is attached to that task, not to another task.
* A *submit* event right after a step click is ignored. The fallback submit label never uses a dismiss word.
* Actions on a record's own page are learned with their key, required status and resulting status: Approve, Authorize,
  the driver's Authenticate (whose code field becomes a step the person types) and Record Trip.
* A rule seen once is a candidate. It is shown, but it is not enforced until it is seen again or an administrator
  confirms it.

Plans are now linked to their request. A run stores the command, intent, application and task. Confirmation is
refused in each of these cases:

* `PLAN_OTHER_APPLICATION`: another application is selected;
* `PLAN_UNRELATED`: the plan does not belong to the request it was made for, for example a stale plan for a navigation.

## Bug 3: "Tick the confirmation box first" and "Every record is blocked"

| # | Cause | Fix |
|---|---|---|
| 1 | The run card was redrawn every 2 seconds (`innerHTML`), which cleared the tick. Pressing Confirm then failed with "Tick the confirmation box first". | The card is redrawn only when something the person sees changes (status, preview hash, record statuses, pause). The tick is kept per run and preview hash. Confirm is disabled after one press. |
| 2 | A preview with missing required values was still created, and every record was blocked. Confirm was shown anyway, and the server answered "Every record is blocked by a business rule". | Missing values are asked for before any preview is made ("Missing information: Vehicle"). A preview in which nothing can run shows **Provide missing data / Edit request / Cancel** and no Confirm. The server refuses `NOTHING_TO_RUN` with the real reasons. |
| 3 | For an action on an existing record, the record's status was checked only after Confirm. | The read-only inspection reads each record's current status. The preview blocks the record when it is missing, already done or in the wrong status, and states the reason (for example: "TRP-0005 has the status Recorded; Approve trip needs Pending Approval"). |

The checks run on the server: `Autopilot.confirm` checks the application, the link to the request, that at least one
record can run, and the acknowledgement. `Engine.confirm` checks the preview hash. SkyGuard and the extension's
companion both use `Autopilot.confirm`.

## Bug 4: saved learning was not used

Each stage of the pipeline was traced (observation, capture, normalization, persistence, index, retrieval, plan,
execution, verification). Where it broke:

* **Capture.** The label of a list read "From -- select --DubaiAbu Dhabi…", because the options were part of the
  `<label>` text. As a result, commands never matched the field names. Fixed in `agent_dom.js` (SkyGuard and the
  extension).
* **Normalization.** Three problems: the Cancel task described above; multi-step tasks were lost; and record-page actions
  were named "Create trip (Trip {id})".
* **Retrieval.** There was no check that a saved task could be found again by a command. `Autopilot.verify_retrieval`
  now runs every learned task and page through the same router a command uses. Stop & save reports the result, and
  learning is reported as saved only with that proof.
* **Planning.** Values given in plain words ("odometer end 500") were not read for fields with a unit in their label
  ("Odometer end (km)"). Fixed in `planner._param_names` and `Autopilot._prepare`.
* **Execution and verification.** The application's generated number and status were not captured. The executor now
  reports them (`record_key` and `record_status`) from what the page shows, and the reply quotes them. Nothing is
  invented when the page does not show them.
