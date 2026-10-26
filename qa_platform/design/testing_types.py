"""The testing types a test suite can stand for, and what each one covers.

The same list drives the Test Suites page (create one suite per checked type), suite-aware test case
generation (the ``focus`` text goes into the prompt) and suite-aware test data generation (``data``).
"""

from __future__ import annotations

from typing import Any

TESTING_TYPES: list[dict[str, Any]] = [
    {"key": "functional", "name": "Functional Testing",
     "focus": "Business behaviour of every acceptance criterion: positive paths, negative paths, input validation and "
              "boundaries. One or more cases per testable condition.",
     "data": "Valid, invalid, boundary and just-outside-boundary values, missing/empty/whitespace inputs."},
    {"key": "smoke", "name": "Smoke Testing",
     "focus": "A small set (usually 1-3) of critical checks proving the build's basic health for this story. "
              "Only the most critical happy path; no negative or edge cases.",
     "data": "Minimal, dependable valid data for the critical workflow only.",
     "needs_context": "Which flows are business-critical is not stated in the story; the selection is based on the "
                      "acceptance criteria only."},
    {"key": "sanity", "name": "Sanity Testing",
     "focus": "Narrow, focused checks around the specific change or fix described by the story; verify the change "
              "and its immediate neighbours only.",
     "data": "Data focused on the specified change.",
     "needs_context": "No change description or fix details were supplied; the checks are scoped to the story's "
                      "acceptance criteria."},
    {"key": "regression", "name": "Regression Testing",
     "focus": "Existing behaviour and dependencies that the story's change could break; repeatable baseline checks.",
     "data": "Repeatable baseline datasets and the boundaries most likely affected.",
     "needs_context": "No baseline or list of affected areas was supplied; regression scope is inferred from the "
                      "acceptance criteria and existing linked test cases only."},
    {"key": "integration", "name": "Integration Testing",
     "focus": "Interfaces and data exchange between the components the story touches, including failures of a "
              "connected component and data consistency across them.",
     "data": "Consistent identities and relationships across components, plus failure payloads."},
    {"key": "end_to_end", "name": "End-to-End Testing",
     "focus": "Complete business journeys across the relevant components, from the user's entry point to the "
              "final observable outcome.",
     "data": "One consistent identity/order/record carried through the whole journey."},
    {"key": "api", "name": "API Testing",
     "focus": "Request/response contracts, authentication, authorization, input validation, status codes and "
              "error payloads of the endpoints behind the story.",
     "data": "Typed request payloads, missing fields, invalid types/values and authorization scenarios."},
    {"key": "database", "name": "Database Testing",
     "focus": "Persistence, integrity constraints, relationships, transactions and rollback of the data the story "
              "creates or changes.",
     "data": "Parent/child records, constraint violations, duplicate keys and rollback scenarios."},
    {"key": "ui_usability", "name": "UI and Usability Testing",
     "focus": "Controls, navigation, feedback messages, consistency, accessibility basics and user interaction.",
     "data": "Display-oriented values: long text, Unicode, special characters, localisation formats."},
    {"key": "performance", "name": "Performance Testing",
     "focus": "Workload, response time, throughput, concurrency and resource behaviour of the story's operations. "
              "Never invent thresholds: when the story gives no target, the case states that a target is needed.",
     "data": "Configurable volumes, distributions, cardinality and unique records.",
     "needs_thresholds": True},
    {"key": "security", "name": "Security Testing",
     "focus": "Access control, sessions, sensitive data handling and the abuse cases that apply to the story. "
              "Never invent a security policy: when none is given, the case states what policy is needed.",
     "data": "Synthetic accounts with defined roles and controlled validation payloads for authorized test "
             "environments only.",
     "needs_thresholds": True},
]

BY_KEY = {t["key"]: t for t in TESTING_TYPES}
BY_NAME = {t["name"].lower(): t for t in TESTING_TYPES}
DEFAULT_CHECKED = [t["key"] for t in TESTING_TYPES[:6]]


def get(key: str) -> dict[str, Any]:
    from ..errors import QAError

    if key not in BY_KEY:
        raise QAError("TESTING_TYPE_INVALID", f"Unknown testing type '{key}'.")
    return BY_KEY[key]


def from_suite_name(name: str | None) -> dict[str, Any] | None:
    """'Smoke Testing' or 'Sprint 4 - Smoke Testing' -> the Smoke Testing type."""
    text = (name or "").strip().lower()
    if text in BY_NAME:
        return BY_NAME[text]
    for t in sorted(TESTING_TYPES, key=lambda x: -len(x["name"])):
        if t["name"].lower() in text:
            return t
    return None


def public() -> list[dict[str, Any]]:
    return [{"key": t["key"], "name": t["name"], "focus": t["focus"], "default": t["key"] in DEFAULT_CHECKED}
            for t in TESTING_TYPES]
