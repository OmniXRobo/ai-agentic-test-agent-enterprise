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

1. Choose the **test plan** and **suite**. The suite's testing type decides what gets generated.
2. Choose the **sprint**. Its user stories are listed with check boxes.
3. **Generate**: for each story the Test Case Design Agent does the following.
   * Reads the title, description and acceptance criteria. Existing criterion ids (`AC1`, `AC-02`, `Criterion 3`)
     are kept. Other criteria get `AC-01`, `AC-02` and so on, and those ids stay stable across generations: unchanged
     text keeps its id, edited text keeps the id of the criterion it replaced, and a new criterion gets the next free number.
     A Given/When/Then scenario counts as one criterion.
   * Breaks each criterion into conditions (`AC-01.1`, `AC-01.2`) with the AI. If the AI can't, each criterion
     becomes a single condition.
   * Writes the cases for that testing type only, in the Title, User Story, Precondition and Step / Action /
     Expected Result format.
   * **Validates** them with explicit rules (see below), sends the issues back to the AI once to refine the cases,
     and validates again. A confidence score is never used as proof.
   * Builds the **coverage matrix** per criterion, judged per condition. The statuses are Covered, Partially Covered,
     Not Covered, Not Applicable and Needs Clarification. This is requirement coverage and is never an execution result.
4. Review the cases, edit any of them (an edit is re-validated), and choose **Reuse existing #id** for duplicate
   candidates. Then **Create selected test cases in this suite**.

Validation rules: `SOURCE_LINK` (every case links to a criterion of this story; unknown ids are dropped),
`STEP_ACTION` / `EXPECTED_RESULT` (no empty or vague results such as "works correctly"), `CLEANUP` (cases that
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

## Test Data (`#/test-data`)

You filter by **sprint → user story → test case**. The list shows the user story, test case, row, scenario, values
(with the dynamic-date preview), expected outcome and criteria.

* **Generate test data** works for the chosen test case, for the selected rows' test cases, or for every test case
  of the chosen story. The Test Data Intelligence Agent reads the steps, the story's criteria and the suite's testing
  type, then generates valid, invalid, boundary, empty/null, duplicate, date/time, role, Unicode and pairwise rows as
  they apply. It never invents business rules or credentials; missing rules are listed as gaps.
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
* **Write to TFS** writes to the test case's test data tab if its type has one. That is a custom "Test Data" field
  or the Parameter values grid. Otherwise it writes to the Summary.
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
