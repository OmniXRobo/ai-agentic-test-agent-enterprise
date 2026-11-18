# Security report: SkyGuard QA 23.12.0 and extension 1.3.1

Scope: everything 23.12.0 changes since 23.11.2 (commit `54b69f0`), with most attention on the new API Testing
module, because it is the first SkyGuard feature that sends requests a person defines to hosts a person names. For
23.11.2 and earlier, see `previous-23.11.2/SECURITY_REPORT.md` and the reports under `previous-*`.

## 1. Tools run (18 November 2026)

| Check | Tool | Result |
|---|---|---|
| Python static analysis of the new and changed files (`qa_platform/apitesting/*`, `qa_platform/api/apitesting.py`, `comparison/schedule.py`, `automation/scheduler.py`, `opsagent/service.py`, `api/core.py`, `install_patch.py`) | `bandit 1.8.6 -ll` | 5 findings, all B608 "SQL built from strings". Each was read: the strings are the code's own table names and `?` placeholders (`UPDATE api_apps ... = ?`, `DELETE FROM {table}` over a fixed tuple, `IN (?,?,...)`). Every value is bound as a parameter. **No injectable query.** One medium finding (B314, XML parsing of the JUnit report) was fixed: the file is the report of the pytest run SkyGuard started, it is now size-capped, and the standard library parser resolves no external entities. |
| Known vulnerabilities in dependencies | `pip-audit 2.10.1 -r requirements.txt` | **No known vulnerabilities found.** |
| New dependencies | `requirements*.txt` | **None added.** PyYAML 6.0.3 (MIT) is vendored as pure Python in `qa_platform/vendor/yaml` for offline YAML OpenAPI files; only `safe_load` is used and the C loader was removed. No known vulnerability in 6.0.3. |
| Secrets in the change | Regular-expression scan for private keys, tokens, API keys and password assignments | **No real secrets.** The only credentials are the fixed test values of `tests/api_site.py`. |

## 2. API Testing: threats and the controls that answer them

| Threat | Control | Test |
|---|---|---|
| **SSRF against SkyGuard itself.** The local API treats a program on this PC that sends no browser headers as the local administrator. An API test pointed at `127.0.0.1:8765` would therefore be able to change users or settings. | Every request, every redirect hop and every specification URL goes through `safety.Policy.check_url`. Any address that resolves to this PC (127.0.0.0/8, ::1, 0.0.0.0, IPv4-mapped IPv6, the decimal form 2130706433, `localhost`) on SkyGuard's own port is refused. | `test_policy_refuses_unauthorized_hosts_own_port_metadata_and_bad_schemes`, `test_redirect_to_unauthorized_host_is_blocked`, installer check *API Testing and new agents (23.12.0)* |
| SSRF to other hosts, cloud metadata, network scanning | A person must tick "I am authorized to test this API" when creating an application. Only its authorized targets (host, host:port, `*.domain` with at least two labels, CIDR at most /16 for IPv4 and /64 for IPv6) can be contacted. 169.254.169.254 and other metadata addresses are always refused. Only `http` and `https`; no user name or password in URLs. Nothing is discovered or scanned: SkyGuard sends only the requests of the tests. | same tests, `test_application_needs_authorization_and_valid_target` |
| Redirects to another host | Redirects are followed by SkyGuard, at most 5, each hop checked by the policy; secret headers are dropped when the host changes. | `test_redirect_to_unauthorized_host_is_blocked` |
| Secrets in plain text | Passwords, tokens, API keys, client secrets and certificate passwords go only to the existing secure store (Windows Credential Manager, namespace `apicred`). They are never returned by the API (only "stored / not stored"), never written to the configuration, tests, scripts, reports, results, audit or logs. Responses and requests are redacted (values, Base64 forms, Authorization/Cookie/API key headers, JSON keys that look secret, query parameters). Imported cURL and Postman data loses its secrets. Saving a script that contains a stored secret is refused. | `test_openapi_import_generate_and_run_with_basic_auth_and_redaction` (checks the database file, logs, report, export and audit), `test_authentication_types`, `test_api_key_in_query_is_redacted_from_url`, `test_curl_postman_and_python_imports_keep_no_secrets_and_never_execute`, `test_generated_pytest_httpx_script_runs_without_secrets_in_the_file` |
| TLS downgrade | Certificate verification is on by default and cannot be turned off for Production. A private CA can be uploaded (PEM, at most 1 MB, kept in the data folder). | `test_https_with_uploaded_ca_and_verification`, `test_application_needs_authorization_and_valid_target` |
| Destructive requests | POST, PUT, PATCH and DELETE are marked "changes data". They run only after the person confirms them for that run; a view-only account cannot confirm; Production needs the application setting "allowed" and confirmation, and never runs them from a schedule. Two runs that change data on the same application never overlap. Retries are only for GET/HEAD/OPTIONS. | `test_state_changing_tests_need_confirmation_and_never_run_on_production_from_schedule`, `test_view_only_user_cannot_change_or_confirm` |
| Imported content executed | cURL is parsed with `shlex` (`; touch file` is never run, `@file` is never read); Python is parsed with `ast` (nothing is imported or run); Postman and OpenAPI are data. Imported tests start as Needs review. | `test_curl_postman_and_python_imports_keep_no_secrets_and_never_execute` |
| Hostile specification files | 8 MB limit, JSON/YAML node limit against alias bombs (billion laughs), reference depth and cycle limits, only local and uploaded references are resolved; remote references are listed, never fetched. | `test_spec_upload_bad_documents_are_refused_clearly`, `test_swagger2_yaml_with_local_multi_file_and_unresolved_external_reference` |
| Unsafe Python in scripts | Generated and edited scripts pass the existing policy (`python_scripts.validate`) with an API allowlist (httpx, requests, json, re, ...; no subprocess, os.system, eval/exec, sockets, file writes). The script runs in a separate pytest process with a time limit, secrets only in environment variables for that run, output redacted, and a network guard (`sitecustomize`) that allows connections only to the authorized targets. | `test_generated_pytest_httpx_script_runs_without_secrets_in_the_file` |
| Resource exhaustion | Response size limit (default 5 MB, streamed), timeouts, rate limit per application, at most 4 tests at the same time. | `test_response_time_timeout_and_connection_failure` |
| XSS through responses | The page escapes every value (`esc`); the JSON tree builds text nodes from escaped strings; the HTML report escapes every field and is served with `Content-Security-Policy: default-src 'none'`. | `test_api_testing_page_end_to_end` (real browser, no page errors) |
| Prompt injection through API data | The local AI only refines a failure's classification, with the rules' facts; its answer is validated against a fixed schema and class list, and text that tries to give instructions is ignored. AI test suggestions are always saved as Needs review. | `qa_platform/apitesting/analysis.py` (`ai_view`), covered by the defect analysis tests |
| CSRF / other web pages on this PC | Unchanged and applied to the new routes: the Host check (DNS rebinding), the Origin / Sec-Fetch-Site check for every request that changes something, the SameSite=Strict session cookie, and role checks (view-only roles can only run tests that do not change data). | existing guard tests; `test_view_only_user_cannot_change_or_confirm` |
| Path traversal | Uploaded certificate names are reduced to `[A-Za-z0-9._-]` and prefixed with the application id; files are only read from the certificates folder by name. | code review |
| TFS publication | Unchanged shared publisher: drafts only, approval by a permitted role, read-back of the work item, duplicate prevention by signature. | `test_draft_is_published_to_tfs_only_after_approval_with_real_work_item`, `test_contract_violation_and_server_error_are_classified_and_drafted` |

## 3. Other changes

* **Scheduled Runs** delegate API and comparison items to their own services; no new route, no new permission.
* **Agent Workflow** availability is computed on the server from what is installed; nothing is exposed beyond a
  short reason ("Playwright is not installed", "On-Premise AI is offline").
* **Setup footer**: text change only.
* **Extension and companion 1.3.1**: unchanged in this release; permissions, routes, pairing and the local channel
  are as in 23.11.2.

## 4. Residual risks (not fixed, stated so they can be judged)

1. **DNS rebinding of an authorized host name during a run.** The policy resolves a name before each request and
   refuses forbidden addresses, but httpx resolves again when it connects. A DNS server that answers differently
   between the two lookups could make one request reach an address the policy would have refused. Use IP addresses
   or names from your own DNS for sensitive environments.
2. **Script isolation is a policy and a guard, not an operating system sandbox.** The import and call policy plus the
   socket guard stop ordinary scripts; a determined person with edit rights could still find a way around a Python
   level guard. Only accounts with edit rights can save scripts, and every save is versioned and audited.
3. **Network access mode.** When SkyGuard is opened to colleagues (Network access on), it also listens on this PC's
   network address. Requests there are not "local programs" and need a signed-in account, so an API test aimed at
   that address has no administrator rights; it is still better not to add your own PC as an authorized target.
4. **Python 3.13 strict certificate checks.** A private CA without the key usage extension is rejected
   ("CA cert does not include key usage extension"). This is correct behaviour; Test Connection shows the message.
   Such a CA must be reissued; verification cannot be relaxed for it.
5. **Setup.exe is not code-signed** (no certificate was available).
