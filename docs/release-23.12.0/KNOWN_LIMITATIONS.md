# Known limitations: 23.12.0

Stated plainly so that nobody has to discover them.

## Where it was tested

* **Linux build machine, Chromium.** Every automated test ran on Linux with Python 3.13 and Playwright's Chromium.
  The Setup wizard was built with NSIS and checked under Wine (see TEST_REPORT.md). A real Windows 10 / 11 PC, Microsoft
  Edge and Google Chrome were not available here; the repository's Windows CI job installs and verifies the upgrade on
  a Windows runner when the pull request runs.
* **APIs.** API Testing was tested against a local Orders API written for the tests (`tests/api_site.py`: HTTP, HTTPS
  with a private CA, Basic, Bearer, API key, OAuth 2.0 client credentials, redirects, slow and broken endpoints). Your
  own APIs and OpenAPI documents were not available. IPv6 parsing and URL building were tested; a live IPv6 request
  was **not executed** because this machine has no IPv6 loopback.
* **Not executed here:** OAuth 2.0 password grant, client certificate authentication against a server that demands
  one, session sign-in (cookie) against a real login, proxies, and Postman collections with folders and variables of
  every kind. The code paths exist; treat them as untested until you try them on a test environment.
* **saucedemo.com** could not be reached from the build machine (the network refused it, HTTP 403). The AI Agent
  flows were tested on the local SauceDemo stand-in and the stand-in shop only.
* **Local AI model.** The local Qwen model was not running here. Failure classification ran with the rules only; the
  AI refinement and "Suggest with local AI" were checked for refusal and validation, not for the quality of real
  answers.

## API Testing scope

* JSONPath is a documented subset (`$`, `.name`, `['name']`, `[n]`, `[*]`, `.*`, `..name`, `.length`); filters
  such as `[?(@.x > 1)]` are not supported and are refused with a message.
* JSON Schema: the keywords OpenAPI uses are checked; `discriminator`, `$dynamicRef`, `unevaluatedProperties` and
  remote references are reported as not checked, never assumed to pass.
* Sorting tests are always Needs review: a specification rarely says which field or order a parameter sorts by.
* A test whose `{{variable}}` is defined nowhere is FAILED (a definition problem); it is BLOCKED only when an earlier
  step of its chain failed to set it.
* GraphQL, SOAP/WSDL, gRPC and WebSocket APIs are not supported. XML bodies are shown and can be checked with "body
  contains", not with a schema.
* Performance: the response-time assertion measures single requests. API Testing is not a load test (use Performance
  Testing).
* Script runs use this PC's Python and the httpx/pytest shipped with SkyGuard; the downloaded script needs
  `pip install httpx pytest` elsewhere.
* Residual security risks (DNS rebinding between two lookups, Python-level script guard) are listed in
  SECURITY_REPORT.md section 4.

## Integration Comparison

* Scheduling replays the saved comparison as configured (captured data, files or the guided recording). Inputs that
  need a person (a screenshot taken by hand) are not refreshed by a schedule.
* "Editable comparison scripts" are the existing editable recorded steps, filters, mapping and calculation rules;
  there is no separate Python script for a comparison in this release.

## Agent Workflow

* Availability is checked when the page loads the agent list: Playwright installed, the AI server's last health check,
  the comparison, scheduler and API Testing services present. It is not a full self-test; use *Run system
  diagnostics* for that.

## Packaging

* Setup.exe is not code-signed, so Windows SmartScreen warns and says *Unknown publisher*, although the file names
  Muhammad Tufail as its publisher. Only a code-signing certificate removes that; the build signs Setup.exe when one is
  given. See INSTALLATION_GUIDE.md.
