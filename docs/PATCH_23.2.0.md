# Patch 23.1.0 → 23.2.0

A small update for a PC where 23.1.0 (or the development setup) already runs. It contains only the changed
application files: no Python packages, no model and no database change.

## Apply

1. Stop QA Enterprise AI (close its window, or press Ctrl+C in the console).
2. Open the application folder: the folder that holds `START_QA_PLATFORM.cmd`, `qa_platform` and `web`
   (for the offline installation this is `<install folder>\app`).
3. Extract `qa-enterprise-ai-patch-23.2.0.zip` into that folder and choose **Replace the files in the destination**.
4. Start `START_QA_PLATFORM.cmd` again and refresh the browser with Ctrl+F5, so it loads the new pages.

With a git clone instead: `git pull`, then restart.

The offline validation (`VALIDATE_OFFLINE.cmd`) reports the replaced files as changed compared with the 23.1.0
manifest. That is expected for a patched installation; the clean offline installer is built again for a release.

## What changes

* **Test case generation no longer times out on large stories.** Two acceptance criteria per AI call, each call
  with its own *Generation timeout* (AI settings, default 600 seconds). A call that times out is retried one
  criterion at a time; a criterion that still fails is reported and the other criteria keep their test cases.
* **Concrete steps**: one action per step naming the page, field or button and the value; observable expected
  results. Summary steps such as "Complete the purchase workflow" are flagged and sent back to the AI.
* **Tags** on created test cases: `AI Generated; <testing type>; US<story>; <criterion ids>`.
* **Several suites at once**: check the suites on Test Cases; each gets its own test cases. *Cases per criterion*
  replaces *Max per story*.
* **Test Cases list**: check boxes, Select all, Delete selected, Edit and Delete on every row; Edit includes the
  steps. Deleting a test case in TFS is permanent and asks first.
* **Test Data** loads the data already in a test case's Test Data tab (Test Data Matrix), writes generated data
  there in the same Combination / Field / Expected Result layout, generates for selected test cases or for several
  user stories, and its coverage matrix recognises test cases created outside the application.
