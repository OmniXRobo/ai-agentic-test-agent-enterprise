"""Performance test plan model and load profiles.

A plan is a list of HTTP requests plus a load profile. ``thread_groups`` turns
the profile for each test type (load, stress, spike, soak) into plain JMeter
thread groups, so the generated JMX needs no JMeter plugins.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Kind = Literal["load", "stress", "spike", "soak"]


class HttpRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    url: str
    body: str | None = None
    headers: dict[str, str] = {}
    expect_status: str | None = Field(default=None, description="Regex for the expected response code, e.g. 2\\d\\d")
    expect_text: str | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("Request URLs must start with http:// or https://")
        return v


class Sla(BaseModel):
    avg_ms: float | None = Field(default=None, gt=0)
    p90_ms: float | None = Field(default=None, gt=0)
    p95_ms: float | None = Field(default=None, gt=0)
    p99_ms: float | None = Field(default=None, gt=0)
    max_error_pct: float | None = Field(default=None, ge=0, le=100)
    min_throughput: float | None = Field(default=None, gt=0, description="Requests per second, whole test")


class LoadProfile(BaseModel):
    users: int = Field(default=10, ge=1, le=100000)
    ramp_up_s: int = Field(default=30, ge=0, le=86400)
    duration_s: int = Field(default=300, ge=10, le=7 * 86400)
    think_time_ms: int = Field(default=1000, ge=0, le=600000)
    # stress: add `users` in this many equal steps
    steps: int = Field(default=4, ge=1, le=50)
    # spike: a burst of spike_users on top of `users`
    spike_users: int = Field(default=50, ge=1, le=100000)
    spike_at_s: int = Field(default=120, ge=0)
    spike_duration_s: int = Field(default=60, ge=5)


class ThreadGroupSpec(BaseModel):
    name: str
    users: int
    ramp_up_s: int
    delay_s: int
    duration_s: int
    duration_known: bool = True  # 23.3.23: False when a script's thread group runs a number of loops instead


class PerfPlan(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=200)
    kind: Kind = "load"
    requests: list[HttpRequest] = []
    profile: LoadProfile = LoadProfile()
    sla: Sla = Sla()
    source: str | None = None  # e.g. "discovery DISC-...", "manual", "import plan.jmx"
    # 23.3.23: when set, the plan is a script (JMX) edited in the script editor. The JMX is then what runs, and
    # `requests` and `profile` are a read-only summary of it, kept for lists, checks and reports.
    script: str | None = None
    script_version: int = 0
    # 23.3.24: values JMeter reads as ${__P(name)} from the Windows Credential Manager at run time (never in the JMX)
    secret_names: list[str] = []
    recording: dict | None = None  # what the recorder kept, left out and correlated
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def is_script(self) -> bool:
        return bool(self.script)

    @model_validator(mode="after")
    def _spike_window(self) -> "PerfPlan":
        if not self.requests and not self.script:
            raise ValueError("A plan needs at least one request.")
        if self.script:
            return self
        p = self.profile
        if self.kind == "spike" and p.spike_at_s + p.spike_duration_s > p.duration_s:
            raise ValueError("The spike must end before the test duration.")
        return self

    def thread_groups(self) -> list[ThreadGroupSpec]:
        if self.script:
            from .script import thread_group_specs
            return [g for g in thread_group_specs(self.script) if g.users or g.duration_s] or [
                ThreadGroupSpec(name="(none)", users=0, ramp_up_s=0, delay_s=0, duration_s=0, duration_known=False)]
        p = self.profile
        if self.kind == "stress":
            base, extra = divmod(p.users, p.steps)
            step_len = max(1, p.duration_s // p.steps)
            groups = []
            for i in range(p.steps):
                users = base + (1 if i < extra else 0)
                if users == 0:
                    continue
                delay = i * step_len
                groups.append(ThreadGroupSpec(name=f"Step {i + 1}", users=users, ramp_up_s=min(p.ramp_up_s, step_len),
                                              delay_s=delay, duration_s=p.duration_s - delay))
            return groups
        if self.kind == "spike":
            return [
                ThreadGroupSpec(name="Baseline", users=p.users, ramp_up_s=p.ramp_up_s, delay_s=0, duration_s=p.duration_s),
                ThreadGroupSpec(name="Spike", users=p.spike_users, ramp_up_s=min(5, p.spike_duration_s),
                                delay_s=p.spike_at_s, duration_s=p.spike_duration_s),
            ]
        return [ThreadGroupSpec(name="Users", users=p.users, ramp_up_s=p.ramp_up_s, delay_s=0, duration_s=p.duration_s)]

    def peak_users(self) -> int:
        if self.script:
            from .script import analyze_cached
            return analyze_cached(self.script)["summary"]["peak_users"]
        return sum(g.users for g in self.thread_groups())

    def total_duration_s(self) -> int | None:
        """How long the test lasts, or None when a script's duration depends on loop counts."""
        if self.script:
            from .script import analyze_cached
            return analyze_cached(self.script)["summary"]["total_duration_s"]
        return max(g.delay_s + g.duration_s for g in self.thread_groups())


def new_plan_id() -> str:
    return "PERF-" + uuid.uuid4().hex[:10].upper()


# ---- 23.3.22: scenarios, environments, run states -----------------------------------------------------------------

# Execution state (what happened to the process) is kept apart from the performance verdict (what the numbers say).
RUN_STATES = ("QUEUED", "VALIDATING", "RUNNING", "STOPPING", "COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED",
              "SKIPPED")
ACTIVE_STATES = ("VALIDATING", "RUNNING", "STOPPING")
# PASS / FAIL against the targets; NOT_SPECIFIED when the plan has no targets (measurements are still reported);
# INCONCLUSIVE when the run did not complete, so its partial numbers cannot pass or fail it.
VERDICTS = ("PASS", "FAIL", "NOT_SPECIFIED", "INCONCLUSIVE")
TEST_TYPES = ("load", "stress", "spike", "soak")


class PerfEnvironment(BaseModel):
    """A target environment and the load this application may put on it."""
    id: str = ""
    name: str = Field(min_length=1, max_length=120)
    base_url: str = ""
    allowed_hosts: list[str] = Field(default=[], description="Host names requests may go to; empty means any host")
    max_users: int = Field(default=50, ge=1, le=100000)
    max_duration_s: int | None = Field(default=None, ge=10, le=7 * 86400)
    third_party: bool = Field(default=False, description="A public site this team does not own: keep load minimal")
    notes: str = ""
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("allowed_hosts")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        out = []
        for h in v:
            h = h.strip().lower()
            if "://" in h:
                h = h.split("://", 1)[1]
            h = h.split("/", 1)[0].split(":", 1)[0]
            if h and h not in out:
                out.append(h)
        return out

    @field_validator("base_url")
    @classmethod
    def _base(cls, v: str) -> str:
        v = v.strip()
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("The base URL must start with http:// or https://")
        return v


class PerfScenario(BaseModel):
    """A performance scenario: what is tested, where, against which story, with which script (plan)."""
    id: str = ""
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    project: str = ""
    environment_id: str | None = None
    plan_id: str | None = None  # the script; versions arrive in 23.3.23
    user_story_id: str = ""
    acceptance_criteria: str = ""
    test_suite_id: str = ""
    functional_script_id: str = ""  # the related functional (browser) script, kept separate from the load script
    tags: list[str] = []
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None


def new_scenario_id() -> str:
    return "SCN-" + uuid.uuid4().hex[:10].upper()


def new_environment_id() -> str:
    return "ENV-" + uuid.uuid4().hex[:8].upper()
