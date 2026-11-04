# Patch 23.3.13: test data on the TFS "Test Data" tab

Cumulative: includes 23.3.12 and everything before it.

The project's Test Case type has a custom "Test Data" tab with the fields "Test Data ID" and "Test Data". Until now
AI Test Design wrote a test case's test data only into the Summary, and Write to TFS wrote either the tab or the
Summary. Now, when the type has a Test Data field:

* **New test cases (AI Test Design > publish):** the test data lines ("product_name = 'Wireless Headphones'") go to
  the Test Data field as a Test Data Matrix (Combination 1 with one line per value), and the Test Data ID gets
  `US<story>-TC<n>`. The Summary is unchanged.
* **Test Data > Write to TFS:** the generated rows go to the Test Data field and the Summary; the Test Data ID gets
  the dataset key (`DS-<test case>-<type>`). The voice command "write test data" does the same.
* An HTML field gets the formatted matrix, a plain-text field the same lines as text. A numbers-only Test Data ID
  is left empty. If TFS refuses the tab fields (HTTP 400), the test case is created or updated without them and the
  log says so.
* Fields are found by name (a custom field whose name or reference name contains "test data"), as before.

Files: `design/tfs_test_data.py` (new), `design/suite_cases.py`, `design/data_agent.py`, `design/service.py`,
`web/js/pages/test-data.js`, `__init__.py`; `tests/test_tfs_test_data_tab_2313.py`.
