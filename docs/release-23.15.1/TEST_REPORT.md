# Test report: SkyGuard QA 23.15.1

Run on Linux, Python 3.13, in the build environment. Windows wrappers and the Setup wizard on Windows run in CI.

| Suite | Result |
|---|---|
| New: `tests/test_testcase_generation_231510.py` (lenient JSON, answer shapes, schema grammar sent and capped, retry, cut-off answers, review of flagged cases only, no review when every rule passes, Test Cases API end to end with a model that ignores the format) | 14 passed |
| Same test file against the 23.15.0 `suite_cases.py` | the end-to-end test gets no test case (reproduces the report) |
| Test case and test data generation suites (`test_suite_cases*.py`, `test_design.py`, `test_data_2312.py`, `test_speed_2339.py`, `test_case_data_fixes.py`) | all passed |
| Full suite on 23.15.1 (installer test run separately, above) | 1,217 passed, 484 skipped (the 23.15.0 installer test skips itself on the 23.15.1 tree) |
| Installer: `tests/test_cumulative_patch_231510.py` (build, apply, verify, rollback, apply again on every release tree 23.3.35 to 23.15.0; wrong version and changed file refused; automatic rollback; OCR runtime; side-by-side; Setup detect and repair; Arabic voice kept) | 44 passed |
