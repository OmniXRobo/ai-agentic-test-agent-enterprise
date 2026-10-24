AI-Driven Automation framework workspace
=========================================

Python + Playwright + Page Object Model, generated and maintained by the
Automation Engineering Agent of AI Agentic Test Agent Enterprise.

  page_objects/       one class per application page (locators + page actions + validations)
  flows/              reusable business flows built from Page Object methods
  tests/              one pytest file per TFS Test Case, with traceability comments and markers
  test_data/          data per test (combinations, runtime date/time rules); never passwords
  fixtures/           browser, page, test data and credential fixtures
  utilities/          base page, locators with self-healing, reporting/evidence, runtime values
  execution-results/  evidence of runs started from this folder

Every path is relative to this folder: copy the folder to another Windows PC and
it still runs. Do not edit fixtures/ and utilities/ files shipped with the
platform (they are refreshed on upgrade, the previous copy is kept in .versions);
add your own helpers in new files instead.

Run
---
  RUN_TESTS.cmd                                   all tests
  RUN_TESTS.cmd tests\test_tc1268_example.py      one test

Settings (environment variables)
--------------------------------
  BASE_URL                       application address (default: saved with each test's data)
  HEADLESS=1                     run without showing the browser
  BROWSER                        chromium (default), msedge, chrome, firefox, webkit
  <ROLE>_USERNAME / <ROLE>_PASSWORD   credentials of the role named in the test data file,
                                 for example STANDARD_USER_USERNAME and STANDARD_USER_PASSWORD
