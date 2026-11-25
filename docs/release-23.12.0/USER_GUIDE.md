# User guide: API Testing (23.12.0)

## 1. Add the application

1. **Automation Studio > API Testing > New application.**
2. Enter a name, the environment and either the complete base URL (`https://orders.intranet.local:8443/api/v1`) or the
   protocol, host name or IP address (IPv4 or IPv6, for example `10.20.30.40` or `fd00::10`), port and base path.
3. Add other **authorized targets** if tests or the specification URL use other hosts (one per line: `host`,
   `host:port`, `*.corp.local`, or a network such as `10.20.30.0/24`). The base URL's host is added for you.
4. Choose the **Authentication** type and fill the non-secret fields (user name, key name, token URL, client ID).
5. Tick **I am authorized to test this API** and press **Create application**.
6. Enter the password, token, API key or client secret and press **Save** next to it. It goes to the Windows Credential
   Manager; the page then only shows *stored*. Upload a trusted CA or a client certificate if the API needs one.
7. Press **Test Connection**. Each step (authorized target, name resolution, TCP, TLS, HTTP answer) is shown with
   its result. "404 ... reachable" is normal for many APIs: nothing is served at the base path itself.

## 2. Import and generate

* **Import & generate > OpenAPI / Swagger**: choose the file (JSON or YAML; select every file it refers to, then the
  main one) or enter the intranet URL of the document, then **Import**. Tests are generated at once if *Generate
  tests* is ticked. References that could not be read offline are listed; upload those files too.
* Choose the kinds of tests and press **Generate from the specification** again whenever the specification changes.
* **Import other definitions**: paste cURL commands, a Postman collection, a Python requests/httpx script or a SkyGuard
  export. Nothing in them is run. Secrets in them are dropped.
* **Suggest with local AI**: describe a behaviour; the suggestions are saved as Needs review.

## 3. Review, edit, run

* **Tests** lists every test with its request, kind and status. *Needs review* means the specification does not
  document the expected result. Open it, check the assertions, then **Approve expected result** (or tick several and
  **Approve selected**).
* **Open** a test to edit it. Assertions are JSON, for example:
  `{"type": "status", "expected": ["201"]}`, `{"type": "jsonpath", "path": "$.items[*].status", "op": "all_equal", "value": "new"}`,
  `{"type": "schema"}` (the documented schema), `{"type": "response_time", "max_ms": 800}`.
  `{{name}}` uses a value extracted by an earlier test or a column of the test data rows.
* **Run selected** / **Run all** / a suite's **Run**. If tests change data, you are asked first; Cancel runs only
  the tests that do not change data and reports the others as BLOCKED.
* **Runs & results** shows the counts, each result and its classification and draft. **View** opens the response
  viewer. **HTML report** opens a printable report.

## 4. Suites and chains

Tick tests in the order they must run, **Suite from selected**, and tick **Chain** to pass extracted values on (for
example: create an order and extract `orderId` from `$.id`, then `GET /api/orders/{{orderId}}`).

## 5. Python script

**Generate Script** writes `test_api_<application>.py` (pytest + httpx). The tabs show the script, an editor
(Find / Replace all, Validate, Save, Full screen, Taller / Shorter), the flow, the test data, which secrets are
stored and the environment variables the script reads, its functions, its versions (Restore, Download), and the
last run's result, evidence and output. **Run** runs the saved version with pytest on this PC.
To run it elsewhere: `set SKYGUARD_API_PASSWORD=...` (or `_TOKEN`, `_KEY`, `_CLIENT_SECRET`), then `pytest -q <file>`.

## 6. Schedule

**Scheduled Runs**, filter *Page: API Testing*, tick "<application>: all API tests", a suite, or the Python script,
then **Schedule** or **Run now**. A scheduled run never runs tests that change data on Production, and on other
environments only when the application allows it ("Allowed").

## 7. Failures and TFS

A failed test becomes a draft in **Defect Review** (filter *API Testing*) with the request, response (redacted),
assertions and classification. Review it, **Approve**, then **Publish**. The TFS work item ID and link are saved on the
draft. A repeated failure adds an occurrence to the same draft.
