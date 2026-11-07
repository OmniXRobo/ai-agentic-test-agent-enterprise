"""23.3.27: live metrics of a running test, for the live dashboard.

Everything here is read from the JTL (CSV) JMeter is writing, so the dashboard shows only what JMeter has recorded:
no value is estimated or filled in. Samples are counted at their start time, so the last seconds fill up as slower
samples finish; those seconds are marked as still open.

Kept bounded for long (soak) runs: per-second figures are kept, the raw response times only for the last
`window_s` seconds (for the rolling percentiles) and for seconds that are still open."""

from __future__ import annotations

import csv
import math
import threading
from collections import deque
from pathlib import Path
from typing import Any

from .jtl import percentile, read_rows

OPEN_S = 5  # a second counts as complete once samples 5 s later have been written
MAX_POINTS = 600  # timeline points sent to the page (seconds are merged on long runs)
MAX_WINDOW_SAMPLES = 100_000


def _int(v: str | None) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except ValueError:
        return 0


class _Sec:
    __slots__ = ("n", "err", "sum", "threads", "values", "p95", "tx")

    def __init__(self) -> None:
        self.n = 0  # requests (transaction samples are counted apart in tx)
        self.err = 0
        self.sum = 0
        self.threads = 0
        self.values: list[int] | None = []
        self.p95: float | None = None
        self.tx = 0


class _Label:
    __slots__ = ("n", "err", "sum", "max", "tx")

    def __init__(self, tx: bool) -> None:
        self.n = self.err = self.sum = self.max = 0
        self.tx = tx


class LiveMetrics:
    def __init__(self, path: Path, *, started_ms: int, transactions: set[str] | None = None, window_s: int = 30,
                 planned: list[dict[str, Any]] | None = None, planned_duration_s: int | None = None,
                 targets: dict[str, Any] | None = None, rate_target: float | None = None) -> None:
        self.path = path
        self.started_ms = started_ms
        self.transactions = transactions or set()
        self.window_s = window_s
        self.planned = planned or []
        self.planned_duration_s = planned_duration_s
        self.targets = {k: v for k, v in (targets or {}).items() if v is not None}
        self.rate_target = rate_target
        self.offset = 0
        self.header: list[str] | None = None
        self.secs: dict[int, _Sec] = {}
        self.labels: dict[str, _Label] = {}
        self.window: deque[tuple[int, int, bool, str]] = deque()  # (ts_ms, elapsed, ok, label), requests only
        self.errors: dict[str, int] = {}
        self.recent_errors: deque[dict[str, Any]] = deque(maxlen=20)
        self.samples = self.failed = self.elapsed_sum = 0
        self.tx_samples = 0
        self.threads = 0
        self.last_ts = 0
        self.first_ts: int | None = None
        self._lock = threading.Lock()

    # ---- reading ---------------------------------------------------------------------------------------------

    def poll(self) -> None:
        """Read the rows JMeter has written since the last poll."""
        lines, self.offset = read_rows(self.path, self.offset)
        if not lines:
            return
        with self._lock:
            if self.header is None:
                self.header = next(csv.reader([lines[0]]))
                lines = lines[1:]
            for r in csv.DictReader(lines, fieldnames=self.header):
                if r.get("timeStamp") in (None, "", "timeStamp"):
                    continue
                self._add(r)
            self._finalize()

    def _add(self, r: dict[str, str]) -> None:
        ts, el = _int(r.get("timeStamp")), _int(r.get("elapsed"))
        ok = (r.get("success") or "").strip().lower() == "true"
        label = r.get("label") or "(unnamed)"
        tx = label in self.transactions
        threads = _int(r.get("allThreads"))
        self.threads = threads or self.threads
        self.last_ts = max(self.last_ts, ts + el)
        self.first_ts = ts if self.first_ts is None else min(self.first_ts, ts)
        sec = max(0, (ts - self.started_ms) // 1000)
        s = self.secs.get(sec)
        if s is None:
            s = self.secs[sec] = _Sec()
        s.threads = max(s.threads, threads)
        lb = self.labels.get(label)
        if lb is None:
            lb = self.labels[label] = _Label(tx)
        lb.n += 1
        lb.sum += el
        lb.max = max(lb.max, el)
        if tx:
            s.tx += 1
            self.tx_samples += 1
            if not ok:
                lb.err += 1
            return
        s.n += 1
        s.sum += el
        if s.values is not None:
            s.values.append(el)
        self.samples += 1
        self.elapsed_sum += el
        if not ok:
            s.err += 1
            lb.err += 1
            self.failed += 1
            code = (r.get("responseCode") or "").strip()
            msg = (r.get("failureMessage") or r.get("responseMessage") or "").strip().replace("\n", " ")
            key = f"{code} {msg}".strip()[:160]
            self.errors[key] = self.errors.get(key, 0) + 1
            self.recent_errors.append({"t": round((ts - self.started_ms) / 1000, 1), "label": label, "code": code,
                                       "message": msg[:300]})
        self.window.append((ts, el, ok, label))
        if len(self.window) > MAX_WINDOW_SAMPLES:
            self.window.popleft()

    def _finalize(self) -> None:
        """Seconds no longer open get their 95th percentile; their raw values are dropped. Old window samples go."""
        newest = (self.last_ts - self.started_ms) // 1000
        for sec, s in self.secs.items():
            if s.values is not None and sec < newest - OPEN_S:
                s.p95 = percentile(sorted(s.values), 95) if s.values else None
                s.values = None
        cut = self.last_ts - self.window_s * 1000
        while self.window and self.window[0][0] < cut:
            self.window.popleft()

    # ---- what the page shows -----------------------------------------------------------------------------

    def _span_s(self) -> float:
        """Seconds the window covers: window_s, or less early in a run (dividing by the full window would understate
        the rate while the first samples arrive)."""
        if self.first_ts is None:
            return float(self.window_s)
        return min(float(self.window_s), max(1.0, (self.last_ts - self.first_ts) / 1000))

    def summary(self) -> dict[str, Any]:
        """The short form kept on the run (run lists, events): the same keys as before 23.3.27."""
        with self._lock:
            w = [x for x in self.window]
            span = self._span_s()
        return {"samples": self.samples, "errors": self.failed,
                "error_pct": round(self.failed * 100.0 / self.samples, 2) if self.samples else 0.0,
                "avg_ms": round(self.elapsed_sum / self.samples, 1) if self.samples else 0.0,
                "threads": self.threads, "recent_throughput": round(len(w) / span, 1) if w else None}

    def planned_users_at(self, t: float) -> float:
        total = 0.0
        for g in self.planned:
            start, end = g["delay_s"], g["delay_s"] + g["duration_s"]
            if t < start or t >= end:
                continue
            ramp = g["ramp_up_s"]
            total += g["users"] if ramp <= 0 or t >= start + ramp else g["users"] * (t - start) / ramp
        return round(total, 2)

    def planned_curve(self) -> list[list[float]]:
        if not self.planned or not all(g["duration_s"] for g in self.planned):
            return []
        marks = {0.0}
        for g in self.planned:
            s, e = g["delay_s"], g["delay_s"] + g["duration_s"]
            marks.update({float(s), float(s + g["ramp_up_s"]), float(e)})
        out: list[list[float]] = []
        for t in sorted(marks):
            for p in ([t, self.planned_users_at(t - 1e-6)], [t, self.planned_users_at(t)]) if t > 0 else \
                    ([t, self.planned_users_at(t)],):
                if not out or out[-1] != p:
                    out.append(p)
        return out

    def snapshot(self, now_ms: int) -> dict[str, Any]:
        with self._lock:
            elapsed_s = max(0.0, (now_ms - self.started_ms) / 1000)
            w = list(self.window)
            secs = sorted(self.secs.items())
            labels = {k: (v.n, v.err, v.sum, v.max, v.tx) for k, v in self.labels.items()}
            errors = dict(self.errors)
            recent = list(self.recent_errors)
            open_from = (self.last_ts - self.started_ms) // 1000 - OPEN_S
            span = self._span_s()
            timeline = []
            for sec, s in secs:
                p95 = s.p95 if s.values is None else (percentile(sorted(s.values), 95) if s.values else None)
                timeline.append([sec, s.n, s.err, s.sum, s.threads, p95, sec >= open_from])
        # window figures (requests only, by start time, over the last window_s seconds of samples, or fewer early on)
        vals = sorted(x[1] for x in w)
        win_err = sum(1 for x in w if not x[2])
        per_label: dict[str, list[int]] = {}
        for _, el, ok, label in w:
            per_label.setdefault(label, []).append(el)
        window = {
            "seconds": round(span), "samples": len(vals),
            "throughput": round(len(vals) / span, 2),
            "avg_ms": round(sum(vals) / len(vals), 1) if vals else None,
            "p50_ms": percentile(vals, 50) if vals else None, "p90_ms": percentile(vals, 90) if vals else None,
            "p95_ms": percentile(vals, 95) if vals else None, "p99_ms": percentile(vals, 99) if vals else None,
            "error_pct": round(win_err * 100.0 / len(vals), 2) if vals else None,
        }
        # targets: shown as "now above / below", never as the verdict (that comes from the full results)
        watch = []
        for key, name, actual, up in (("p95_ms", "95th percentile", window["p95_ms"], False),
                                      ("p90_ms", "90th percentile", window["p90_ms"], False),
                                      ("p99_ms", "99th percentile", window["p99_ms"], False),
                                      ("avg_ms", "Average", window["avg_ms"], False),
                                      ("max_error_pct", "Error rate", window["error_pct"], False),
                                      ("min_throughput", "Throughput", window["throughput"], True)):
            if key in self.targets and actual is not None:
                limit = self.targets[key]
                ok = actual >= limit if up else actual <= limit
                watch.append({"name": name, "limit": limit, "actual": actual, "ok": ok,
                              "unit": "req/s" if up else "%" if key == "max_error_pct" else "ms"})
        if self.rate_target:
            watch.append({"name": "Request rate target", "limit": self.rate_target, "actual": window["throughput"],
                          "ok": window["throughput"] >= 0.95 * self.rate_target, "unit": "req/s"})
        # timeline, merged to at most MAX_POINTS points
        last_sec = timeline[-1][0] if timeline else -1
        width = max(1, math.ceil((last_sec + 1) / MAX_POINTS))
        points: list[dict[str, Any]] = []
        bucket: dict[int, list[Any]] = {}
        for row in timeline:
            bucket.setdefault(row[0] // width, []).append(row)
        threads = 0
        for b in range(0, last_sec // width + 1 if timeline else 0):
            rows = bucket.get(b, [])  # a second without samples: nothing started then (0 requests per second)
            n = sum(r[1] for r in rows)
            p95s = [r[5] for r in rows if r[5] is not None]
            threads = max((r[4] for r in rows), default=threads)
            points.append({"t": b * width, "throughput": round(n / width, 2), "errors": sum(r[2] for r in rows),
                           "avg_ms": round(sum(r[3] for r in rows) / n, 1) if n else 0.0,
                           "p95_ms": max(p95s) if p95s else 0.0, "threads": threads,
                           "planned": self.planned_users_at(b * width) if self.planned else None,
                           "open": any(r[6] for r in rows)})
        reqs = []
        for k, (n, err, total, mx, tx) in sorted(labels.items()):
            wv = sorted(per_label.get(k, []))
            reqs.append({"label": k, "transaction": tx, "samples": n, "errors": err,
                         "error_pct": round(err * 100.0 / n, 2) if n else 0.0,
                         "avg_ms": round(total / n, 1) if n else 0.0, "max_ms": mx,
                         "window_throughput": round(len(wv) / span, 2),
                         "window_p95_ms": percentile(wv, 95) if wv else None})
        planned_now = self.planned_users_at(elapsed_s) if self.planned else None
        return {
            "elapsed_s": round(elapsed_s, 1), "planned_duration_s": self.planned_duration_s,
            "progress_pct": (min(100.0, round(elapsed_s * 100 / self.planned_duration_s, 1))
                             if self.planned_duration_s else None),
            "active_users": self.threads, "planned_users_now": planned_now,
            "samples": self.samples, "errors": self.failed, "transaction_samples": self.tx_samples,
            "error_pct": round(self.failed * 100.0 / self.samples, 2) if self.samples else 0.0,
            "avg_ms": round(self.elapsed_sum / self.samples, 1) if self.samples else None,
            "window": window, "watch": watch, "rate_target": self.rate_target,
            "timeline": points, "bucket_s": width, "planned_curve": self.planned_curve(),
            "requests": reqs,
            "top_errors": sorted(({"error": k, "count": v} for k, v in errors.items()), key=lambda e: -e["count"])[:10],
            "recent_errors": recent[::-1],
        }
