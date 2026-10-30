# Patch 23.2.1 → 23.2.2

A small update for a PC that runs 23.2.1. It contains only the changed application files: no Python packages, no
model and no database change. Apply patch 23.2.1 first if that PC still runs 23.2.0.

## Apply

1. Stop QA Enterprise AI (close its window, or press Ctrl+C in the console).
2. Open the application folder: the folder that holds `START_QA_PLATFORM.cmd`, `qa_platform` and `web`
   (for the offline installation this is `<install folder>\app`).
3. Extract `qa-enterprise-ai-patch-23.2.2.zip` into that folder and choose **Replace the files in the destination**.
4. Start `START_QA_PLATFORM.cmd` again and refresh the browser.

## What changes

* **Test data generation no longer times out.** It used the general 2-minute AI timeout and asked the local model
  for all rows (12 by default) in one answer. It now asks for four rows per call, each call with the *Generation
  timeout* from AI settings (default 600 seconds), and tells the model which rows it already wrote so each call adds
  new scenarios. Activity shows the progress per batch.
* If a later call still times out, the rows already written are kept and saved, with a gap that says generation
  stopped early. If the very first call times out, it is retried once for a single row before reporting the error.
