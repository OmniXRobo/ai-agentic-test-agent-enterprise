"""Failure & Quality Analysis of a failed framework run.

Classifies the likely cause from what the run recorded: the failed step and its
error, the Test Case's expected result, the browser state at the failure (URL,
visible error messages, console errors, failed requests) and the self-healing
attempts. It separates what was observed from the likely cause, gives a
confidence, and says when the evidence is not enough ("Unknown - Manual
Investigation Required"). It never claims an application defect only because a
Playwright exception happened: automation, data, environment and locator causes
are checked first.
"""

from __future__ import annotations

import re
from typing import Any

APPLICATION_DEFECT = "Application Functional Defect"
SCRIPT_DEFECT = "Automation Script Defect"
LOCATOR_CHANGED = "Locator Changed"
TEST_DATA = "Test Data Problem"
ENVIRONMENT = "Environment Failure"
AUTHENTICATION = "Authentication Failure"
NETWORK = "Network/API Failure"
TIMEOUT = "Timeout"
MISMATCH = "Expected Result Mismatch"
PERFORMANCE = "Application Performance Problem"
UNKNOWN = "Unknown - Manual Investigation Required"

#: classification -> (is it likely an application problem?, what to do next)
CLASSIFICATIONS: dict[str, tuple[bool, str]] = {
    APPLICATION_DEFECT: (True, "Review the evidence and raise a defect if the behaviour is wrong."),
    MISMATCH: (True, "Compare the expected result with the screenshot; raise a defect or correct the expected result."),
    AUTHENTICATION: (False, "Check the credentials saved for this script or application, then Play again."),
    TEST_DATA: (False, "Correct the Test Data or credentials of this script, then Play again."),
    LOCATOR_CHANGED: (False, "Review the element in the screenshot and update the Page Object locator."),
    SCRIPT_DEFECT: (False, "Fix the Python script (Edit Script) and Play again."),
    ENVIRONMENT: (False, "Check that the application URL is reachable from this PC, then Play again."),
    NETWORK: (True, "A request the page depends on failed; check the server or API, then Play again."),
    TIMEOUT: (False, "The page did not reach the expected state in time; check the screenshot and the timeout."),
    PERFORMANCE: (True, "The application responded too slowly; check its performance."),
    UNKNOWN: (False, "Review the screenshots and logs of the run."),
}

_AUTH = re.compile(r"invalid (credentials|password|user|login)|incorrect (password|user)|login failed|"
                   r"authentication failed|access denied|not authori[sz]ed|unauthori[sz]ed|wrong password|"
                   r"account (is )?(locked|disabled)|sign.?in failed", re.I)
_SCRIPT = re.compile(r"\b(NameError|AttributeError|TypeError|ImportError|ModuleNotFoundError|SyntaxError|"
                     r"IndentationError|fixture '\w+' not found)\b")
_ENV = re.compile(r"net::ERR_(CONNECTION_REFUSED|NAME_NOT_RESOLVED|ADDRESS_UNREACHABLE|INTERNET_DISCONNECTED|"
                  r"CONNECTION_TIMED_OUT|CONNECTION_RESET|CERT_\w+|SSL_\w+)|ECONNREFUSED|No application URL|"
                  r"Executable doesn't exist|browserType\.launch", re.I)


def diagnose(result: dict[str, Any], record: dict[str, Any], healing: list[dict[str, Any]]) -> dict[str, Any]:
    """``result`` is the run result (steps, error, diagnostics); ``healing`` the run's healing events."""
    steps = result.get("steps") or []
    failed = next((s for s in steps if s.get("status") == "FAILED"), None)
    error = (failed or {}).get("error") or result.get("error") or ""
    diag = result.get("diagnostics") or {}
    messages = [m for m in diag.get("page_messages") or [] if m]
    console = diag.get("console") or []
    network = diag.get("network") or []
    actions = (failed or {}).get("actions") or []
    last_action = actions[-1] if actions else {}
    is_check = (last_action.get("action") or "").startswith("expect") or bool((failed or {}).get("checkpoint"))
    heal_failed = [h for h in healing if h.get("result") == "failed"]
    heal_any = [h for h in healing if h.get("healed_selector")]
    observed: list[str] = []
    if diag.get("url"):
        observed.append(f"Page at the failure: {diag['url']}" + (f" ({diag['title']})" if diag.get("title") else ""))
    observed += [f"Message on the page: \"{m}\"" for m in messages[:5]]
    observed += [f"Browser console error: {c}" for c in console[:5]]
    observed += [f"Request problem: {n}" for n in network[:5]]
    for h in heal_failed[:3]:
        best = max((c.get("confidence", 0) for c in h.get("candidates") or []), default=0)
        observed.append(f"Self-healing could not replace {h.get('original_selector')} (best candidate confidence "
                        f"{best:.2f})")
    passed = [s.get("description") for s in steps if s.get("status") == "PASSED"]
    if passed:
        observed.append(f"{len(passed)} earlier step(s) passed; the last was \"{passed[-1]}\"")

    classification, confidence, reason = _classify(error, failed, record, is_check, messages, console, network,
                                                   heal_failed, result)
    likely_app, next_step = CLASSIFICATIONS[classification]
    return {
        "agent": "Failure & Quality Analysis Agent",
        "run_id": result.get("run_id"),
        "test_case_id": result.get("test_case_id"),
        "result": result.get("status"),
        "failed_step": (failed or {}).get("index"),
        "failed_step_title": (failed or {}).get("description"),
        "expected": (failed or {}).get("expected"),
        "actual": (failed or {}).get("actual") or error or None,
        "error": error or None,
        "observed": observed,
        "classification": classification,
        "confidence": confidence,
        "reason": reason,
        "likely_application_problem": likely_app,
        "automation_problem": not likely_app and classification != UNKNOWN,
        "self_healing": {"attempted": len(heal_failed) + len(heal_any), "failed": len(heal_failed)},
        "next_step": next_step,
        "evidence": {
            "failure_screenshot": (failed or {}).get("screenshot"),
            "full_page_screenshot": (failed or {}).get("full_page"),
            "trace": (result.get("evidence") or {}).get("trace") or [],
            "log": (result.get("evidence") or {}).get("log"),
        },
    }


def _classify(error: str, failed: dict[str, Any] | None, record: dict[str, Any], is_check: bool, messages: list[str],
              console: list[str], network: list[str], heal_failed: list[dict[str, Any]],
              result: dict[str, Any]) -> tuple[str, int, str]:
    text = error or ""
    page_text = " ".join(messages)
    if failed is None and not text:
        return UNKNOWN, 30, "The run failed without a recorded failing step or error."
    if "Application credentials missing" in text or "Test data field" in text or "is not in this combination" in text:
        return TEST_DATA, 95, "The test could not get the credentials or data it needs."
    if _SCRIPT.search(text):
        return SCRIPT_DEFECT, 90, "The Python code itself raised an error before the application was checked."
    if _ENV.search(text):
        return ENVIRONMENT, 90, "The browser could not reach the application or could not start."
    if _AUTH.search(page_text) and (record.get("credential_role") or "credentials" in text.lower() or is_check):
        return AUTHENTICATION, 85, f"The application rejected the sign-in: \"{_first(messages, _AUTH)}\"."
    server_errors = [n for n in network if re.search(r"HTTP 5\d\d|failed:", n)]
    if server_errors and (is_check or "Timeout" in text):
        return NETWORK, 75, f"A request the page depends on failed ({server_errors[0]})."
    if "LocatorNotFound" in text or "Element not found" in text or heal_failed:
        best = max((c.get("confidence", 0) for h in heal_failed for c in h.get("candidates") or []), default=0)
        if best >= 0.4:
            return LOCATOR_CHANGED, 70, ("The element was not found with its locators, but a similar element is on the "
                                         f"page (best match {best:.2f}): the UI probably changed.")
        if messages:
            return APPLICATION_DEFECT, 55, ("The expected element is not on the page and the page shows: "
                                            f"\"{messages[0]}\".")
        return LOCATOR_CHANGED, 50, "The element was not found and no similar element was confident enough."
    if is_check and ("AssertionError" in text or "expected" in text.lower() or "to_contain_text" in text
                     or "to_have" in text or "to_be_visible" in text):
        if messages:
            return APPLICATION_DEFECT, 80, f"The application showed \"{messages[0]}\" instead of the expected result."
        return MISMATCH, 75, "The page did not show the expected result."
    if "Timeout" in text:
        if (failed or {}).get("duration_ms", 0) and any((a.get("action") == "goto") for a in (failed or {}).get("actions", [])):
            return PERFORMANCE, 60, "The page did not finish loading within the timeout."
        return TIMEOUT, 60, "An action did not complete within the timeout."
    if console:
        return APPLICATION_DEFECT, 50, f"The page logged an error: {console[0]}"
    return UNKNOWN, 30, "The evidence does not point to a single cause."


def _first(messages: list[str], pattern: re.Pattern[str]) -> str:
    return next((m for m in messages if pattern.search(m)), messages[0] if messages else "")
