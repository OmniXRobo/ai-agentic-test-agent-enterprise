"""23.14.0: what the Security, Red Hat Fuse and ActiveMQ modules share.

* **Targets**: a security application, a Fuse connection or a broker connection. Secrets (passwords, tokens, keys)
  are kept in the OS credential store (the existing vault, ``SecretStore``), never in the database, settings, reports,
  emails, AI prompts or ordinary logs; the database only records *that* a secret is set.
* **Scope**: every network request of the three modules goes through ``ScopeGuard.check`` first. Hosts must be
  authorized targets (the API Testing policy: no SkyGuard port, no cloud metadata address, every resolved address
  checked), the URL must match an allowed prefix and no excluded prefix, and a state-changing method needs the target's
  explicit permission. This is deterministic code: the AI never decides what is permitted.
* **Runs**: started in the background, with pause, resume, cancel (safe stop at the next check), checkpoints and
  partial results kept when a run fails. Nothing is retried blindly.
* **Results**: one table for the three modules (status, expected, actual, sanitized evidence, correlation ID), the
  source of the dashboard figures and of the result actions.
"""

from __future__ import annotations

import re
import secrets as _secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from .. import auth
from ..clock import now_iso
from ..errors import QAError
from ..logging_setup import get_logger
from ..storage.db import dumps, loads

log = get_logger("itest")

MODULES = {"security": "Security Testing", "fuse": "Red Hat Fuse Testing", "activemq": "ActiveMQ Testing"}
ENVIRONMENTS = ("Development", "QA", "Test", "UAT", "Staging", "Production")
RESULT_STATUSES = ("PASSED", "FAILED", "BLOCKED", "SKIPPED", "UNVERIFIED", "ERROR")
FAIL_STATUSES = ("FAILED", "ERROR")
RUN_STATUSES = ("QUEUED", "RUNNING", "PAUSED", "CANCELLING", "CANCELLED", "COMPLETED", "FAILED", "INTERRUPTED")
SEVERITIES = ("critical", "high", "medium", "low", "info")
MAX_EVIDENCE_TEXT = 4000
SECRET_KEYS = re.compile(r"pass(word|wd|phrase)?|secret|token|api[-_]?key|apikey|authorization|cookie|session|"
                         r"private[-_]?key|client[-_]?secret|credential|otp|pin\b|cvv|signature", re.I)
SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token",
                  "x-csrf-token", "x-xsrf-token"}
_BEARER = re.compile(r"(?i)\b(bearer|basic|digest)\s+[A-Za-z0-9._~+/=-]{6,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\b")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_KV = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)"
                 r"(\s*[=:]\s*|\"\s*:\s*\")([^\s\"&,;]{2,})")
ID_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_-]|\.(?!\.)){0,79}$")  # also a safe file / folder name


def new_id(prefix: str) -> str:
    return f"{prefix}-{_secrets.token_hex(4).upper()}"


def user_name() -> str:
    return (auth.current_user() or auth.LOCAL_PROGRAM).username


def actor(what: str) -> str:
    """The signed-in person, when that account may change things (view-only accounts may look and run only)."""
    user = auth.current_user() or auth.LOCAL_PROGRAM
    if user.role in auth.VIEW_ROLES:
        raise QAError("ITEST_NOT_ALLOWED", f"Your account ({auth.ROLES.get(user.role, user.role)}) may not {what}.",
                      status=403)
    return user.username


def validate(model: Any, body: Any) -> Any:
    """``model.model_validate(body)`` with the errors as one readable 422 instead of a server error."""
    from pydantic import ValidationError

    try:
        return model.model_validate(body)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        raise QAError("VALIDATION_FAILED", "The test definition is not valid: " + "; ".join(parts), status=422) from None


def check_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise QAError("VALIDATION_FAILED", f"Invalid {what}.", status=422)
    return value


# ---- redaction -------------------------------------------------------------------------------------------------------

class Sanitizer:
    """Removes credentials and tokens from anything that is stored as evidence, shown, reported, emailed or given to
    the AI. Known secret values (the target's own passwords and tokens) are replaced wherever they appear."""

    def __init__(self, values: list[str] | None = None) -> None:
        self.values = sorted({str(v) for v in (values or []) if v and len(str(v)) >= 3}, key=len, reverse=True)

    def text(self, value: Any, limit: int = MAX_EVIDENCE_TEXT) -> Any:
        if not isinstance(value, str):
            return value
        out = value
        for v in self.values:
            out = out.replace(v, "[redacted]")
        out = _PEM.sub("[redacted private key]", out)
        out = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", out)
        out = _JWT.sub("[redacted token]", out)
        out = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
        if limit and len(out) > limit:
            out = out[:limit] + f"… [{len(value) - limit} more characters not kept]"
        return out

    def headers(self, headers: dict[str, Any] | None) -> dict[str, Any]:
        return {str(k): ("[redacted]" if str(k).lower() in SECRET_HEADERS or SECRET_KEYS.search(str(k)) else
                         self.text(str(v), 600)) for k, v in (headers or {}).items()}

    def data(self, value: Any, depth: int = 0) -> Any:
        if depth > 25:
            return "[too deep]"
        if isinstance(value, dict):
            return {str(k): ("[redacted]" if SECRET_KEYS.search(str(k)) and value[k] not in (None, "", [], {})
                             else self.data(v, depth + 1)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.data(v, depth + 1) for v in list(value)[:200]]
        return self.text(value)


SANITIZER = Sanitizer()


# ---- scope -----------------------------------------------------------------------------------------------------------

class ScopeGuard:
    """Where a module may send requests for one target. ``allowed`` and ``excluded`` are URL prefixes (or regular
    expressions written as ``re:<pattern>``); hosts must be authorized targets (API Testing policy)."""

    def __init__(self, targets: list[str], allowed: list[str] | None = None, excluded: list[str] | None = None, *,
                 own_port: int = 8765, allow_state_changing: bool = False, resolver: Any = None) -> None:
        from ..apitesting.safety import Policy

        self.policy = Policy(targets, own_port=own_port, resolver=resolver)
        self.allowed = [a.strip() for a in (allowed or []) if a and a.strip()]
        self.excluded = [a.strip() for a in (excluded or []) if a and a.strip()]
        self.allow_state_changing = bool(allow_state_changing)

    @staticmethod
    def _match(rule: str, url: str) -> bool:
        if rule.startswith("re:"):
            try:
                return re.search(rule[3:], url) is not None
            except re.error:
                return False
        return url.lower().startswith(rule.lower())

    def in_scope(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return False, "only http and https are tested"
        if any(self._match(x, url) for x in self.excluded):
            return False, "the URL is excluded from the approved scope"
        if self.allowed and not any(self._match(a, url) for a in self.allowed):
            return False, "the URL is outside the allowed URLs of the approved scope"
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if not self.policy.authorized(host, port):
            return False, f"{host}:{port} is not an authorized target"
        return True, ""

    def check(self, url: str, method: str = "GET") -> dict[str, Any]:
        ok, why = self.in_scope(url)
        if not ok:
            raise QAError("ITEST_OUT_OF_SCOPE", f"Refused {method.upper()} {url}: {why}.", status=403,
                          details={"url": url, "reason": why})
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE") and not self.allow_state_changing:
            raise QAError("ITEST_STATE_CHANGE_REFUSED", f"{method.upper()} {url} could change data, and this target "
                          "does not allow state-changing tests.", status=403)
        return self.policy.check_url(url)


class Throttle:
    """At most ``rate`` requests per second and ``budget`` requests in total for one run, with a time limit."""

    def __init__(self, rate: float = 5.0, budget: int = 500, seconds: float = 600.0) -> None:
        self.gap = 1.0 / rate if rate and rate > 0 else 0.0
        self.budget = max(1, int(budget))
        self.deadline = time.monotonic() + max(5.0, float(seconds))
        self.used = 0
        self._next = 0.0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.budget:
                raise QAError("ITEST_BUDGET", f"The request budget of this run ({self.budget}) is used up.", status=409)
            if time.monotonic() > self.deadline:
                raise QAError("ITEST_TIME_LIMIT", "The time limit of this run is reached.", status=409)
            self.used += 1
            now = time.monotonic()
            at = max(now, self._next)
            self._next = at + self.gap
        if at > now:
            time.sleep(min(at - now, 5.0))


# ---- run control -----------------------------------------------------------------------------------------------------

class Cancelled(Exception):
    """The person cancelled the run; it stops at the next safe point."""


class RunControl:
    """Pause, resume and cancel for background runs. A run calls ``point()`` between two checks: it waits while
    paused and stops (raising ``Cancelled``) when cancelled."""

    def __init__(self) -> None:
        self._pause: dict[str, threading.Event] = {}
        self._cancel: set[str] = set()
        self._lock = threading.Lock()

    def register(self, run_id: str) -> None:
        with self._lock:
            ev = threading.Event()
            ev.set()
            self._pause[run_id] = ev

    def done(self, run_id: str) -> None:
        with self._lock:
            self._pause.pop(run_id, None)
            self._cancel.discard(run_id)

    def active(self, run_id: str) -> bool:
        return run_id in self._pause

    def pause(self, run_id: str) -> bool:
        ev = self._pause.get(run_id)
        if ev is None:
            return False
        ev.clear()
        return True

    def resume(self, run_id: str) -> bool:
        ev = self._pause.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    def cancel(self, run_id: str) -> bool:
        if run_id not in self._pause:
            return False
        self._cancel.add(run_id)
        self._pause[run_id].set()
        return True

    def point(self, run_id: str) -> None:
        ev = self._pause.get(run_id)
        if ev is not None:
            while not ev.wait(0.5):
                if run_id in self._cancel:
                    break
        if run_id in self._cancel:
            raise Cancelled()


CONTROL = RunControl()


# ---- store -----------------------------------------------------------------------------------------------------------

class ItestStore:
    """Targets, runs, results, cases and the audit trail of the three modules (migration 23)."""

    def __init__(self, db: Any, secrets: Any) -> None:
        self.db = db
        self.secrets = secrets

    # audit
    def audit(self, module: str, action: str, target: str | None = None, **detail: Any) -> None:
        self.db.execute("INSERT INTO itest_audit (at, by, module, action, target, detail) VALUES (?,?,?,?,?,?)",
                        (now_iso(), user_name(), module, action, target, dumps(SANITIZER.data(detail))))

    def audit_log(self, module: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM itest_audit" + (" WHERE module = ?" if module else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.db.query(q, ((module,) if module else ()) + (int(limit),))
        for r in rows:
            r["detail"] = loads(r["detail"])
        return rows

    # secrets
    @staticmethod
    def _ns(module: str, target_id: str) -> str:
        return f"itest.{module}.{target_id}"

    def set_secret(self, module: str, target_id: str, name: str, value: str | None) -> None:
        if not re.match(r"^[A-Za-z0-9_.-]{1,60}$", name or ""):
            raise QAError("VALIDATION_FAILED", "Invalid secret name.", status=422)
        if value:
            self.secrets.set(self._ns(module, target_id), name, value)
        else:
            self.secrets.delete(self._ns(module, target_id), name)

    def secret(self, module: str, target_id: str, name: str) -> str | None:
        try:
            return self.secrets.get(self._ns(module, target_id), name)
        except Exception:  # noqa: BLE001 - a vault that cannot be read means "not set"
            return None

    def secret_values(self, module: str, target_id: str, names: list[str]) -> list[str]:
        return [v for v in (self.secret(module, target_id, n) for n in names) if v]

    # targets
    def save_target(self, module: str, name: str, environment: str, config: dict[str, Any],
                    target_id: str | None = None) -> dict[str, Any]:
        if module not in MODULES:
            raise QAError("VALIDATION_FAILED", "Unknown module.", status=422)
        name = (name or "").strip()[:120]
        if len(name) < 2:
            raise QAError("VALIDATION_FAILED", "Give the target a name of at least 2 characters.", status=422)
        if environment not in ENVIRONMENTS:
            raise QAError("VALIDATION_FAILED", f"The environment must be one of: {', '.join(ENVIRONMENTS)}.", status=422)
        bad = [k for k, v in _walk(config) if SECRET_KEYS.search(k) and isinstance(v, str) and v and k not in
               ("secret_names", "token_header", "auth_header")]
        if bad:
            raise QAError("ITEST_SECRET_IN_CONFIG", "Passwords, tokens and keys are saved in the credential vault, not "
                          f"in the configuration ({', '.join(sorted(set(bad))[:4])}).", status=422)
        ts = now_iso()
        by = actor("change test targets")
        if target_id:
            row = self.target(module, target_id)
            self.db.execute("UPDATE itest_targets SET name = ?, environment = ?, config = ?, updated_at = ? WHERE id = ?",
                            (name, environment, dumps(config), ts, row["id"]))
            self.audit(module, "target.updated", target_id, name=name)
            return self.target(module, target_id)
        target_id = new_id({"security": "SAPP", "fuse": "FUSE", "activemq": "BRK"}[module])
        self.db.execute("INSERT INTO itest_targets (id, module, name, environment, config, created_by, created_at, "
                        "updated_at) VALUES (?,?,?,?,?,?,?,?)", (target_id, module, name, environment, dumps(config), by,
                                                                 ts, ts))
        self.audit(module, "target.created", target_id, name=name, environment=environment)
        return self.target(module, target_id)

    def target(self, module: str, target_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM itest_targets WHERE id = ? AND module = ?", (check_id(target_id), module))
        if not row:
            raise QAError("ITEST_TARGET_NOT_FOUND", f"{MODULES.get(module, module)} target {target_id} does not exist.",
                          status=404)
        row["config"] = loads(row["config"])
        return row

    def targets(self, module: str) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM itest_targets WHERE module = ? ORDER BY name", (module,))
        for r in rows:
            r["config"] = loads(r["config"])
        return rows

    def delete_target(self, module: str, target_id: str, secret_names: list[str]) -> None:
        actor("delete test targets")
        self.target(module, target_id)
        for n in secret_names:
            try:
                self.set_secret(module, target_id, n, None)
            except Exception:  # noqa: BLE001
                pass
        self.db.execute("DELETE FROM itest_targets WHERE id = ?", (target_id,))
        self.audit(module, "target.deleted", target_id)

    # runs
    def create_run(self, module: str, target_id: str | None, kind: str, label: str, config: dict[str, Any]) -> str:
        run_id = new_id({"security": "SEC", "fuse": "FUSE-RUN", "activemq": "AMQ-RUN"}[module])
        self.db.execute("INSERT INTO itest_runs (id, module, target_id, kind, label, status, counts, config, started_by, "
                        "started_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, module, target_id, kind, label[:200], "QUEUED", dumps({}), dumps(SANITIZER.data(config)),
                         user_name(), now_iso()))
        return run_id

    def set_run(self, run_id: str, **values: Any) -> None:
        cols = []
        params: list[Any] = []
        for k, v in values.items():
            if k not in ("status", "counts", "checkpoint", "summary", "finished_at"):
                continue
            cols.append(f"{k} = ?")
            params.append(dumps(v) if isinstance(v, (dict, list)) else v)
        if cols:
            self.db.execute(f"UPDATE itest_runs SET {', '.join(cols)} WHERE id = ?", (*params, run_id))

    def run(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM itest_runs WHERE id = ?", (check_id(run_id, "run id"),))
        if not row:
            raise QAError("ITEST_RUN_NOT_FOUND", f"Run {run_id} does not exist.", status=404)
        for k in ("counts", "config", "checkpoint", "summary"):
            row[k] = loads(row[k]) if row.get(k) else ({} if k != "summary" else None)
        row["active"] = CONTROL.active(row["id"])
        return row

    def runs(self, module: str, target_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        q = "SELECT id FROM itest_runs WHERE module = ?" + (" AND target_id = ?" if target_id else "") + \
            " ORDER BY started_at DESC LIMIT ?"
        return [self.run(r["id"]) for r in self.db.query(q, (module, *((target_id,) if target_id else ()), int(limit)))]

    def mark_interrupted(self) -> int:
        cur = self.db.execute("UPDATE itest_runs SET status = 'INTERRUPTED', finished_at = ? WHERE status IN "
                              "('QUEUED','RUNNING','PAUSED','CANCELLING')", (now_iso(),))
        return cur.rowcount or 0

    # results
    def add_result(self, run_id: str, module: str, target_id: str | None, test_name: str, status: str, *,
                   category: str = "", severity: str | None = None, expected: Any = None, actual: Any = None,
                   evidence: Any = None, correlation_id: str | None = None, finding_id: str | None = None,
                   sanitizer: Sanitizer | None = None) -> int:
        if status not in RESULT_STATUSES:
            status = "UNVERIFIED"
        s = sanitizer or SANITIZER
        cur = self.db.execute(
            "INSERT INTO itest_results (run_id, module, target_id, test_name, category, status, severity, expected, "
            "actual, evidence, correlation_id, finding_id, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, module, target_id, test_name[:250], category[:80], status, severity,
             dumps(s.data(expected)) if expected is not None else None,
             dumps(s.data(actual)) if actual is not None else None, dumps(s.data(evidence or {})),
             (correlation_id or "")[:120] or None, finding_id, now_iso()))
        return int(cur.lastrowid)

    def result(self, result_id: int) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM itest_results WHERE id = ?", (int(result_id),))
        if not row:
            raise QAError("ITEST_RESULT_NOT_FOUND", f"Result {result_id} does not exist.", status=404)
        return _result_row(row)

    def results(self, *, module: str | None = None, run_id: str | None = None, status: str | None = None,
                category: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        where, params = [], []
        for col, val in (("module", module), ("run_id", run_id), ("category", category)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if status:
            st = [s for s in status.upper().split(",") if s in RESULT_STATUSES]
            if st:
                where.append(f"status IN ({','.join('?' * len(st))})")
                params += st
        q = "SELECT * FROM itest_results" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
        return [_result_row(r) for r in self.db.query(q, (*params, int(limit)))]

    def counts(self, run_id: str) -> dict[str, int]:
        rows = self.db.query("SELECT status, COUNT(*) AS n FROM itest_results WHERE run_id = ? GROUP BY status", (run_id,))
        out = {s: 0 for s in RESULT_STATUSES}
        for r in rows:
            out[r["status"]] = r["n"]
        out["total"] = sum(out[s] for s in RESULT_STATUSES)
        return out

    # cases (Fuse and ActiveMQ test designs: Generate -> Review -> Approve -> Execute)
    def save_case(self, module: str, target_id: str, name: str, definition: dict[str, Any],
                  case_id: str | None = None, origin: str = "manual") -> dict[str, Any]:
        by = actor("change test cases")
        ts = now_iso()
        name = (name or "").strip()[:200] or "Unnamed test"
        if case_id:
            self.case(case_id)
            # an edited case has to be approved again before it runs
            self.db.execute("UPDATE itest_cases SET name = ?, definition = ?, status = 'draft', approved_by = NULL, "
                            "updated_at = ? WHERE id = ?", (name, dumps(definition), ts, case_id))
        else:
            case_id = new_id("TC")
            self.db.execute("INSERT INTO itest_cases (id, module, target_id, name, definition, status, origin, created_by,"
                            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (case_id, module, target_id, name, dumps(definition), "draft", origin, by, ts, ts))
        self.audit(module, "case.saved", case_id, name=name, origin=origin)
        return self.case(case_id)

    def approve_case(self, case_id: str) -> dict[str, Any]:
        by = actor("approve test cases")
        c = self.case(case_id)
        self.db.execute("UPDATE itest_cases SET status = 'approved', approved_by = ?, updated_at = ? WHERE id = ?",
                        (by, now_iso(), case_id))
        self.audit(c["module"], "case.approved", case_id)
        return self.case(case_id)

    def case(self, case_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM itest_cases WHERE id = ?", (check_id(case_id, "test case id"),))
        if not row:
            raise QAError("ITEST_CASE_NOT_FOUND", f"Test case {case_id} does not exist.", status=404)
        row["definition"] = loads(row["definition"])
        return row

    def cases(self, module: str, target_id: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM itest_cases WHERE module = ?" + (" AND target_id = ?" if target_id else "") + " ORDER BY name"
        rows = self.db.query(q, (module, *((target_id,) if target_id else ())))
        for r in rows:
            r["definition"] = loads(r["definition"])
        return rows

    def delete_case(self, case_id: str) -> None:
        actor("delete test cases")
        c = self.case(case_id)
        self.db.execute("DELETE FROM itest_cases WHERE id = ?", (case_id,))
        self.audit(c["module"], "case.deleted", case_id)


def _result_row(row: dict[str, Any]) -> dict[str, Any]:
    for k in ("expected", "actual", "evidence"):
        row[k] = loads(row[k]) if row.get(k) else None
    return row


def _walk(value: Any, key: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append((str(k), v))
            out += _walk(v, str(k))
    elif isinstance(value, list):
        for v in value:
            out += _walk(v, key)
    return out


def run_in_background(*, store: ItestStore, agent: Any, run_id: str, module: str, description: str,
                      work: Callable[[Any], dict[str, Any] | None]) -> None:
    """Runs ``work(activity)`` in a thread with the agent card, pause / resume / cancel and the final status. The
    results already recorded are kept whatever happens (partial results)."""
    CONTROL.register(run_id)

    def go() -> None:
        store.set_run(run_id, status="RUNNING")
        status, summary = "COMPLETED", None
        try:
            with agent.activity(description, run_id=run_id) as act:
                try:
                    summary = work(act) or {}
                except Cancelled:
                    status, summary = "CANCELLED", {"note": "Cancelled by a person; the results so far are kept."}
                counts = store.counts(run_id)
                act.complete(f"{agent.name}: {counts['PASSED']} passed, {counts['FAILED'] + counts['ERROR']} failed, "
                             f"{counts['BLOCKED']} blocked" + (" (cancelled)" if status == "CANCELLED" else ""),
                             counts=counts)
        except Exception as exc:  # noqa: BLE001 - the run is marked FAILED with the reason; partial results stay
            status = "FAILED"
            summary = {"error": SANITIZER.text(str(getattr(exc, "message", exc)), 600)}
            log.warning(f"{module} run {run_id} failed: {exc}")
        finally:
            store.set_run(run_id, status=status, counts=store.counts(run_id), summary=summary, finished_at=now_iso())
            CONTROL.done(run_id)
            store.audit(module, "run.finished", run_id, status=status)

    threading.Thread(target=go, name=f"itest-{run_id}", daemon=True).start()


__all__ = ["MODULES", "ENVIRONMENTS", "ScopeGuard", "Throttle", "Sanitizer", "SANITIZER", "ItestStore", "CONTROL",
           "Cancelled", "run_in_background", "new_id", "actor", "user_name", "check_id"]
