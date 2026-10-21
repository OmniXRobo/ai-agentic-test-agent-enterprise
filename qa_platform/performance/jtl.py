"""JTL (CSV) parsing and metrics: per-request and overall response times, errors, throughput, timeline."""

from __future__ import annotations

import csv
import math
from array import array
from pathlib import Path
from typing import Any, Iterable

TOTAL = "ALL REQUESTS"


def percentile(sorted_values: Any, pct: float) -> float:
    """Nearest-rank percentile on an already sorted sequence (the method JMeter's aggregate report uses)."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    rank = max(1, math.ceil(pct / 100.0 * n))
    return float(sorted_values[rank - 1])


class _Bucket:
    __slots__ = ("elapsed", "errors", "bytes", "sent", "first", "last", "latency", "connect", "codes", "messages")

    def __init__(self) -> None:
        self.elapsed = array("l")
        self.errors = 0
        self.bytes = 0
        self.sent = 0
        self.first: int | None = None
        self.last: int | None = None
        self.latency = 0
        self.connect = 0
        self.codes: dict[str, int] = {}
        self.messages: dict[str, int] = {}

    def add(self, ts: int, elapsed: int, ok: bool, code: str, message: str, nbytes: int, sent: int, latency: int,
            connect: int) -> None:
        self.elapsed.append(elapsed)
        self.bytes += nbytes
        self.sent += sent
        self.latency += latency
        self.connect += connect
        end = ts + elapsed
        self.first = ts if self.first is None else min(self.first, ts)
        self.last = end if self.last is None else max(self.last, end)
        self.codes[code] = self.codes.get(code, 0) + 1
        if not ok:
            self.errors += 1
            key = f"{code} {message}".strip()[:160]
            self.messages[key] = self.messages.get(key, 0) + 1

    def summary(self, label: str) -> dict[str, Any]:
        n = len(self.elapsed)
        values = sorted(self.elapsed)
        span_s = max(((self.last or 0) - (self.first or 0)) / 1000.0, 0.001)
        return {
            "label": label,
            "samples": n,
            "errors": self.errors,
            "error_pct": round(self.errors * 100.0 / n, 3) if n else 0.0,
            "avg_ms": round(sum(values) / n, 1) if n else 0.0,
            "min_ms": float(values[0]) if n else 0.0,
            "max_ms": float(values[-1]) if n else 0.0,
            "p50_ms": percentile(values, 50),
            "p90_ms": percentile(values, 90),
            "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99),
            "throughput": round(n / span_s, 3) if n else 0.0,
            "received_kb_s": round(self.bytes / 1024.0 / span_s, 2) if n else 0.0,
            "sent_kb_s": round(self.sent / 1024.0 / span_s, 2) if n else 0.0,
            "avg_latency_ms": round(self.latency / n, 1) if n else 0.0,
            "avg_connect_ms": round(self.connect / n, 1) if n else 0.0,
            "response_codes": dict(sorted(self.codes.items())),
            "top_errors": sorted(({"error": k, "count": v} for k, v in self.messages.items()),
                                 key=lambda e: -e["count"])[:10],
        }


def _int(value: str | None) -> int:
    try:
        return int(float(value)) if value not in (None, "") else 0
    except ValueError:
        return 0


def read_rows(path: Path, start: int = 0) -> tuple[list[str], int]:
    """Read complete CSV rows from byte offset `start`; returns rows and the offset after the last full line."""
    if not path.exists():
        return [], start
    with path.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    if start == 0 and not data:
        return [], 0
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], start
    chunk = data[: cut + 1].decode("utf-8", errors="replace")
    return chunk.splitlines(), start + cut + 1


def compute(rows: Iterable[dict[str, str]], timeline_points: int = 300) -> dict[str, Any]:
    labels: dict[str, _Bucket] = {}
    total = _Bucket()
    per_second: dict[int, list[int]] = {}  # second -> [samples, errors, elapsed_sum, max_threads]
    for r in rows:
        if r.get("timeStamp") in (None, "", "timeStamp"):
            continue
        ts, elapsed = _int(r.get("timeStamp")), _int(r.get("elapsed"))
        ok = (r.get("success") or "").strip().lower() == "true"
        args = (ts, elapsed, ok, (r.get("responseCode") or "").strip(),
                (r.get("failureMessage") or r.get("responseMessage") or "").strip(), _int(r.get("bytes")),
                _int(r.get("sentBytes")), _int(r.get("Latency")), _int(r.get("Connect")))
        label = r.get("label") or "(unnamed)"
        labels.setdefault(label, _Bucket()).add(*args)
        total.add(*args)
        sec = ts // 1000
        slot = per_second.setdefault(sec, [0, 0, 0, 0])
        slot[0] += 1
        slot[1] += 0 if ok else 1
        slot[2] += elapsed
        slot[3] = max(slot[3], _int(r.get("allThreads")))
    timeline = []
    if per_second:
        first, last = min(per_second), max(per_second)
        width = max(1, math.ceil((last - first + 1) / timeline_points))
        for start in range(first, last + 1, width):
            secs = [per_second[s] for s in range(start, start + width) if s in per_second]
            samples = sum(s[0] for s in secs)
            timeline.append({
                "t": start - first,
                "throughput": round(samples / width, 2),
                "errors": sum(s[1] for s in secs),
                "avg_ms": round(sum(s[2] for s in secs) / samples, 1) if samples else 0.0,
                "threads": max((s[3] for s in secs), default=0),
            })
    overall = total.summary(TOTAL)
    return {
        "overall": overall,
        "requests": [labels[k].summary(k) for k in sorted(labels)],
        "timeline": timeline,
        "duration_s": round(((total.last or 0) - (total.first or 0)) / 1000.0, 1),
        "peak_threads": max((p["threads"] for p in timeline), default=0),
    }


def parse_file(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        return compute(csv.DictReader(fh))


class LiveTail:
    """Incrementally reads a JTL being written by JMeter for live progress."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.header: list[str] | None = None
        self.samples = 0
        self.errors = 0
        self.elapsed_sum = 0
        self.threads = 0
        self.window: list[tuple[int, int]] = []  # (timestamp, elapsed) for the last poll

    def poll(self) -> dict[str, Any]:
        lines, self.offset = read_rows(self.path, self.offset)
        if lines and self.header is None:
            self.header = next(csv.reader([lines[0]]))
            lines = lines[1:]
        batch = []
        if self.header:
            for row in csv.DictReader(lines, fieldnames=self.header):
                elapsed = _int(row.get("elapsed"))
                self.samples += 1
                self.elapsed_sum += elapsed
                self.errors += 0 if (row.get("success") or "").lower() == "true" else 1
                self.threads = _int(row.get("allThreads")) or self.threads
                batch.append((_int(row.get("timeStamp")), elapsed))
        span = (max(t for t, _ in batch) - min(t for t, _ in batch)) / 1000.0 if len(batch) > 1 else 0
        return {
            "samples": self.samples,
            "errors": self.errors,
            "error_pct": round(self.errors * 100.0 / self.samples, 2) if self.samples else 0.0,
            "avg_ms": round(self.elapsed_sum / self.samples, 1) if self.samples else 0.0,
            "threads": self.threads,
            "recent_throughput": round(len(batch) / span, 1) if span else None,
        }
