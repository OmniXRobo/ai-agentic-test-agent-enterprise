# Patch 23.3.3: AI-Driven Automation by voice (milestone 6)

One patch for a PC on 23.2.2, 23.3.0, 23.3.1 or 23.3.2. It includes everything from the earlier 23.3 patches (see
PATCH_23.3.0.md to PATCH_23.3.2.md).

## What you can say

Generate (the existing Script Studio engine: Python with Playwright, Page Objects, flows, test data)
* "Select the current test plan, choose Python with Playwright, and generate the automation script": scripts for the
  test cases in the plan's selected suites. Also "generate scripts for the regression suite", "generate automation
  scripts for these test cases" (the ones just saved) and, as before, "generate automation for test case 105".
* The reply gives the real counts. A script is saved only after it passed the syntax and static checks, and it keeps
  its links to the test case, user story and acceptance criteria. Scripts that already exist are not overwritten.
  With one script, its code opens. Nothing is run until you ask.
* Python with Playwright is the only framework here; asked for Selenium, Java and so on, the agent says so.

Run settings (also on the Settings page, under "Automation runs")
* "Change the browser to Chrome" / "use Edge" (only if it is installed on the PC), "set timeout to 30 seconds"
  (1 to 120), "disable step screenshots", "turn on video", "turn on the trace", "run headless",
  "what are the automation settings".
* Screenshots on failure are always captured. The framework has no retry setting; the agent says so instead of
  pretending.

Check, run and fix
* "Validate the script": Python syntax and the framework's static checks, read back. It does not run the test.
* "Run this test", "Run this test in the test environment": runs the script and reports the real result (passed with
  the number of steps, or the failed step and its error). A named environment must be the script's environment or one
  saved in Application Credentials for its application, otherwise it does not run. Production asks for confirmation.
* "Show me the failure": the failed step, the error and the likely cause from the last run; the Result tab opens.
* "Fix the locator and regenerate": rediscovers the page and regenerates the script; the old version stays in History.
  It is not reported fixed until you run it again.

## Files (in addition to 23.3.2)

qa_platform/voice: `commands_automation.py` (new), `intents_automation.py` (new), `intents.py`, `registry.py`,
`service.py`, `model.py`, `commands_more.py`. qa_platform/automation/framework/runner.py (passes the saved browser,
timeout and step-screenshot settings; nothing changes while they are not set). qa_platform/api/core.py (validates
them). web/js/pages/settings.js ("Automation runs").
