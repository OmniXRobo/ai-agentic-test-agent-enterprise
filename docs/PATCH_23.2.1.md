# Patch 23.2.0 → 23.2.1

A small update for a PC that runs 23.2.0 (or 23.1.0 with patch 23.2.0). It contains only the changed application
files: no Python packages, no model and no database change.

## Apply

1. Stop QA Enterprise AI (close its window, or press Ctrl+C in the console).
2. Open the application folder: the folder that holds `START_QA_PLATFORM.cmd`, `qa_platform` and `web`
   (for the offline installation this is `<install folder>\app`).
3. Extract `qa-enterprise-ai-patch-23.2.1.zip` into that folder and choose **Replace the files in the destination**.
4. Start `START_QA_PLATFORM.cmd` again and refresh the browser once with Ctrl+F5.

From this version on, the browser loads the pages of the installed version by itself after a patch.

## What changes

* **Generated test cases are created in TFS.** With *Create in TFS when done* (checked by default), every generated
  case without an error is created in the selected suite when generation ends, tagged and linked to its user story.
  A case the rules found to duplicate an existing linked test case reuses that test case. Cases with errors wait for
  review; the result shows how many were created, reused and held back.
* **Faster generation.** The second AI pass (a review that can take as long as the first call) now runs only for
  errors, uncovered criteria, summary steps and vague expected results. Check *Thorough review (slower)* to also
  send cases with other warnings back to the AI.
* **No stale pages after a patch.** Pages and scripts are revalidated and loaded from a versioned path, so an old
  cached Test Cases page can no longer stay in the browser.

Cases generated before this patch are still in the application: open Test Cases, then the run from the Execution
History (or `#/test-cases?runs=<run id>`), and click *Create selected test cases in this suite*.
