# Patch 23.3.2: test cases and test data by voice (milestone 5)

One patch for a PC on 23.2.2, 23.3.0 or 23.3.1. It includes everything from 23.3.0 and 23.3.1 (wake-name listening,
the voice assistant at the top right, test plans and test suites by voice), see PATCH_23.3.0.md and PATCH_23.3.1.md.

## What you can say

Test cases (uses the AI configured in AI settings and the existing Test Cases generator)
* "Generate test cases from the acceptance criteria" (or "... for Sprint 25"): for the current test plan, one
  generation per selected suite, from the acceptance criteria of the sprint's user stories. Test Cases opens and
  shows the progress. When it is finished you hear the real counts, possible duplicates and cases with errors.
  Nothing is written to TFS yet.
* "Save the test cases": asks "Please confirm", then creates the reviewed cases in their suites. Cases with errors
  are held back; existing duplicates are linked instead of created again. The reply gives the counts TFS returned.
* "Explain test case 106", "Explain this test case": title, number of steps, first step and expected result.
* On a user story page, "Generate test cases" keeps doing what it did before (cases for that story).
* If there is no plan, no suite or no AI, or the AI is busy, the agent says so and asks instead of guessing.

Test data (the existing Test Data generator)
* "Generate test data for test cases 105, 106 and 107", "... for these test cases" (the ones just saved),
  "Generate five more combinations" (the current test case). Each test case is checked in TFS first. Test Data opens
  filtered to those cases, and the reply gives the rows made and the gaps noted.
* Up to 60 rows per case. "Only negative" data cannot be requested from the generator yet, and the agent says so.

## Files (in addition to 23.3.1)

qa_platform/voice: `commands_design.py` (new), `intents_plans.py`, `registry.py`, `service.py`, `model.py`.
web/js/pages: `test-data.js` (opens filtered by `?tc=`).
