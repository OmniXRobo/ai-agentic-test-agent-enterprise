"""Execution-layer authorization: independent of the application's own permission checks, run before every step that
changes business data. All six checks must pass; the first failure stops the step with its reason.

1 active account is the account the plan assigned to this stage
2 the account's identity is verified from the application's own display
3 its role is verified, and the approved workflow allows that role (the user's description is never trusted)
4 the workflow is the approved version that was previewed (and, where learned, the record has the expected status)
5 the target record is identified (no "first row" guesses)
6 the user confirmed this exact preview (hash), the confirmation is fresh and was not reused
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .util import norm

CONFIRMATION_TTL_MIN = 60


def check(*, plan: dict[str, Any], stage: dict[str, Any], session: dict[str, Any], workflow: dict[str, Any] | None,
          run: dict[str, Any], record: dict[str, Any], target_ok: bool = True,
          mode: str = "testing") -> list[str]:
    problems: list[str] = []
    if session.get("account_id") != stage.get("account_id"):
        problems.append("The signed-in account is not the account this stage must use.")
    if not session.get("verified"):
        problems.append("The signed-in identity has not been verified from the application.")
    allowed = {norm(r) for r in (workflow or {}).get("data", {}).get("roles", []) if r}
    if allowed:
        if not session.get("role_verified"):
            problems.append("The account's role could not be verified from the application, so a role-restricted "
                            "operation is not performed.")
        elif norm(session.get("role")) not in allowed:
            problems.append(f"The verified role '{session.get('role')}' is not allowed to perform this operation "
                            f"(allowed: {', '.join(sorted(allowed))}).")
    elif mode == "production":
        problems.append("Production mode needs the workflow's allowed roles to be learned and approved first.")
    if not workflow or workflow.get("state") != "approved":
        problems.append("The workflow is not approved.")
    elif workflow.get("id") != stage.get("workflow_entry_id"):
        problems.append("The workflow changed after the preview; it must be previewed and confirmed again.")
    if not target_ok:
        problems.append("The target record could not be identified unambiguously.")
    if not run.get("confirmed_at") or not run.get("preview_hash"):
        problems.append("This operation has not been confirmed.")
    elif run.get("confirmed_hash") and run.get("confirmed_hash") != run.get("preview_hash"):
        problems.append("The confirmation does not match the current preview.")
    else:
        try:
            when = _dt.datetime.fromisoformat(run["confirmed_at"])
            started = run.get("execution_started_at")
            if not started and (_dt.datetime.now().astimezone() - when).total_seconds() > CONFIRMATION_TTL_MIN * 60:
                problems.append("The confirmation expired before execution started; confirm the preview again.")
        except (TypeError, ValueError):
            problems.append("The confirmation time is invalid.")
    return problems
