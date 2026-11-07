"""23.3.21: Scheduled Runs.

A schedule plays one or more scripts (AI-Driven framework scripts FWS-..., recorded / hybrid / single AI scripts
SCR-...) once at a future date and time, or again and again (every day, weekdays, chosen days) until an optional
end date. Each batch can run in several browsers, several runs at the same time, all data rows, writes the outcomes
to TFS test runs and emails an HTML report through Outlook. Times are the PC's local time.

Schedules and their history are kept in <data>/schedules.json. A background thread checks every 15 seconds; the
application must be running. A run missed while the application was closed starts once when it opens again
(catch_up, on by default) and the schedule then continues from the next time.
"""

from __future__ import annotations

import html
import json
import secrets as _secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .. import branding
from ..agents.base import new_run_id
from ..errors import QAError
from ..events import names
from ..logging_setup import get_logger

log = get_logger("scheduler")

REPEATS = ("once", "daily", "weekdays", "weekly", "hourly")
BROWSERS = ("chromium", "msedge", "chrome")  # 23.3.32: Firefox is not included
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HISTORY_KEEP = 300


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)  # the PC's local time, like the person's clock


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hh, mm = str(value).strip().split(":")[:2]
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise QAError("SCHEDULE_TIME", "Time must be HH:MM, for example 07:30.", status=422) from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise QAError("SCHEDULE_TIME", "Time must be HH:MM between 00:00 and 23:59.", status=422)
    return h, m


def _parse_date(value: Any, what: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise QAError("SCHEDULE_DATE", f"{what} must be a date (YYYY-MM-DD).", status=422) from None


def next_run(s: dict[str, Any], after: datetime) -> datetime | None:
    """The first time strictly after ``after`` the schedule runs, or None when it has ended."""
    h, m = _parse_time(s.get("time") or "00:00")
    start = _parse_date(s.get("start_date"), "Start date") or after.date()
    end = _parse_date(s.get("end_date"), "End date")
    repeat = s.get("repeat") or "once"
    if repeat == "once":
        at = datetime.combine(start, datetime.min.time()).replace(hour=h, minute=m)
        return at if at > after else None
    if repeat == "hourly":
        every = max(1, int(s.get("every_hours") or 1))
        first = datetime.combine(start, datetime.min.time()).replace(hour=h, minute=m)
        if first > after:
            at = first
        else:
            steps = int((after - first).total_seconds() // (every * 3600)) + 1
            at = first + timedelta(hours=every * steps)
        return None if end and at.date() > end else at
    days = {0, 1, 2, 3, 4} if repeat == "weekdays" else set(range(7)) if repeat == "daily" else \
        {int(d) for d in (s.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6}
    if not days:
        return None
    d = max(start, after.date())
    for _ in range(0, 370):
        at = datetime.combine(d, datetime.min.time()).replace(hour=h, minute=m)
        if end and d > end:
            return None
        if d.weekday() in days and at > after:
            return at
        d += timedelta(days=1)
    return None


def describe(s: dict[str, Any]) -> str:
    repeat = s.get("repeat") or "once"
    t = s.get("time") or "00:00"
    if repeat == "once":
        return f"Once on {s.get('start_date')} at {t}"
    if repeat == "hourly":
        every = int(s.get("every_hours") or 1)
        return f"Every {every} hour{'s' if every != 1 else ''} from {t}" + (f" until {s['end_date']}" if s.get("end_date") else "")
    when = {"daily": "Every day", "weekdays": "Monday to Friday"}.get(
        repeat, "Every " + ", ".join(DAY_NAMES[int(d)] for d in sorted(s.get("days") or [])))
    return f"{when} at {t}" + (f" until {s['end_date']}" if s.get("end_date") else " (continues)")


class ScheduleStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("schedules", [])
                data.setdefault("history", [])
                return data
        except (OSError, ValueError):
            pass
        return {"schedules": [], "history": []}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def read(self) -> dict[str, Any]:
        with self._lock:
            return self._load()

    def change(self, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            data = self._load()
            out = fn(data)
            self._save(data)
            return out


class Scheduler:
    def __init__(self, automation: Any, path: Path, *, tfs: Any = None, mailer: Any = None,
                 reports_dir: Path | None = None, clock: Callable[[], datetime] = _now) -> None:
        self.automation = automation
        self.studio: Any = None  # set by the runtime (AI-Driven framework scripts)
        self.tfs = tfs
        self.mailer = mailer
        self.store = ScheduleStore(path)
        self.reports_dir = reports_dir or path.parent / "schedule-reports"
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active: dict[str, dict[str, Any]] = {}  # batch id -> live progress
        self._running_schedules: set[str] = set()
        self._lock = threading.Lock()

    # ---- background loop ---------------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._catch_up()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(15):
            try:
                self.tick()
            except Exception as exc:  # the loop must keep going
                log.warning(f"Scheduler tick failed: {exc}")

    def _catch_up(self) -> None:
        """When the application starts: a run missed while it was closed starts once (catch_up), then the schedule
        continues from its next time."""
        now = self.clock()
        due: list[str] = []

        def fix(data: dict[str, Any]) -> None:
            for s in data["schedules"]:
                if not s.get("enabled", True):
                    continue
                nxt = s.get("next_run_at")
                if nxt and datetime.fromisoformat(nxt) <= now - timedelta(minutes=1):
                    if s.get("catch_up", True):
                        due.append(s["id"])
                        s["missed_at"] = nxt
                    n = next_run(s, now)
                    s["next_run_at"] = n.isoformat() if n else None
                    if n is None and s.get("repeat") == "once" and not s.get("catch_up", True):
                        s["enabled"] = False

        self.store.change(fix)
        for sid in due:
            self.run_now(sid, trigger="missed")

    def tick(self) -> list[str]:
        now = self.clock()
        due: list[str] = []

        def pick(data: dict[str, Any]) -> None:
            for s in data["schedules"]:
                if not s.get("enabled", True) or not s.get("next_run_at"):
                    continue
                if datetime.fromisoformat(s["next_run_at"]) <= now:
                    due.append(s["id"])
                    n = next_run(s, now)
                    s["next_run_at"] = n.isoformat() if n else None
                    if n is None:  # a one-time schedule, or past its end date
                        s["enabled"] = False
                        s["finished"] = True

        self.store.change(pick)
        started = []
        for sid in due:
            try:
                started.append(self.run_now(sid, trigger="schedule"))
            except QAError as exc:
                log.warning(f"Schedule {sid} could not start: {exc.message}")
        return started

    # ---- schedules -------------------------------------------------------------------------------

    def _validate(self, body: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        s = {**(current or {}), **{k: v for k, v in body.items() if v is not None}}
        ids = [str(x) for x in s.get("script_ids") or [] if str(x).strip()]
        if not ids:
            raise QAError("SCHEDULE_SCRIPTS", "Choose at least one script.", status=422)
        for sid in ids:
            self.script_info(sid)  # exists?
        s["script_ids"] = list(dict.fromkeys(ids))
        if s.get("repeat", "once") not in REPEATS:
            raise QAError("SCHEDULE_REPEAT", f"Repeat must be one of {', '.join(REPEATS)}.", status=422)
        s["repeat"] = s.get("repeat") or "once"
        _parse_time(s.get("time") or "")
        s["start_date"] = (_parse_date(s.get("start_date"), "Start date") or self.clock().date()).isoformat()
        end = _parse_date(s.get("end_date"), "End date")
        if end and end < date.fromisoformat(s["start_date"]):
            raise QAError("SCHEDULE_DATE", "The end date is before the start date.", status=422)
        s["end_date"] = end.isoformat() if end else None
        if s["repeat"] == "weekly":
            days = sorted({int(d) for d in s.get("days") or [] if str(d).lstrip("-").isdigit() and 0 <= int(d) <= 6})
            if not days:
                raise QAError("SCHEDULE_DAYS", "Choose at least one day of the week.", status=422)
            s["days"] = days
        browsers = [b for b in (s.get("browsers") or ["chromium"]) if b in BROWSERS]
        if not browsers:
            raise QAError("SCHEDULE_BROWSER", f"Browsers can be {', '.join(BROWSERS)}.", status=422)
        s["browsers"] = list(dict.fromkeys(browsers))
        s["parallel"] = max(1, min(8, int(s.get("parallel") or 1)))
        s["every_hours"] = max(1, min(24, int(s.get("every_hours") or 1)))
        s["data_rows"] = s.get("data_rows") if s.get("data_rows") in ("first", "all") else "first"
        s["headless"] = bool(s.get("headless", False))
        s["catch_up"] = bool(s.get("catch_up", True))
        s["publish_tfs"] = bool(s.get("publish_tfs", False))
        email = dict(s.get("email") or {})
        email["enabled"] = bool(email.get("enabled"))
        email["on"] = email.get("on") if email.get("on") in ("always", "failures") else "always"
        if email["enabled"]:
            from ..mailer import split_addresses

            if not split_addresses(email.get("to")):
                raise QAError("SCHEDULE_EMAIL", "Add the email address(es) the report goes to.", status=422)
        s["email"] = email
        s["name"] = (str(s.get("name") or "").strip() or self.script_info(ids[0])["name"])[:120]
        s["enabled"] = bool(s.get("enabled", True))
        return s

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        s = self._validate(body)
        s.update(id="SCH-" + _secrets.token_hex(4).upper(), created_at=self.clock().isoformat(), last_run_at=None,
                 last_status=None, last_batch=None)
        n = next_run(s, self.clock() - timedelta(seconds=1))
        if n is None:
            raise QAError("SCHEDULE_PAST", "That date and time has already passed. Choose a future time.", status=422)
        s["next_run_at"] = n.isoformat()
        self.store.change(lambda d: d["schedules"].append(s))
        return self._view(s)

    def update(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        def upd(data: dict[str, Any]) -> dict[str, Any]:
            cur = self._find(data, sid)
            s = self._validate(body, cur)
            if s.get("enabled", True):
                n = next_run(s, self.clock() - timedelta(seconds=1))
                if n is None and ("time" in body or "start_date" in body or "repeat" in body or body.get("enabled")):
                    raise QAError("SCHEDULE_PAST", "That date and time has already passed. Choose a future time.",
                                  status=422)
                s["next_run_at"] = n.isoformat() if n else None
                s["finished"] = n is None
            cur.clear()
            cur.update(s)
            return dict(cur)

        return self._view(self.store.change(upd))

    def delete(self, sid: str) -> None:
        def rm(data: dict[str, Any]) -> None:
            self._find(data, sid)
            data["schedules"] = [s for s in data["schedules"] if s["id"] != sid]

        self.store.change(rm)

    @staticmethod
    def _find(data: dict[str, Any], sid: str) -> dict[str, Any]:
        for s in data["schedules"]:
            if s["id"] == sid:
                return s
        raise QAError("SCHEDULE_NOT_FOUND", f"Schedule {sid} does not exist.", status=404)

    def get(self, sid: str) -> dict[str, Any]:
        return self._view(self._find(self.store.read(), sid))

    def list(self) -> list[dict[str, Any]]:
        return [self._view(s) for s in self.store.read()["schedules"]]

    def history(self, sid: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        items = self.store.read()["history"]
        if sid:
            items = [h for h in items if h.get("schedule_id") == sid]
        live = [dict(b) for b in self.active.values() if not sid or b.get("schedule_id") == sid]
        return (live + list(reversed(items)))[:limit]

    def _view(self, s: dict[str, Any]) -> dict[str, Any]:
        out = dict(s)
        out["when"] = describe(s)
        out["running"] = s["id"] in self._running_schedules
        names_ = []
        for sid in s.get("script_ids") or []:
            try:
                names_.append(self.script_info(sid)["name"])
            except QAError:
                names_.append(f"{sid} (deleted)")
        out["script_names"] = names_
        return out

    # ---- scripts -------------------------------------------------------------------------------

    def script_info(self, sid: str) -> dict[str, Any]:
        if sid.startswith("FWS-"):
            if self.studio is None:
                raise QAError("SCRIPT_NOT_FOUND", f"Script {sid} does not exist.", status=404)
            rec = self.studio._record(sid)
            name = f"TC{rec['test_case_id']} {rec.get('test_case_title') or ''}".strip() if rec.get("test_case_id") \
                else rec.get("test_case_title") or sid
            return {"id": sid, "name": name, "kind": "ai", "page": "AI-Driven Automation",
                    "test_case_id": rec.get("test_case_id"), "application": rec.get("application"), "rows": 1}
        s = self.automation.scripts.get(sid)
        page = {"ai": "AI-Driven Automation", "guided": "Guided Browser Automation", "manual": "Guided Browser Automation",
                "hybrid": "Hybrid Test Automation", "discovery": "Application Discovery & Update"}.get(s.mode, s.mode)
        rows = len(s.data_table.rows) if s.data_table is not None and s.data_table.rows else 0
        return {"id": sid, "name": s.name, "kind": s.mode, "page": page, "test_case_id": s.test_case_id,
                "application": s.application, "rows": rows}

    def scripts(self) -> list[dict[str, Any]]:
        """Every script a schedule can play, for the filterable list."""
        out = []
        if self.studio is not None:
            try:
                for rec in self.studio.inventory():
                    if not rec.get("framework"):
                        continue  # recorded / hybrid scripts are listed below
                    tc = rec.get("test_case_id")
                    out.append({"id": rec["id"], "name": (f"TC{tc} " if tc else "") + (rec.get("test_case_title") or rec["id"]),
                                "kind": "ai", "page": "AI-Driven Automation", "test_case_id": tc,
                                "application": rec.get("application"), "steps": rec.get("steps"),
                                "last_result": rec.get("last_result"), "rows": 1})
            except Exception as exc:  # the framework workspace may not exist yet
                log.info(f"AI-Driven scripts not listed: {exc}")
        for s in self.automation.scripts.list():
            sid = s["id"] if isinstance(s, dict) else s.id
            try:
                info = self.script_info(sid)
            except QAError:
                continue
            info["steps"] = s.get("steps") if isinstance(s, dict) else len(s.steps)
            out.append(info)
        planned: dict[str, list[str]] = {}
        for sch in self.store.read()["schedules"]:
            for sid in sch.get("script_ids") or []:
                planned.setdefault(sid, []).append(sch["id"])
        for item in out:
            item["schedules"] = planned.get(item["id"], [])
        return out

    # ---- running -------------------------------------------------------------------------------

    def run_now(self, sid: str, *, trigger: str = "manual") -> str:
        s = self._find(self.store.read(), sid)
        if sid in self._running_schedules:
            raise QAError("SCHEDULE_RUNNING", "This schedule is already running.", status=409)
        return self.run_batch(s, trigger=trigger)

    def run_batch(self, s: dict[str, Any], *, trigger: str = "manual", wait: bool = False) -> str:
        """Run a schedule's scripts now (also used by Run now with ad hoc settings, s without id)."""
        batch_id = new_run_id("SCHRUN")
        items: list[dict[str, Any]] = []
        for script_id in s.get("script_ids") or []:
            try:
                info = self.script_info(script_id)
            except QAError as exc:
                items.append({"script_id": script_id, "name": script_id, "browser": None, "status": "ERROR",
                              "error": exc.message})
                continue
            rows: list[int | None] = [None]
            if info["kind"] != "ai" and info["rows"]:
                rows = list(range(info["rows"])) if s.get("data_rows") == "all" else [0]
            for browser in s.get("browsers") or ["chromium"]:
                for row in rows:
                    items.append({"script_id": script_id, "name": info["name"], "browser": browser, "row": row,
                                  "rows": info["rows"], "test_case_id": info.get("test_case_id"), "status": "QUEUED",
                                  "run_id": new_run_id("RUN")})
        batch = {"batch_id": batch_id, "schedule_id": s.get("id"), "name": s.get("name") or "Run now", "trigger": trigger,
                 "started_at": self.clock().isoformat(), "finished_at": None, "status": "RUNNING", "items": items,
                 "parallel": int(s.get("parallel") or 1), "email": None, "tfs": None}
        with self._lock:
            self.active[batch_id] = batch
            if s.get("id"):
                self._running_schedules.add(s["id"])
        agent = self.automation.runner.execution
        agent.emit(names.AGENT_LOG, f"Scheduled run started: {batch['name']} ({len(items)} run(s))", run_id=batch_id,
                   details={"link": "#/scheduled-runs"})

        def one(item: dict[str, Any]) -> None:
            if item["status"] != "QUEUED":
                return
            self.automation._exec_lock.limit_for_thread(batch["parallel"])
            item["status"] = "RUNNING"
            started = time.monotonic()
            try:
                if item["script_id"].startswith("FWS-"):
                    res = self.studio.run(item["script_id"], headless=s.get("headless"), background=False,
                                          browser=item["browser"], run_id=item["run_id"])
                else:
                    dataset = label = None
                    if item.get("row") is not None:
                        table = self.automation.scripts.get(item["script_id"]).data_table
                        dataset = dict(table.rows[item["row"]])
                        label = f"row {item['row'] + 1} of {item['rows']}"
                    res = self.automation.run_script(item["script_id"], headless=s.get("headless"), background=False,
                                                     browser=item["browser"], dataset=dataset, data_label=label,
                                                     run_id=item["run_id"])
                item["status"] = str(res.get("status") or "FAILED")
                item["error"] = res.get("error")
                fs = res.get("failed_step")
                item["failed_step"] = fs + 1 if isinstance(fs, int) else fs
                item["started_at"], item["finished_at"] = res.get("started_at"), res.get("finished_at")
            except QAError as exc:
                item["status"], item["error"] = "ERROR", exc.message
            except Exception as exc:  # one broken run must not stop the batch
                item["status"], item["error"] = "ERROR", str(exc).splitlines()[0][:300] if str(exc) else repr(exc)
            finally:
                item["duration_ms"] = int((time.monotonic() - started) * 1000)
                self.automation._exec_lock.limit_for_thread(None)

        def work() -> None:
            try:
                with ThreadPoolExecutor(max_workers=batch["parallel"], thread_name_prefix=batch_id) as pool:
                    list(pool.map(one, items))
                counts: dict[str, int] = {}
                for it in items:
                    counts[it["status"]] = counts.get(it["status"], 0) + 1
                batch["counts"] = counts
                batch["status"] = "PASSED" if items and all(i["status"] == "PASSED" for i in items) else "FAILED"
                batch["finished_at"] = self.clock().isoformat()
                if s.get("publish_tfs") and self.tfs is not None:
                    batch["tfs"] = self._publish(batch)
                email = s.get("email") or {}
                if email.get("enabled") and self.mailer is not None and (email.get("on") != "failures"
                                                                          or batch["status"] != "PASSED"):
                    batch["email"] = self._email(batch, email)
            finally:
                if batch["status"] == "RUNNING":
                    batch["status"] = "ERROR"
                    batch["finished_at"] = self.clock().isoformat()
                self._finish(s, batch)

        if wait:
            work()
        else:
            threading.Thread(target=work, name=batch_id, daemon=True).start()
        return batch_id

    def _finish(self, s: dict[str, Any], batch: dict[str, Any]) -> None:
        def save(data: dict[str, Any]) -> None:
            data["history"].append(batch)
            data["history"] = data["history"][-HISTORY_KEEP:]
            if s.get("id"):
                for cur in data["schedules"]:
                    if cur["id"] == s["id"]:
                        cur.update(last_run_at=batch["started_at"], last_status=batch["status"],
                                   last_batch=batch["batch_id"])

        self.store.change(save)
        with self._lock:
            self.active.pop(batch["batch_id"], None)
            if s.get("id"):
                self._running_schedules.discard(s["id"])
        agent = self.automation.runner.execution
        c = batch.get("counts") or {}
        summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(c.items())) or "nothing ran"
        event = names.EXECUTION_COMPLETED if batch["status"] == "PASSED" else names.EXECUTION_FAILED
        agent.emit(event, f"Scheduled run {batch['name']} {batch['status']}: {summary}", run_id=batch["batch_id"],
                   details={"link": "#/scheduled-runs"})

    def _publish(self, batch: dict[str, Any]) -> dict[str, Any]:
        try:
            rep = self.tfs.publish_results(f"{batch['name']} - {batch['started_at'][:16].replace('T', ' ')}",
                                           batch["items"])
            return {"ok": True, **rep}
        except QAError as exc:
            return {"ok": False, "error": exc.message}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    # ---- the report --------------------------------------------------------------------------------

    def report_html(self, batch: dict[str, Any]) -> str:
        colour = {"PASSED": "#127a3d", "FAILED": "#b42318", "ERROR": "#b42318", "STOPPED": "#9a6700"}
        rows = "".join(
            f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(str(i.get('name')))}"
            f"{' (' + html.escape(str(i['row'] + 1)) + ')' if i.get('row') is not None and i.get('rows', 0) > 1 else ''}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(str(i.get('browser') or ''))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:{colour.get(i.get('status'), '#374151')};font-weight:700'>{html.escape(str(i.get('status')))}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{(i.get('duration_ms') or 0) / 1000:.1f} s</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(('Step ' + str(i['failed_step']) + ': ') if i.get('failed_step') else '')}{html.escape(str(i.get('error') or ''))[:400]}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#6b7280'>{html.escape(str(i.get('run_id') or ''))}</td></tr>"
            for i in batch["items"])
        c = batch.get("counts") or {}
        total = len(batch["items"])
        passed = c.get("PASSED", 0)
        tfs = batch.get("tfs") or {}
        tfs_line = ""
        if tfs:
            tfs_line = (f"<p>TFS: {len(tfs.get('runs') or [])} test run(s) written.</p>" if tfs.get("ok")
                        else f"<p style='color:#b42318'>TFS: results not written ({html.escape(str(tfs.get('error')))}).</p>")
        return (f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111827'>"
                f"<h2 style='margin:0 0 6px'>{html.escape(batch['name'])}: "
                f"<span style='color:{colour.get(batch['status'], '#374151')}'>{batch['status']}</span></h2>"
                f"<p style='margin:0 0 12px;color:#4b5563'>{passed} of {total} passed · started "
                f"{html.escape(batch['started_at'].replace('T', ' '))}, finished "
                f"{html.escape(str(batch.get('finished_at') or '').replace('T', ' '))} · "
                f"{'scheduled' if batch.get('trigger') == 'schedule' else 'missed run, started late' if batch.get('trigger') == 'missed' else 'run now'}</p>"
                f"<table style='border-collapse:collapse;width:100%'><thead><tr style='background:#f3f4f6;text-align:left'>"
                f"<th style='padding:6px 10px'>Script</th><th style='padding:6px 10px'>Browser</th>"
                f"<th style='padding:6px 10px'>Result</th><th style='padding:6px 10px'>Time</th>"
                f"<th style='padding:6px 10px'>Failure</th><th style='padding:6px 10px'>Run</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>{tfs_line}"
                f"<p style='color:#6b7280;font-size:12px'>Evidence (screenshots, logs) is in {html.escape(branding.name())} > "
                f"Execution History. Sent by {html.escape(branding.name())}.</p></div>")

    def _email(self, batch: dict[str, Any], email: dict[str, Any]) -> dict[str, Any]:
        body = self.report_html(batch)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report = self.reports_dir / f"{batch['batch_id']}.html"
        report.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(batch['name'])}</title>{body}",
                          encoding="utf-8")
        attachments = [report]
        if email.get("attach_screenshots", True):
            evidence = getattr(self.automation.runner.paths, "evidence", None)
            for it in batch["items"]:
                if it.get("status") in ("FAILED", "ERROR") and evidence is not None and it.get("run_id"):
                    shots = sorted((evidence / it["run_id"]).glob("*failed*.png"))[:1]
                    attachments += shots
        c = batch.get("counts") or {}
        subject = (f"[{batch['status']}] {batch['name']}: {c.get('PASSED', 0)}/{len(batch['items'])} passed "
                   f"({batch['started_at'][:16].replace('T', ' ')})")
        try:
            out = self.mailer.send(email.get("to"), subject, body, cc=email.get("cc"), attachments=attachments[:6])
            return {"ok": True, **out}
        except QAError as exc:
            return {"ok": False, "error": exc.message, "details": exc.details}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
