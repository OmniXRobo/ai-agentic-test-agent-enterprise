# Test Plans, Test Suites, Test Cases and Test Data

This document covers the four Test Design pages and the agents behind them. It builds on the existing 5-agent
design pipeline (`qa_platform/design/service.py`), which stays as it was. AI Test Design and its publish flow,
voice commands and the Script Studio test data binding all keep working unchanged.

## Test Plans (`#/test-plans`)

| Action | TFS call (Azure DevOps Online and Server 2020+) | Older servers (legacy Test API 5.0) |
|---|---|---|
| Create, with or without a sprint | `POST _apis/testplan/plans` | `POST _apis/test/plans` |
| Update the name, sprint (or no sprint), state or description | `PATCH _apis/testplan/plans/{id}` | `PATCH _apis/test/plans/{id}` |
| Delete | `DELETE _apis/testplan/plans/{id}` | `DELETE _apis/test/plans/{id}` |

"No sprint" means the plan sits on the project's root iteration. Deleting a plan removes its suites in TFS, but
the test case work items stay. The page asks for confirmation and names the project first.

## Test Suites (`#/test-suites`)

There are 11 testing types: Functional, Smoke, Sanity, Regression, Integration, End-to-End, API, Database,
UI and Usability, Performance and Security. The first six are checked by default. Each checked type becomes a
static suite (reused if one with the same name exists). The suite's testing type is stored by the application
(table `suite_types`), so it survives a rename. Suites can be renamed, given a type, or deleted
(`PATCH/DELETE _apis/testplan/Plans/{plan}/suites/{suite}`). The root suite can't be deleted on its own.

## Test Cases (`#/test-cases`)

1. Choose the **test plan** and check one or more **suites**. Each checked suite gets its own run with test cases of
   its testing type; the runs are generated one after the other because a single local model serves them.
2. Choose the **sprint**. Its user stories are listed with check boxes.
3. **Generate**: for each story the Test Case Design Agent does the following.
   * Reads the title, description and acceptance criteria. Existing criterion ids (`AC1`, `AC-02`, `Criterion 3`)
     are kept. Other criteria get `AC-01`, `AC-02` and so on, and those ids stay stable across generations: unchanged
     text keeps its id, edited text keeps the id of the criterion it replaced, and a new criterion gets the next free number.
     A Given/When/Then scenario counts as one criterion.
   * Breaks each criterion into conditions (`AC-01.1`, `AC-01.2`) by its clauses ("A and B", "A; B"), without an AI
     call. Unclear words such as "appropriate" or "quickly" become a clarification question.
   * Writes the cases for that testing type only, **two criteria per AI call** (`design.criteria_per_call`), up to
     *Cases per criterion* each, in the Title, User Story, Precondition and Step / Action / Expected Result format.
     Small calls keep a local model on a CPU within its time: each call may take up to the **generation timeout**
     (AI settings, default 600 s). When a call times out, its criteria are retried one by one; a criterion that still
     fails is reported ("Generate again") and the other criteria keep their test cases. A story with 11 criteria is
     6 small calls instead of one large one.
   * Steps must be concrete: one action per step naming the page, field or button and the value used, and an
     observable expected result. The prompt carries an example of the format.
   * **Validates** them with explicit rules (see below). Errors, uncovered criteria, summary steps and vague expected
     results are sent back to the AI once per call to refine the cases, and validated again. Other warnings (a single
     step, a missing cleanup step, a duplicate) only trigger that second pass with **Thorough review (slower)**, since
     each pass takes minutes on a local model. A confidence score is never used as proof.
   * Builds the **coverage matrix** per criterion, judged per condition. The statuses are Covered, Partially Covered,
     Not Covered, Not Applicable and Needs Clarification. This is requirement coverage and is never an execution result.
4. With **Create in TFS when done** (checked by default) every case without an error is created in the suite as soon
   as generation ends, tagged and linked to its user story; a duplicate of an existing linked test case reuses that
   test case. Cases with errors wait: edit them (an edit is re-validated) and click **Create selected test cases in
   this suite**. With the option unchecked, review first, choose **Reuse existing #id** or a new case for duplicate
   candidates, and create the selected cases yourself.

Validation rules: `SOURCE_LINK` (every case links to a criterion of this story; unknown ids are dropped),
`STEP_ACTION` (no empty actions, no single summary step such as "Complete the purchase workflow"), `STEP_COUNT` (more than one step),
`EXPECTED_RESULT` (no empty or vague results such as "works correctly"), `CLEANUP` (cases that
change data need a restore step), `THRESHOLD` (performance and security cases without a stated target or policy are
flagged as not decidable yet), `DUPLICATE` (generated cases that overlap each other or existing linked cases).

Traceability is User Story → Acceptance Criterion → Test Suite → Test Case. TFS gets the native *Tested By* link
to the story and the suite membership. TFS has no link to an individual acceptance criterion, so criterion links are
kept by the application (table `ac_trace`) and written as text into the test case Summary. They are never shown as
native TFS links. When a criterion changes or is removed, its linked cases are marked **review** (open
**Traceability**). The application never overwrites existing TFS test cases.

Each generation records the agent, `agent_version`, `prompt_version` (a hash of the prompts), the model
configuration (mode, model, temperature) and the requirement revision (story `rev` and a hash of the criteria).

### Example coverage matrix (Functional Testing, US204)

| AC | Criterion | Conditions | Test cases | Coverage | Missing / reason |
|---|---|---|---|---|---|
| AC1 | Admin can approve an order and the customer is notified | AC1.1 ✓ approve, AC1.2 ✓ notified | TC1 | Covered | — |
| AC2 | Approval happens within an appropriate time | AC2.1 ✓ | TC2 | Needs Clarification | What does "appropriate" mean? |

With a **Performance Testing** suite, the same story gives one workload case marked "no performance target is
stated", and the other criteria are marked *Not Applicable* with a reason. A **Smoke Testing** suite gives one
critical-path check, plus the limitation that business criticality wasn't supplied.

Created test cases are tagged `AI Generated; <testing type>; US<story>; <criterion ids>` (for example
`Functional Testing; US118`), the same tags the project's existing test cases use.

Test cases created elsewhere (by hand or by an earlier version) have no stored criterion link. Coverage and
Traceability then read criterion ids from the case's tags, title or Summary, or match the case to the criterion
whose wording its title and steps share, marked *inferred*. Cases that match nothing are listed as unmapped.

The **test case list** has check boxes, *Select all*, *Delete selected*, and Edit / Delete on every row. Edit changes
the title, state, sprint, tags, priority, Summary and the steps (one line per step: `Action || Expected Result`).
Deleting a test case uses the Test Plans API (`DELETE _apis/testplan/testcases/{id}`, legacy
`_apis/test/testcases/{id}`): TFS deletes test cases permanently, so the page asks for confirmation and lists them.
Other work item types go to the Recycle Bin.

## Test Data (`#/test-data`)

You filter by **sprint → user story → test case**. The list shows the user story, test case, row, scenario, values
(with the dynamic-date preview), expected outcome and criteria.

* Choosing a user story or a test case **loads the test data already in TFS**: the test case's Test Data tab
  (a custom field named "Test Data", in the *Test Data Matrix* layout of `Combination N: name`, `Field: value` and
  `Expected Result:` lines, or a table), its Parameter values, or a Test Data section of its Summary. Passwords in
  valid rows are stored here as `{{SECRET:password}}`; keep the real value in Credentials. **Load from TFS** reads
  TFS again. Edited rows are kept; data this application wrote itself is recognised and not loaded twice.
* **Generate test data** opens a choice: the checked test cases of the chosen story, or every test case of the
  checked user stories of the sprint. The Test Data Intelligence Agent reads the steps, the story's criteria and the suite's testing
  type, then generates valid, invalid, boundary, empty/null, duplicate, date/time, role, Unicode and pairwise rows as
  they apply. It never invents business rules or credentials; missing rules are listed as gaps. The rows are asked
  for four at a time (setting `design.data_rows_per_call`), each call with the **generation timeout**, and every
  later call lists the rows already written so the model adds new scenarios. If a later call times out, the rows
  already written are kept and a gap says so; if even the first call times out it is retried for a single row.
* The generated rows go through explicit rules: plain-text secrets in valid rows become `{{SECRET:name}}`, real
  e-mail domains become `example.com`, identical rows are dropped, invalid rows without a violated rule are flagged,
  and unknown criterion ids are removed.
* Dynamic values are stored as rules (`{{TODAY}}`, `{{TODAY+3d:%d/%m/%Y}}`, `{{TODAY-2d}}`, `{{NOW+2h+30min:%H:%M}}`)
  and resolved from the Windows system clock when the test runs. The page shows a preview that is resolved now.
* Every row has a stable key (`DS-<test case>-<type>-R01`), the dataset version, the seed, and the agent, prompt,
  model and requirement revision.
* **Edit** marks a row as user-edited, raises its version and keeps the earlier values in its history. Regenerating
  archives unedited rows and keeps edited ones, flagged for review. **Delete selected** removes the checked rows,
  and they stay in the history.
* **Write to TFS** writes a *Test Data Matrix* (Combination 1, 2, … with each field and the Expected Result) into the
  test case's Test Data tab, and the dataset key into its "Test Data ID" field when there is one. Without a Test Data
  field it writes the Parameter values grid, otherwise the matrix goes into the Summary.
* **Export / Import** support CSV and JSON. CSV keeps leading zeros, writes null as `<null>` and neutralises
  formula injection with a leading `'`.
* **Coverage matrix** shows Criterion → Test Case → Rows, and reports missing data separately from missing test
  cases. **Check parameter binding** lists step parameters (`@name`, `{{DATA:x}}`) that have no value in a row.

Generating data never provisions it: nothing is written to a database or an API.

## Offline

Everything runs against Local TFS / Azure DevOps Server with the configured local model (llama-server or Ollama).
The rules, ids, coverage, traceability, versioning and import/export are deterministic and don't need the AI. The
quality of the condition breakdown and the refinement depends on the size of the local model. If the AI is
unreachable, generation fails with the reason and never switches to an Internet service.

## Known limitations

* Excel (.xlsx) import and export are not included. Use CSV, which opens in Excel.
* Provisioning datasets into a database or API, and cleaning up the records they create, are not implemented.
  Rows are only generated, stored and written to TFS.
* Row-level execution results come from the existing automation runner, which already binds test data rows to
  steps. This change adds no new runner.
* Duplicate detection compares titles and steps. It doesn't compare meaning.
