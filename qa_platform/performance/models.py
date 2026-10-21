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


class PerfPlan(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=200)
    kind: Kind = "load"
    requests: list[HttpRequest] = Field(min_length=1)
    profile: LoadProfile = LoadProfile()
    sla: Sla = Sla()
    source: str | None = None  # e.g. "discovery DISC-..." or "manual"
    created_at: str | None = None
    updated_at: str | None = None

    @model_validator(mode="after")
    def _spike_window(self) -> "PerfPlan":
        p = self.profile
        if self.kind == "spike" and p.spike_at_s + p.spike_duration_s > p.duration_s:
            raise ValueError("The spike must end before the test duration.")
        return self

    def thread_groups(self) -> list[ThreadGroupSpec]:
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
        return sum(g.users for g in self.thread_groups())


def new_plan_id() -> str:
    return "PERF-" + uuid.uuid4().hex[:10].upper()
