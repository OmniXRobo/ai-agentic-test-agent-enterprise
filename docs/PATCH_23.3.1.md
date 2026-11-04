# Patch 23.3.1: test plans and test suites by voice (milestones 3 and 4)

One patch for a PC on 23.2.2 or 23.3.0. It includes everything from 23.3.0 (global wake-name listening, the voice
assistant at the top right, conversational replies), see PATCH_23.3.0.md.

## What you can say

Test plans
* "Find the test plan for Sprint 24", "Open test plan 512", "Search test plans for checkout", "Show inactive test plans",
  "Open the last test plan". One match opens; several matches are read out and you choose ("the second one").
* "Create a new test plan for Sprint 25" (optionally "... called Release Smoke", "... with the default suites").
  The sprint must exist in the TFS project; if it is unknown or matches more than one sprint, the agent asks.
  A plan with the same name is reused, never duplicated.
* "Open it", "Open the test plan I just created", "Show me more details" (reads state, sprint and suites).
* "Rename the test plan to ...", "Set the test plan to inactive", "Move the test plan to Sprint 26": these change an
  existing plan, so the agent asks "Please confirm" first.

Test suites (the 11 testing types)
* "Now go to Test Suites" opens the suites of the plan you are working on.
* "Select Functional, Integration and Regression, and uncheck Performance": the boxes on the page follow, the new
  suites are created in TFS, read back, and only then reported saved. "Update the test suites" saves the current
  selection again. "Which suites does it have?" reads them.
* Unchecking a suite that already exists never deletes it; the agent says so. "Delete the Performance Testing suite"
  deletes it after you confirm.

## Context and safety

* The conversation remembers the current test plan, its sprint, the suite selection and what "it" refers to. The
  plan shown on the Test Plans or Test Suites page is the current plan, so the agent never changes a different one;
  every reply names the plan. Without a known plan it asks which one.
* The same sentence received twice within 5 seconds (an echo, a repeated recognition, a double click) is answered
  but not executed again. Lookups and navigation can be repeated.
* A task that finishes very quickly is still reported in order: "On it" first, then "Done".
* All writes use the existing TFS services of the Test Plans and Test Suites pages; TFS failures are reported as they are.

## Files (in addition to 23.3.0)

qa_platform/voice: `commands_plans.py` (new), `intents_plans.py` (new), `intents.py`, `registry.py`, `service.py`,
`model.py`, `commands_more.py`. web/js: `voice.js`, `pages/testplans.js`, `pages/test-suites.js`.
