"""23.3.29: performance schedules.

A schedule runs a saved scenario once at a chosen date and time, every day, on chosen weekdays, every N weeks, or
every N minutes, in the Windows time zone of this PC (or a chosen one). The schedule fixes which versions run:

* latest: the latest saved script, workload and targets; the data set versions are resolved when the run is
  created and that run must use exactly those versions;
* pinned: the script version, data set versions, workload and targets taken when the schedule was saved. A run is
  skipped, with the reason, when a pinned version is no longer there. Unsaved editor drafts never run: only saved
  versions exist on the server.

Every due time is a job with a unique key per schedule (the database refuses a second job for the same time), so
two checks, a restart, or two copies of the application cannot start the same run twice. Overlap: skip (default)
or queue one run behind the active one; load runs on the same environment never run at the same time (this PC is
one load generator and runs one test at a time). Missed times (the application was closed, the PC was off or
asleep) are recorded as missed, never as runs; the missed-run policy is skip or run once (the latest missed time
only, never a backlog). A run longer than the schedule's maximum runtime is stopped and its results are kept.

Schedules run only while the application runs on this PC: closing the browser changes nothing; closing the
application stops a running test (recorded as INTERRUPTED) and no schedule starts until it is started again, which
"Start at Windows sign-in" (qa_platform.startup) can do without administrator rights.
"""

from __future__ import annotations

import re
import secrets as _secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

from .. import branding
from ..clock import parse_iso
from ..errors import QAError
from ..logging_setup import get_logger
from ..storage.db import dumps, loads
from . import workload as wl

log = get_logger("performance.schedules")

REPEATS = ("once", "daily", "days", "weekly", "interval")
OVERLAP = ("skip", "queue")
MISSED = ("skip", "run_once")
VERSIONS = ("latest", "pinned")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ACTIVE = ("QUEUED", "VALIDATING", "RUNNING", "STOPPING")
TICK_S = 15
GRACE_S = 300  # a due time found up to 5 minutes late (a busy or briefly sleeping PC) still runs; later = missed
MISSED_KEEP = 20  # missed times recorded one by one; more are counted in one summary entry
MIN_INTERVAL = 15
MAX_INTERVAL = 7 * 24 * 60


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = parse_iso(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ---- time zones --------------------------------------------------------------------------------------------------

def system_zone_name() -> str:
    """The Windows (host) time zone, as the system names it, with its current offset."""
    now = datetime.now().astimezone()
    off = now.utcoffset() or timedelta(0)
    sign = "+" if off >= timedelta(0) else "-"
    mins = abs(int(off.total_seconds())) // 60
    name = time.tzname[0] if time.tzname and time.tzname[0] else now.tzname() or "local time"
    return f"{name} (UTC{sign}{mins // 60:02d}:{mins % 60:02d})"


# Fixed UTC offsets need no time zone data (Windows has none without the tzdata package); they do not follow
# daylight saving time, which the system time zone and named zones do.
OFFSETS = ["UTC-12:00", "UTC-11:00", "UTC-10:00", "UTC-09:30", "UTC-09:00", "UTC-08:00", "UTC-07:00", "UTC-06:00",
           "UTC-05:00", "UTC-04:00", "UTC-03:30", "UTC-03:00", "UTC-02:00", "UTC-01:00", "UTC", "UTC+01:00",
           "UTC+02:00", "UTC+03:00", "UTC+03:30", "UTC+04:00", "UTC+04:30", "UTC+05:00", "UTC+05:30", "UTC+05:45",
           "UTC+06:00", "UTC+06:30", "UTC+07:00", "UTC+08:00", "UTC+08:45", "UTC+09:00", "UTC+09:30", "UTC+10:00",
           "UTC+10:30", "UTC+11:00", "UTC+12:00", "UTC+12:45", "UTC+13:00", "UTC+14:00"]
_OFFSET = re.compile(r"^UTC([+-])(\d{2}):(\d{2})$")


def zones() -> list[str]:
    """Time zones offered besides the system one: fixed UTC offsets always, and named zones (with daylight saving)
    when this PC has the time zone data."""
    try:
        from zoneinfo import available_timezones

        named = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    except Exception:
        named = []
    return OFFSETS + named


def zone(name: str | None) -> tzinfo | None:
    """None is the system time zone."""
    if not name or name == "system":
        return None
    if name == "UTC":
        return timezone.utc
    m = _OFFSET.match(name)
    if m:
        h, mm = int(m.group(2)), int(m.group(3))
        if h > 14 or mm >= 60:
            raise QAError("SCHEDULE_TIMEZONE", f"{name} is not a valid UTC offset.", status=422)
        off = timedelta(hours=h, minutes=mm) * (1 if m.group(1) == "+" else -1)
        return timezone(off, name)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        raise QAError("SCHEDULE_TIMEZONE", f"The time zone {name} is not known on this PC. Use the system time "
                      "zone or a UTC offset, or install the time zone data.", status=422) from None


def _to_utc(local: datetime, tz: tzinfo | None) -> datetime:
    """A wall-clock time in the schedule's time zone to UTC (the system zone when tz is None)."""
    if tz is None:
        return local.astimezone(timezone.utc)  # a naive datetime is read as the system's local time
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def _wall(utc: datetime, tz: tzinfo | None) -> datetime:
    return (utc.astimezone(tz) if tz else utc.astimezone()).replace(tzinfo=None)


def local_text(utc_iso: str | None, tz_name: str | None) -> str | None:
    if not utc_iso:
        return None
    dt = _utc(utc_iso)
    tz = zone(tz_name)
    loc = dt.astimezone(tz) if tz else dt.astimezone()
    return loc.strftime("%Y-%m-%d %H:%M ") + (tz_name if tz else "(system time)")


# ---- recurrence --------------------------------------------------------------------------------------------------

def _hm(value: Any) -> tuple[int, int]:
    try:
        hh, mm = str(value).strip().split(":")[:2]
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        raise QAError("SCHEDULE_TIME", "Time must be HH:MM, for example 07:30.", status=422) from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise QAError("SCHEDULE_TIME", "Time must be HH:MM between 00:00 and 23:59.", status=422)
    return h, m


def _day(value: Any, what: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise QAError("SCHEDULE_DATE", f"{what} must be a date (YYYY-MM-DD).", status=422) from None


def next_after(spec: dict[str, Any], after: datetime) -> datetime | None:
    """The first due time (UTC) strictly after ``after`` (UTC), or None when the schedule has ended."""
    tz = zone(spec.get("timezone"))
    h, m = _hm(spec.get("time") or "00:00")
    start = _day(spec.get("start_date"), "Start date") or _wall(after, tz).date()
    end = _day(spec.get("end_date"), "End date")
    repeat = spec.get("repeat") or "once"

    def at(d: date) -> datetime:
        return _to_utc(datetime(d.year, d.month, d.day, h, m), tz)

    if repeat == "once":
        t = at(start)
        return t if t > after else None
    if repeat == "interval":
        every = timedelta(minutes=int(spec.get("interval_minutes") or 60))
        first = at(start)
        t = first if first > after else first + every * (int((after - first) / every) + 1)
        if end and _wall(t, tz).date() > end:
            return None
        return t
    if repeat == "daily":
        days = set(range(7))
    else:
        days = {int(x) for x in spec.get("days") or [] if 0 <= int(x) <= 6}
    if not days:
        return None
    weeks = max(1, int(spec.get("every_weeks") or 1)) if repeat == "weekly" else 1
    anchor = start - timedelta(days=start.weekday())  # the Monday of the first week
    d = max(start, _wall(after, tz).date() - timedelta(days=1))
    for _ in range(7 * weeks * 60):
        if end and d > end:
            return None
        if d.weekday() in days and ((d - anchor).days // 7) % weeks == 0:
            t = at(d)
            if t > after:
                return t
        d += timedelta(days=1)
    return None


def describe(spec: dict[str, Any]) -> str:
    t = spec.get("time") or "00:00"
    tz = "" if (spec.get("timezone") or "system") == "system" else f" ({spec['timezone']})"
    until = f" until {spec['end_date']}" if spec.get("end_date") else ""
    repeat = spec.get("repeat")
    if repeat == "once":
        return f"Once on {spec.get('start_date')} at {t}{tz}"
    if repeat == "interval":
        n = int(spec.get("interval_minutes") or 60)
        every = f"{n // 60} hour{'s' if n // 60 != 1 else ''}" if n % 60 == 0 else f"{n} minutes"
        return f"Every {every} from {spec.get('start_date')} {t}{tz}{until}"
    if repeat == "daily":
        return f"Every day at {t}{tz}{until}"
    names_ = ", ".join(DAY_NAMES[d] for d in sorted(spec.get("days") or []))
    if repeat == "weekly" and int(spec.get("every_weeks") or 1) > 1:
        return f"Every {spec['every_weeks']} weeks on {names_} at {t}{tz}{until}"
    return f"{'Every week on ' if repeat == 'weekly' else 'On '}{names_} at {t}{tz}{until}"


# ---- the scheduler -----------------------------------------------------------------------------------------------

class PerfScheduler:
    def __init__(self, performance: Any, *, reports: Any = None, notifications: Any = None, mailer: Any = None,
                 defects: Any = None, clock: Callable[[], datetime] = utcnow) -> None:
        self.performance = performance
        self.db = performance.db
        self.reports = reports
        self.notifications = notifications
        self.mailer = mailer
        self.defects = defects
        self.clock = clock
        self._stop = threading.Event()  # the 15-second loop ends
        self._closed = threading.Event()  # the application is closing: no reports or emails are started
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self.started_at: str | None = None
        self.last_tick_at: str | None = None
        self._after: set[threading.Thread] = set()
        performance.finished_hooks.append(self.on_run_finished)

    # ---- lifecycle ---------------------------------------------------------------------------------------------

    def recover(self) -> dict[str, int]:
        """When the application starts, before the run queue: a job claimed but never queued is an error; a
        scheduled run still queued from before the shutdown is a missed run unless its policy runs it once."""
        now = self.clock()
        out = {"claimed": 0, "cancelled": 0}
        for j in self.db.query("SELECT id, schedule_id FROM perf_schedule_jobs WHERE status = 'CLAIMED'"):
            self._job(j["id"], "ERROR", reason="The application stopped before this run was created.")
            out["claimed"] += 1
        keep: set[str] = set()
        rows = self.db.query("SELECT run_id, schedule_id, config, queued_at FROM perf_runs WHERE state = 'QUEUED'"
                             " AND schedule_id IS NOT NULL ORDER BY queued_at DESC")
        for r in rows:
            spec = self._spec_or_none(r["schedule_id"])
            sch = (loads(r["config"]) or {}).get("schedule") or {}
            due = _utc(sch.get("due_at")) or _utc(r["queued_at"])
            late = due is not None and (now - due).total_seconds() > GRACE_S
            if not late:
                continue
            if spec and spec.get("missed") == "run_once" and r["schedule_id"] not in keep:
                keep.add(r["schedule_id"])  # the newest one still runs, once
                continue
            reason = ("Not started: the application was closed before this scheduled run could start "
                      "(missed-run policy: skip).")
            self.performance.db.execute(
                "UPDATE perf_runs SET state = 'CANCELLED', status = 'FAILED', finished_at = ?, state_reason = ?"
                " WHERE run_id = ? AND state = 'QUEUED'", (_iso(now), reason, r["run_id"]))
            self.db.execute("UPDATE perf_schedule_jobs SET status = 'MISSED', reason = ?, updated_at = ?"
                            " WHERE run_id = ?", (reason, _iso(now), r["run_id"]))
            out["cancelled"] += 1
        return out

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.started_at = _iso(self.clock())
        self._thread = threading.Thread(target=self._loop, name="perf-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # the loop must keep going
                log.warning(f"Performance schedule check failed: {exc}")
            if self._stop.wait(TICK_S):
                return

    def close(self, timeout: float = 30) -> str:
        """Application shutdown. Returns what it means for the schedules (also logged)."""
        self._stop.set()
        self._closed.set()
        deadline = time.monotonic() + timeout
        for t in list(self._after):
            t.join(max(0.0, deadline - time.monotonic()))
        n = self.db.query_one("SELECT COUNT(*) AS n FROM perf_schedules WHERE enabled = 1")["n"]
        nxt = self.db.query_one("SELECT name, next_run_at, spec FROM perf_schedules WHERE enabled = 1 AND"
                                " next_run_at IS NOT NULL ORDER BY next_run_at LIMIT 1")
        msg = (f"Application closing: {n} active performance schedule(s) will not start until "
               f"{branding.name()} runs again on this PC; a running load test is stopped and kept as INTERRUPTED.")
        if nxt:
            msg += f" Next due: {nxt['name']} at {local_text(nxt['next_run_at'], loads(nxt['spec']).get('timezone'))}."
        if n:
            log.info(msg)
        return msg

    # ---- schedules ---------------------------------------------------------------------------------------------

    def _plan_for(self, scenario_id: str) -> tuple[Any, Any, Any]:
        sc = self.performance.get_scenario(scenario_id)
        if sc.archived:
            raise QAError("PERF_SCENARIO_ARCHIVED", "This scenario is archived. Restore it before scheduling it.",
                          status=409)
        if not sc.plan_id:
            raise QAError("PERF_SCENARIO_NO_SCRIPT", "This scenario has no script yet. Create or choose one first.",
                          status=409)
        plan = self.performance.get_plan(sc.plan_id)
        env = self.performance.get_environment(sc.environment_id) if sc.environment_id else None
        return sc, plan, env

    @staticmethod
    def planned_duration_s(plan: Any) -> int | None:
        if plan.workload is not None:
            return int(wl.duration(plan.workload)) or None
        if not plan.script:
            return int(plan.profile.duration_s)
        return None  # set by the script's own thread groups

    def _validate(self, body: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        s = {**(current or {}), **{k: v for k, v in body.items() if v is not None and k != "repin"}}
        if not s.get("scenario_id"):
            raise QAError("SCHEDULE_SCENARIO", "Choose the scenario to run.", status=422)
        sc, plan, env = self._plan_for(str(s["scenario_id"]))
        s["name"] = (str(s.get("name") or "").strip() or sc.name)[:120]
        repeat = s.get("repeat") or "once"
        if repeat not in REPEATS:
            raise QAError("SCHEDULE_REPEAT", f"Repeat must be one of {', '.join(REPEATS)}.", status=422)
        s["repeat"] = repeat
        _hm(s.get("time") or "")
        s["timezone"] = s.get("timezone") or "system"
        tz = zone(s["timezone"])
        today = _wall(self.clock(), tz).date()
        s["start_date"] = (_day(s.get("start_date"), "Start date") or today).isoformat()
        end = _day(s.get("end_date"), "End date")
        if end and end < date.fromisoformat(s["start_date"]):
            raise QAError("SCHEDULE_DATE", "The end date is before the start date.", status=422)
        s["end_date"] = end.isoformat() if end else None
        if repeat in ("days", "weekly"):
            days = sorted({int(d) for d in s.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
            if not days:
                raise QAError("SCHEDULE_DAYS", "Choose at least one day of the week.", status=422)
            s["days"] = days
        else:
            s["days"] = []
        s["every_weeks"] = max(1, min(52, int(s.get("every_weeks") or 1))) if repeat == "weekly" else 1
        if repeat == "interval":
            n = int(s.get("interval_minutes") or 0)
            if not MIN_INTERVAL <= n <= MAX_INTERVAL:
                raise QAError("SCHEDULE_INTERVAL", f"The interval must be {MIN_INTERVAL} minutes to 7 days.",
                              status=422)
            s["interval_minutes"] = n
        else:
            s["interval_minutes"] = None
        if s.get("overlap", "skip") not in OVERLAP:
            raise QAError("SCHEDULE_OVERLAP", "Overlap must be skip or queue. Parallel load runs are not available: "
                          "this PC is one load generator and runs one test at a time.", status=422)
        s["overlap"] = s.get("overlap") or "skip"
        if s.get("missed", "skip") not in MISSED:
            raise QAError("SCHEDULE_MISSED", "Missed runs must be skip or run_once.", status=422)
        s["missed"] = s.get("missed") or "skip"
        planned = self.planned_duration_s(plan)
        default_max = max(10, -(-int(planned * 1.5) // 60) + 5) if planned else 60
        s["max_runtime_min"] = int(s.get("max_runtime_min") or default_max)
        if not 1 <= s["max_runtime_min"] <= 24 * 60:
            raise QAError("SCHEDULE_MAX_RUNTIME", "The maximum runtime must be 1 minute to 24 hours.", status=422)
        if planned and s["max_runtime_min"] * 60 < planned:
            raise QAError("SCHEDULE_MAX_RUNTIME", f"The maximum runtime ({s['max_runtime_min']} min) is shorter than "
                          f"the planned test ({-(-planned // 60)} min). Raise it, or shorten the workload.", status=422)
        report = dict(s.get("report") or {})
        s["report"] = {"pdf": bool(report.get("pdf", True))}
        email = dict((s.get("notify") or {}).get("email") or {})
        email["enabled"] = bool(email.get("enabled"))
        email["on"] = email.get("on") if email.get("on") in ("always", "failures") else "always"
        if email["enabled"]:
            from ..mailer import split_addresses

            if not split_addresses(email.get("to")):
                raise QAError("SCHEDULE_EMAIL", "Add the email address(es) the report goes to.", status=422)
        s["notify"] = {"in_app": True, "email": email}
        s["build"] = str(s.get("build") or "").strip()[:120]
        policy = s.get("version_policy") or "latest"
        if policy not in VERSIONS:
            raise QAError("SCHEDULE_VERSIONS", "Versions must be latest or pinned.", status=422)
        s["version_policy"] = policy
        if policy == "pinned":
            stale = not s.get("pinned") or body.get("repin") or (current or {}).get("version_policy") != "pinned" \
                or (current or {}).get("scenario_id") != s["scenario_id"]
            if stale:
                s["pinned"] = self.pin(plan)
        else:
            s["pinned"] = None
        s["scenario_id"] = sc.id
        s["environment_id"] = sc.environment_id
        s["enabled"] = bool(s.get("enabled", True))
        return s

    def pin(self, plan: Any) -> dict[str, Any]:
        if plan.script and not plan.script_version:
            raise QAError("SCHEDULE_NOT_SAVED", "The script has no saved version yet. Save it in the script editor "
                          "first; unsaved drafts are never run.", status=409)
        return {"plan_id": plan.id, "script_version": plan.script_version if plan.script else None,
                "datasets": {d["id"]: d["version"] for d in self.performance.plan_datasets(plan) if d["version"]},
                "workload": plan.workload.model_dump() if plan.workload is not None else None,
                "sla": plan.sla.model_dump(), "pinned_at": _iso(self.clock())}

    def create(self, body: dict[str, Any]) -> dict[str, Any]:
        s = self._validate(body)
        sid = "PSCH-" + _secrets.token_hex(4).upper()
        nxt = next_after(s, self.clock() - timedelta(seconds=1)) if s["enabled"] else None
        if s["enabled"] and nxt is None:
            raise QAError("SCHEDULE_PAST", "That date and time has already passed. Choose a future time.", status=422)
        now = _iso(self.clock())
        self.db.execute("INSERT INTO perf_schedules (id, name, scenario_id, enabled, spec, next_run_at, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (sid, s["name"], s["scenario_id"], int(s["enabled"]), dumps(s), _iso(nxt), now, now))
        return self.get(sid)

    def update(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        cur = self._row(sid)
        spec = loads(cur["spec"])
        s = self._validate(body, spec)
        nxt = next_after(s, self.clock() - timedelta(seconds=1)) if s["enabled"] else None
        timing = any(k in body for k in ("time", "start_date", "repeat", "days", "interval_minutes", "timezone",
                                         "every_weeks")) or (body.get("enabled") and not cur["enabled"])
        if s["enabled"] and nxt is None and timing:
            raise QAError("SCHEDULE_PAST", "That date and time has already passed. Choose a future time.", status=422)
        self.db.execute("UPDATE perf_schedules SET name = ?, scenario_id = ?, enabled = ?, spec = ?, next_run_at = ?,"
                        " updated_at = ? WHERE id = ?", (s["name"], s["scenario_id"], int(s["enabled"]), dumps(s),
                                                         _iso(nxt), _iso(self.clock()), sid))
        return self.get(sid)

    def set_enabled(self, sid: str, enabled: bool) -> dict[str, Any]:
        """Pause keeps everything; resume plans the next time from now (times passed while paused are not run)."""
        return self.update(sid, {"enabled": enabled})

    def delete(self, sid: str) -> None:
        self._row(sid)
        self.db.execute("DELETE FROM perf_schedules WHERE id = ?", (sid,))
        self.db.execute("DELETE FROM perf_schedule_jobs WHERE schedule_id = ? AND status <> 'QUEUED'", (sid,))

    def _row(self, sid: str) -> Any:
        r = self.db.query_one("SELECT * FROM perf_schedules WHERE id = ?", (sid,))
        if r is None:
            raise QAError("SCHEDULE_NOT_FOUND", f"Schedule {sid} does not exist.", status=404)
        return r

    def _spec_or_none(self, sid: str) -> dict[str, Any] | None:
        r = self.db.query_one("SELECT spec FROM perf_schedules WHERE id = ?", (sid,))
        return loads(r["spec"]) if r else None

    def get(self, sid: str) -> dict[str, Any]:
        return self._view(self._row(sid))

    def list(self, scenario_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM perf_schedules" + (" WHERE scenario_id = ?" if scenario_id else "")
                             + " ORDER BY enabled DESC, next_run_at IS NULL, next_run_at, name",
                             (scenario_id,) if scenario_id else ())
        return [self._view(r) for r in rows]

    def _view(self, r: Any) -> dict[str, Any]:
        s = loads(r["spec"])
        out = {**s, "id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "next_run_at": r["next_run_at"],
               "next_run_local": local_text(r["next_run_at"], s.get("timezone")), "when": describe(s),
               "created_at": r["created_at"], "updated_at": r["updated_at"],
               "timezone_label": system_zone_name() if s.get("timezone", "system") == "system" else s["timezone"]}
        try:
            sc = self.performance.get_scenario(s["scenario_id"])
            out["scenario_name"] = sc.name
            env = self.performance.get_environment(sc.environment_id) if sc.environment_id else None
            out["environment_name"] = env.name if env else None
        except QAError:
            out["scenario_name"] = f"{s['scenario_id']} (deleted)"
            out["environment_name"] = None
        last = self.db.query_one("SELECT j.status, j.run_id, j.reason, j.due_at, j.kind, r.state, r.verdict"
                                 " FROM perf_schedule_jobs j LEFT JOIN perf_runs r ON r.run_id = j.run_id"
                                 " WHERE j.schedule_id = ? ORDER BY j.id DESC LIMIT 1", (r["id"],))
        out["last"] = dict(last) if last else None
        out["active_run"] = self._active_run(r["id"])
        out["finished"] = bool(r["enabled"]) and r["next_run_at"] is None
        return out

    def jobs(self, sid: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT j.*, s.name AS schedule_name, r.state AS run_state, r.verdict AS run_verdict"
                             " FROM perf_schedule_jobs j LEFT JOIN perf_schedules s ON s.id = j.schedule_id"
                             " LEFT JOIN perf_runs r ON r.run_id = j.run_id"
                             + (" WHERE j.schedule_id = ?" if sid else "") + " ORDER BY j.id DESC LIMIT ?",
                             ((sid, limit) if sid else (limit,)))
        return [dict(r) for r in rows]

    # ---- due times ---------------------------------------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Start what is due, record what was missed, stop runs past their maximum runtime."""
        with self._tick_lock:
            now = now or self.clock()
            self.last_tick_at = _iso(now)
            self._watchdog(now)
            out: list[dict[str, Any]] = []
            for r in self.db.query("SELECT * FROM perf_schedules WHERE enabled = 1 AND next_run_at IS NOT NULL"
                                   " AND next_run_at <= ?", (_iso(now),)):
                out += self._due(r, now)
            return out

    def _due(self, r: Any, now: datetime) -> list[dict[str, Any]]:
        spec = loads(r["spec"])
        times: list[datetime] = []
        t = _utc(r["next_run_at"])
        while t is not None and t <= now and len(times) < 20000:
            times.append(t)
            t = next_after(spec, t)
        nxt = next_after(spec, now)
        self.db.execute("UPDATE perf_schedules SET next_run_at = ?, updated_at = ? WHERE id = ? AND next_run_at = ?",
                        (_iso(nxt), _iso(now), r["id"], r["next_run_at"]))
        if not times:
            return []
        latest = times[-1]
        on_time = (now - latest).total_seconds() <= GRACE_S
        missed = times if not on_time else times[:-1]
        out = []
        if missed:
            self._record_missed(r["id"], spec, missed)
        if on_time:
            out.append(self._fire(r["id"], spec, key=_iso(latest), due=latest, kind="schedule"))
        elif spec.get("missed") == "run_once":
            out.append(self._fire(r["id"], spec, key=_iso(latest), due=latest, kind="catch_up"))
        return [o for o in out if o]

    def _record_missed(self, sid: str, spec: dict[str, Any], missed: list[datetime]) -> None:
        reason = ("Missed: the application was not running at this time (closed, or the PC was off or asleep). "
                  "No run took place.")
        if spec.get("missed") == "run_once":
            reason += " Missed-run policy: run once (the latest missed time only)."
        else:
            reason += " Missed-run policy: skip."
        catch = missed[-1] if spec.get("missed") == "run_once" else None
        shown = missed[-MISSED_KEEP:]
        for t in shown:
            if t == catch:
                continue  # recorded as the catch-up job instead
            self._insert(sid, _iso(t), t, "missed", "MISSED", reason=reason)
        rest = len(missed) - len(shown)
        if rest > 0:
            self._insert(sid, f"missed:{_iso(missed[0])}..{_iso(missed[rest - 1])}", missed[0], "missed", "MISSED",
                         reason=f"{rest} more missed time(s) from {local_text(_iso(missed[0]), spec.get('timezone'))}"
                                f" to {local_text(_iso(missed[rest - 1]), spec.get('timezone'))}. No runs took place.")
        self._notify("warning", f"Missed performance schedule: {spec.get('name')}",
                     f"{len(missed)} scheduled time(s) passed while the application was not running; "
                     + ("the latest one runs now." if catch else "they were skipped."), link="#/perf-schedules")

    def _insert(self, sid: str, key: str, due: datetime | None, kind: str, status: str, *,
                reason: str | None = None) -> int | None:
        now = _iso(self.clock())
        try:
            cur = self.db.execute("INSERT INTO perf_schedule_jobs (schedule_id, job_key, due_at, kind, status, reason,"
                                  " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (sid, key, _iso(due), kind, status, reason, now, now))
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None  # this due time already has a job: never a second run

    def _job(self, job_id: int, status: str, *, run_id: str | None = None, reason: str | None = None) -> None:
        self.db.execute("UPDATE perf_schedule_jobs SET status = ?, run_id = COALESCE(?, run_id), reason = ?,"
                        " updated_at = ? WHERE id = ?", (status, run_id, reason, _iso(self.clock()), job_id))

    def _active_run(self, sid: str) -> dict[str, Any] | None:
        r = self.db.query_one("SELECT run_id, state FROM perf_runs WHERE schedule_id = ? AND state IN"
                              f" ({','.join('?' * len(ACTIVE))}) ORDER BY queued_at LIMIT 1", (sid, *ACTIVE))
        return dict(r) if r else None

    def _overlap(self, sid: str, spec: dict[str, Any]) -> str | None:
        """Why this run must not start now, or None."""
        q = ",".join("?" * len(ACTIVE))
        mine = self.db.query("SELECT run_id, state FROM perf_runs WHERE schedule_id = ? AND state IN"
                             f" ({q})", (sid, *ACTIVE))
        env = spec.get("environment_id")
        others = self.db.query(f"SELECT run_id, state, name FROM perf_runs WHERE state IN ({q})"
                               + (" AND environment_id = ?" if env else ""), (*ACTIVE, env) if env else ACTIVE)
        if spec.get("overlap") == "queue":
            if any(m["state"] == "QUEUED" for m in mine):
                return (f"Skipped: a run of this schedule ({next(m['run_id'] for m in mine if m['state'] == 'QUEUED')})"
                        " is already waiting; overlap policy queue keeps at most one waiting run.")
            return None
        busy = mine or others
        if busy:
            b = busy[0]
            where = "of this schedule" if mine else "on the same environment" if env else "on this load generator"
            return (f"Skipped: run {b['run_id']} {where} was still {b['state'].lower()}; overlap policy: skip "
                    "(load runs never overlap).")
        return None

    def _fire(self, sid: str, spec: dict[str, Any], *, key: str, due: datetime | None, kind: str
              ) -> dict[str, Any] | None:
        job = self._insert(sid, key, due, kind, "CLAIMED")
        if job is None:
            return None
        try:
            why = self._overlap(sid, spec)
            if why:
                self._job(job, "SKIPPED", reason=why)
                self._notify("warning", f"Scheduled run skipped: {spec.get('name')}", why, link="#/perf-schedules")
                return {"job_id": job, "status": "SKIPPED", "reason": why}
            sc, plan, env = self._plan_for(spec["scenario_id"])
            overrides: dict[str, Any] = {}
            pinned = spec.get("pinned") if spec.get("version_policy") == "pinned" else None
            if pinned:
                if pinned.get("plan_id") and pinned["plan_id"] != plan.id:
                    raise QAError("SCHEDULE_PINNED_CHANGED", "The scenario now uses another script than the one "
                                  "pinned. Edit the schedule and pin again.", status=409)
                if pinned.get("script_version"):
                    try:
                        text = self.performance.script_version(plan.id, int(pinned["script_version"]))
                    except QAError:
                        raise QAError("SCHEDULE_PINNED_MISSING", f"Pinned script version {pinned['script_version']}"
                                      " no longer exists.", status=409) from None
                    overrides.update(script_version=int(pinned["script_version"]), script=text)
                overrides["workload"] = pinned.get("workload")
                overrides["sla"] = pinned.get("sla")
                need = dict(pinned.get("datasets") or {})
            else:
                need = {d["id"]: d["version"] for d in self.performance.plan_datasets(plan) if d["version"]}
            changed = self.performance._datasets_changed({"schedule": {"require_datasets": need}})
            if changed:
                raise QAError("SCHEDULE_PINNED_DATA", changed + " Edit the schedule and pin again to use them.",
                              status=409)
            sched = {"id": sid, "name": spec.get("name"), "job_id": job, "job_key": key, "due_at": _iso(due),
                     "kind": kind, "version_policy": spec.get("version_policy"), "require_datasets": need,
                     "script_version": overrides.get("script_version") or plan.script_version,
                     "max_runtime_min": spec.get("max_runtime_min"), "report": spec.get("report"),
                     "notify": spec.get("notify"), "overlap": spec.get("overlap"), "missed": spec.get("missed"),
                     "timezone": spec.get("timezone")}
            run_id = self.performance.enqueue(plan.id, scenario_id=sc.id, trigger="schedule",
                                              overrides=overrides or None, build=spec.get("build") or None,
                                              schedule=sched)
            self._job(job, "QUEUED", run_id=run_id)
            self.db.execute("UPDATE perf_schedules SET last_run_id = ?, last_job_at = ? WHERE id = ?",
                            (run_id, _iso(self.clock()), sid))
            return {"job_id": job, "status": "QUEUED", "run_id": run_id}
        except QAError as exc:
            self._job(job, "SKIPPED" if exc.error_code.startswith("SCHEDULE_") else "ERROR", reason=exc.message)
            self._notify("error", f"Scheduled run not started: {spec.get('name')}", exc.message,
                         link="#/perf-schedules")
            return {"job_id": job, "status": "ERROR", "reason": exc.message}
        except Exception as exc:  # the scheduler keeps going; the job says why
            self._job(job, "ERROR", reason=str(exc)[:300] or repr(exc))
            return {"job_id": job, "status": "ERROR", "reason": str(exc)[:300]}

    def run_now(self, sid: str) -> dict[str, Any]:
        """Run the schedule's scenario now, with its versions and options (its next time is unchanged)."""
        r = self._row(sid)
        spec = loads(r["spec"])
        res = self._fire(sid, spec, key="manual:" + _secrets.token_hex(6), due=self.clock(), kind="manual")
        if res is None or res["status"] != "QUEUED":
            raise QAError("SCHEDULE_NOT_STARTED", (res or {}).get("reason") or "The run did not start.", status=409)
        return res

    # ---- running runs ------------------------------------------------------------------------------------------

    def _watchdog(self, now: datetime) -> None:
        for r in self.db.query("SELECT run_id, config, started_at FROM perf_runs WHERE schedule_id IS NOT NULL"
                               " AND state IN ('VALIDATING', 'RUNNING')"):
            sch = (loads(r["config"]) or {}).get("schedule") or {}
            limit = sch.get("max_runtime_min")
            started = _utc(r["started_at"])
            if limit and started and (now - started).total_seconds() > int(limit) * 60:
                self.performance.stop(r["run_id"], "graceful", reason=(
                    f"Stopped: the schedule's maximum runtime of {limit} min was reached; results up to the stop "
                    "are kept."))

    def on_run_finished(self, run_id: str, verdict: str | None) -> None:
        row = self.db.query_one("SELECT state, config, schedule_id FROM perf_runs WHERE run_id = ?", (run_id,))
        if not row or not row["schedule_id"]:
            return
        sch = (loads(row["config"]) or {}).get("schedule") or {}
        if sch.get("job_id"):
            self._job(int(sch["job_id"]), row["state"], reason=None)
        if self._closed.is_set():
            return

        def work() -> None:
            try:
                report = None
                if (sch.get("report") or {}).get("pdf", True) and self.reports is not None:
                    if verdict == "FAIL" and self.defects is not None:
                        self.defects.wait_for_automatic(run_id, 30)
                    try:
                        report = self.reports.perf_run_report_serial(run_id)
                    except QAError as exc:
                        log.info(f"Scheduled run report not created: {exc.message}", extra={"run_id": run_id})
                self._email(run_id, sch, verdict, report)
            except Exception as exc:
                log.warning(f"After the scheduled run {run_id}: {exc}")
            finally:
                self._after.discard(threading.current_thread())

        t = threading.Thread(target=work, name=f"sched-after-{run_id}", daemon=True)
        self._after.add(t)
        t.start()

    def _email(self, run_id: str, sch: dict[str, Any], verdict: str | None, report: dict[str, Any] | None) -> None:
        email = ((sch.get("notify") or {}).get("email")) or {}
        if not email.get("enabled") or self.mailer is None:
            return
        run = self.performance.get_run(run_id)
        failed = run["state"] != "COMPLETED" or verdict == "FAIL"
        if email.get("on") == "failures" and not failed:
            return
        from html import escape

        o = ((run.get("metrics") or {}).get("overall")) or {}
        verdict_text = {"PASS": "targets met", "FAIL": "targets missed", "NOT_SPECIFIED": "no targets set",
                        "INCONCLUSIVE": "inconclusive (not a complete run)"}.get(verdict or "", "no verdict")
        subject = f"[{run['state']}] {sch.get('name')}: {verdict_text} ({run_id})"
        figures = (f"{o.get('samples')} requests, 95th percentile {o.get('p95_ms')} ms, errors {o.get('error_pct')}%,"
                   f" {o.get('throughput')} requests/s") if o else "No samples were recorded."
        body = (f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px'><h2>{escape(str(sch.get('name')))}"
                f"</h2><p>Run {escape(run_id)}: {escape(run['state'].lower())}, {escape(verdict_text)}.</p>"
                f"<p>{escape(figures)}</p><p>Scheduled for {escape(str(local_text(sch.get('due_at'), sch.get('timezone'))))}."
                f"{' ' + escape(str(run.get('state_reason'))) if run.get('state_reason') else ''}</p>"
                f"<p style='color:#6b7280;font-size:12px'>The full report is attached when it was made. Sent by QA "
                f"Enterprise AI.</p></div>")
        files = []
        if report and report.get("pdf"):
            from pathlib import Path

            files.append(Path(run["folder"]) / "qa-report.pdf")
        try:
            self.mailer.send(email.get("to"), subject, body, cc=email.get("cc"), attachments=files)
        except Exception as exc:
            log.warning(f"Scheduled run email not sent: {exc}", extra={"run_id": run_id})

    def _notify(self, severity: str, title: str, message: str, *, link: str | None = None) -> None:
        if self.notifications is None:
            return
        try:
            self.notifications.create("performance", severity, title, message, link=link)
        except Exception as exc:
            log.info(f"Notification not saved: {exc}")

    # ---- host ----------------------------------------------------------------------------------------------------

    def host(self) -> dict[str, Any]:
        from .. import startup

        nxt = self.db.query_one("SELECT id, name, next_run_at, spec FROM perf_schedules WHERE enabled = 1 AND"
                                " next_run_at IS NOT NULL ORDER BY next_run_at LIMIT 1")
        return {
            "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
            "started_at": self.started_at, "last_check_at": self.last_tick_at, "check_every_s": TICK_S,
            "system_timezone": system_zone_name(), "timezones": zones(),
            "enabled": self.db.query_one("SELECT COUNT(*) AS n FROM perf_schedules WHERE enabled = 1")["n"],
            "next": {"id": nxt["id"], "name": nxt["name"], "at": nxt["next_run_at"],
                     "local": local_text(nxt["next_run_at"], loads(nxt["spec"]).get("timezone"))} if nxt else None,
            "startup": startup.status(),
            "explain": (f"Schedules run only while {branding.name()} runs on this PC. Closing the browser does not stop "
                        "a run or a schedule. Closing the application stops a running load test (kept as INTERRUPTED)"
                        " and no schedule starts until the application runs again; times passed meanwhile are "
                        "recorded as missed. Start at Windows sign-in starts it for you without administrator rights."),
        }
