"""23.3.22: load-generator health during a run (this PC: CPU, memory, disk, the JMeter process).

A saturated generator distorts response times, so the run records when CPU or memory stayed high and when disk space
ran low. Needs psutil; without it the run says the generator was not monitored instead of guessing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

try:  # pinned in requirements; the patch installs it
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - depends on the PC
    psutil = None

CPU_LIMIT = 85.0  # % of all cores
MEM_LIMIT = 90.0  # % of physical memory
DISK_MIN_MB = 512
SUSTAIN = 3  # consecutive samples (at 2 s) before CPU counts as saturated
MAX_POINTS = 1800


def available() -> bool:
    return psutil is not None


class GeneratorMonitor:
    def __init__(self, folder: Path, interval: float = 2.0) -> None:
        self.folder = folder
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.flags: dict[str, dict[str, Any]] = {}
        self._pid: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()
        self._high_cpu = 0

    def start(self, pid: int | None) -> None:
        self._pid = pid
        if psutil is None:
            return
        psutil.cpu_percent(None)  # prime the counter
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._loop, name="perf-generator", daemon=True)
        self._thread.start()

    def _proc_tree(self) -> tuple[float, float]:
        """CPU % and resident MB of the JMeter process and its children (jmeter.bat starts java as a child)."""
        if not self._pid:
            return 0.0, 0.0
        try:
            root = psutil.Process(self._pid)
            procs = [root, *root.children(recursive=True)]
        except psutil.Error:
            return 0.0, 0.0
        cpu = mem = 0.0
        for p in procs:
            try:
                cpu += p.cpu_percent(None)
                mem += p.memory_info().rss / (1024 * 1024)
            except psutil.Error:
                continue
        return cpu, mem

    def sample(self) -> dict[str, Any]:
        cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        disk_mb = psutil.disk_usage(str(self.folder)).free / (1024 * 1024)
        jcpu, jmem = self._proc_tree()
        point = {"t": round(time.monotonic() - self._t0, 1), "cpu_pct": round(cpu, 1), "mem_pct": round(vm.percent, 1),
                 "disk_free_mb": round(disk_mb), "jmeter_cpu_pct": round(jcpu, 1), "jmeter_mem_mb": round(jmem)}
        self._high_cpu = self._high_cpu + 1 if cpu >= CPU_LIMIT else 0
        if self._high_cpu >= SUSTAIN:
            self._flag("cpu", f"Generator CPU at or above {CPU_LIMIT:.0f}% for {SUSTAIN * self.interval:.0f}+ s",
                       point, "cpu_pct")
        if vm.percent >= MEM_LIMIT:
            self._flag("memory", f"Generator memory at or above {MEM_LIMIT:.0f}%", point, "mem_pct")
        if disk_mb < DISK_MIN_MB:
            self._flag("disk", f"Less than {DISK_MIN_MB} MB disk space left for results", point, "disk_free_mb")
        return point

    def _flag(self, key: str, text: str, point: dict[str, Any], metric: str) -> None:
        f = self.flags.get(key)
        if f is None:
            self.flags[key] = {"id": key, "message": text, "first_at_s": point["t"], "last_at_s": point["t"],
                               "count": 1, "worst": point[metric]}
        else:
            f["last_at_s"] = point["t"]
            f["count"] += 1
            f["worst"] = min(f["worst"], point[metric]) if metric == "disk_free_mb" else max(f["worst"], point[metric])

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.samples.append(self.sample())
            except Exception:  # monitoring must never break a run
                continue
            if len(self.samples) > MAX_POINTS:  # keep long soak runs bounded: drop every other old point
                self.samples = self.samples[:MAX_POINTS // 2:2] + self.samples[MAX_POINTS // 2:]

    def latest(self) -> dict[str, Any] | None:
        return self.samples[-1] if self.samples else None

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        if psutil is None:
            return {"available": False, "reason": "psutil is not installed, so the load generator was not monitored.",
                    "saturated": None, "flags": [], "samples": []}
        s = self.samples
        stats: dict[str, Any] = {}
        if s:
            stats = {"cpu_avg_pct": round(sum(p["cpu_pct"] for p in s) / len(s), 1),
                     "cpu_max_pct": max(p["cpu_pct"] for p in s),
                     "mem_max_pct": max(p["mem_pct"] for p in s),
                     "disk_min_free_mb": min(p["disk_free_mb"] for p in s),
                     "jmeter_mem_max_mb": max(p["jmeter_mem_mb"] for p in s)}
        return {"available": True, "interval_s": self.interval, "cpu_limit_pct": CPU_LIMIT, "mem_limit_pct": MEM_LIMIT,
                "saturated": bool(self.flags), "flags": list(self.flags.values()), "stats": stats, "samples": s}


def find_run_process(pid: int | None, folder: Path) -> Any:
    """The JMeter process this application started for a run, only if it is still that process: its command line must
    name the run's folder or plan. Never matches unrelated Java processes."""
    if psutil is None or not pid:
        return None
    try:
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline())
        cwd = ""
        try:
            cwd = p.cwd()
        except psutil.Error:
            pass
    except psutil.Error:
        return None
    marker = str(folder)
    if marker in cmd or (cwd and Path(cwd).resolve() == folder.resolve()):
        return p
    return None


def kill_tree(proc: Any) -> int:
    """End a process found by find_run_process and its children. Returns how many were ended."""
    if psutil is None or proc is None:
        return 0
    try:
        procs = [*proc.children(recursive=True), proc]
    except psutil.Error:
        procs = [proc]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            continue
    psutil.wait_procs(procs, timeout=10)
    return len(procs)
