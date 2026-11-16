# 23.8.0 test report

Environment: Linux, Python 3.13, Chromium (Playwright), Tesseract OCR. Windows OCR and the Windows wrappers were not
run here (no Windows machine); the wrappers call the same `install_patch.py` that was tested.

## Full suite

| Run | Result |
| --- | --- |
| 23.7.2 before the change (baseline) | 799 passed, 65 skipped, 0 failed |
| 23.8.0 after the change | **821 passed, 77 skipped, 0 failed** |

The 12 extra skips are the 23.7.2 cumulative patch tests, which are superseded by `tests/test_cumulative_patch_23800.py`
(the same pattern as earlier releases). Every other existing test passes as before.

## Required scenarios (`tests/test_integration_workspace_23800.py`, 21 tests, all pass)

| # | Scenario | Test | Result |
| --- | --- | --- | --- |
| 1 | Table vs table | `test_01_table_vs_table` | PASS |
| 2 | Table vs calculated percentages | `test_02_table_vs_calculated_percentages`, `test_02b_unapproved_rule_is_a_warning` | PASS |
| 3 | Table vs graph | `test_03_table_vs_graph` | PASS |
| 4 | Three applications, different field names | `test_04_three_apps_different_field_names` | PASS |
| 5 | Status codes vs descriptions | `test_05_status_codes_vs_descriptions`, `test_05b_unapproved_status_mapping_never_passes` | PASS |
| 6 | Table vs KPI dashboard | `test_06_table_vs_kpi_dashboard` | PASS |
| 7 | Three screenshots: OCR, analysis, comparison | `test_07_three_screenshots_ocr_and_compare`, `test_07b_screen_reader_layouts` | PASS |
| 8 | Tester corrects a mapping | `test_08_tester_corrects_a_mapping` | PASS |
| 9 | Saved mapping reused | `test_09_saved_mapping_is_reused` | PASS |
| 10 | Calculation mismatch and defect report | `test_10_calculation_mismatch_and_defect_report` | PASS |
| 11 | Legitimate business-rule difference | `test_11_legitimate_business_rule_difference`, `test_11b_tester_decision_accepts_a_known_difference` | PASS |
| 12 | Missing, duplicate and mismatched records | `test_12_missing_duplicate_and_mismatched_records` | PASS |
| 13 | Incomplete screenshots give warnings, not PASS | `test_13_incomplete_screenshots_give_warnings_not_pass`, `test_13b_correcting_an_ocr_value` | PASS |
| 14 | Existing functionality still answers | `test_14_existing_functionality_still_answers`, full suite above | PASS |
| — | Security: formula injection, non-image upload, password never stored or returned | `test_security_formula_upload_and_password` | PASS |
| — | Live capture with the browser and PDF report | `test_live_capture_and_pdf` | PASS |

## Cumulative patch (`tests/test_cumulative_patch_23800.py`, all pass)

Apply, verify, rollback and apply again on real 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1 and 23.7.2
trees; migration 19 with no rows lost; automatic rollback after a failure; refusal of a wrong version and of a
changed patch file; no network and no administrator rights.

## Browser walkthrough

A real server with sign-in, three screenshots of the sample pages read by Tesseract, analysis, mapping, run and
the detail view. No page errors. Screenshots are in `evidence/`:
`1-apps.png`, `2-analysis.png`, `3-mapping.png`, `4-results.png`, `5-detail.png`.
