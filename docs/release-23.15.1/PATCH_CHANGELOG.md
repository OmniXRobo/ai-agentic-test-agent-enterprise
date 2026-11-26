# Changelog: SkyGuard QA 23.15.1

Cumulative on 23.15.0 (commit `474d0b1`, PR #66). One upgrade for every release from 23.3.35 to 23.15.0. Everything
works offline: nothing is downloaded during the install or at run time. No database change (main database still
**23 migrations**, knowledge database still **schema 3**), no new dependency, browser extension still **1.5.0**.
Earlier changes: `previous-23.15.0/`, `previous-23.14.1/` ...

## "Generate test cases with AI" works on the Test Cases page

Reported on 23.15.0: on **Test Cases**, *Generate test cases with AI* (plan #1538, Sprint 1, Functional Testing,
US1554, 2 cases per criterion, Create in TFS) stopped with **"No test cases could be generated for US1554: AC-01: no
test cases were written (The On-Premise AI did not return valid structured output)"**, while **AI Test Design** wrote
the test cases for the same story with the same local model. Cause: `ROOT_CAUSE_ANALYSIS.md`.

* **The answer format is enforced like on AI Test Design.** The Test Cases page now sends its JSON schema to the AI
  server (llama-server turns it into a grammar), so a small local model can only write JSON of the right shape.
* **An almost-right answer is repaired instead of refused.** Trailing commas, `//` comments, `True`/`None`, typographic
  quotes, prose or a ```json fence around the answer, steps written as text ("1. Click Add to cart => The cart badge
  shows 1"), step keys such as `step` / `expected_result`, criterion ids given as text, test data given as an object,
  preconditions given as a list: all are read correctly.
* **Each test case is checked on its own.** A case the model got wrong is left out (and logged); the other cases of
  the answer are kept. An answer cut off at the answer limit keeps its complete cases.
* **One more call from the start** when nothing usable came back, with what was wrong; only then the criterion is
  reported as not generated.
* The JSON repair is shared: every other AI step that reads structured output (AI Test Design, test data, reports)
  also accepts trailing commas, comments and Python literals now.

## Faster, with the same quality

On a PC without a GPU, each AI answer costs minutes, and a second (review) call for the same criteria cost as much as
the first. 23.15.1 writes fewer and shorter answers while keeping every quality rule:

* **The rules the model used to break are enforced by the grammar**: each case links at least one criterion of this
  call (only ids that exist), condition ids are only the conditions of these criteria, and a case has at least two
  steps (one for Performance Testing). These were the rule errors that most often made the review call necessary.
* **Short texts**: a title, objective, rationale or step can no longer run on (limits leave room for concrete actions
  on named pages, fields and buttons, with the exact values, and an observable expected result).
* **Only the flagged cases are reviewed**: when the rules find a vague expected result or a summary step, only those
  cases go to the reviewer (plus room for cases that cover a missing condition); the cases that passed every rule are
  kept exactly as written, in their place. "Thorough review (slower)" still reviews every case with a warning.
* No more failing first call followed by a repair call and a second failure for each criterion (the old path made two
  slow calls and then gave up).

## Companion (from the 23.15.0 CI)

The browser companion reads and discards the body of a request it refuses (for example a pairing attempt over the
rate limit) before it answers, so the next request on the same connection is not broken and Windows no longer resets
the connection (WinError 10053).
