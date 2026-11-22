"""23.14.1: TFS is fast again with personal PATs and projects, and the Dashboard never waits forever for TFS.

* one open HTTP client per server / project / PAT (connections and the TLS context are reused, not made per call)
* the signed-in person's PAT and project are read from the credential store once, not on every call
* concurrent requests for the same TFS figure share one TFS call (the Dashboard asks twice at the same moment)
* the Dashboard waits a few seconds for TFS, shows everything else, and fills the TFS figures in when they arrive
* Connection Test makes one call fewer and runs the independent checks together
* background work (test design, scripts, voice, reports, defects) keeps the signed-in person's PAT and project
"""

from __future__ import annotations

import threading
import time

import anyio.to_thread
import httpx
import pytest
from fastapi.testclient import TestClient

from qa_platform.auth import User, carry_user, reset_current_user, set_current_user
from qa_platform.credentials import MemoryBackend
from qa_platform.main import REQUEST_THREADS, create_app
from qa_platform.reports import analytics as analytics_mod
from qa_platform.tfs import client as client_mod
from tests.fake_ado import PAT, FakeAzureDevOps


class CountingSecrets(MemoryBackend):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def get(self, key):
        self.reads += 1
        return super().get(key)


@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps()
    state = {"delay": 0.0}

    def handle(request: httpx.Request) -> httpx.Response:
        if state["delay"]:
            time.sleep(state["delay"])
        return ado.handle(request)

    secrets = CountingSecrets()
    app = create_app(paths, secret_backend=secrets, tfs_transport=httpx.MockTransport(handle))
    with TestClient(app) as c:
        rt = app.state.runtime
        rt.tfs.save_profile("online", url="https://dev.azure.com/acme", project="Shop", pat=PAT, make_active=True)
        yield c, ado, rt, secrets, state, app


def _person(app, rt, name, project, role="tester"):
    users = app.state.users
    row = users.create(name, f"{name.title()}-pass-2026", role, name.title(), email=f"{name}@acme.ae",
                       tfs_project=project)
    rt.tfs.save_personal(name, "online", pat=PAT)
    return User(row["id"], name, name.title(), role)


def test_one_client_is_reused_for_every_call(env, monkeypatch):
    c, ado, rt, *_ = env
    made = []
    real = client_mod.AzureDevOpsClient.__init__

    def counting(self, *a, **k):
        made.append(1)
        real(self, *a, **k)

    monkeypatch.setattr(client_mod.AzureDevOpsClient, "__init__", counting)
    rt.tfs.invalidate()
    rt.tfs.meta()
    rt.tfs.query(category="requirement")
    rt.tfs.kpis()
    rt.tfs.projects()
    assert len(made) == 1  # before 23.14.1: one new client (TLS context, connection, handshake) per call


def test_tls_context_is_built_once(monkeypatch):
    built = []
    real = client_mod._build_tls_context
    monkeypatch.setattr(client_mod, "_build_tls_context", lambda ca=None: built.append(1) or real(ca))
    client_mod._TLS_CACHE.clear()
    for _ in range(5):
        client_mod.AzureDevOpsClient("https://dev.azure.com/acme", "Shop", PAT).close()
    assert len(built) == 1


def test_personal_pat_and_project_are_not_read_on_every_call(env):
    c, ado, rt, secrets, state, app = env
    maya = _person(app, rt, "maya", "Shop")
    token = set_current_user(maya)
    try:
        rt.tfs.kpis()
        secrets.reads = 0
        for _ in range(20):
            rt.tfs.profile()
            rt.tfs.kpis()
            rt.tfs.meta()
        assert secrets.reads == 0  # was 4 or more Credential Manager reads per call
        assert rt.tfs.profile()["personal"] and rt.tfs.profile()["project"] == "Shop"
    finally:
        reset_current_user(token)


def test_a_changed_pat_or_project_is_used_at_once(env):
    c, ado, rt, secrets, state, app = env
    maya = _person(app, rt, "maya", "Shop")
    token = set_current_user(maya)
    try:
        assert rt.tfs.profile()["project"] == "Shop"
        rt.tfs.save_profile("online", project="Mobile")
        assert rt.tfs.profile()["project"] == "Mobile"  # not the remembered "Shop"
    finally:
        reset_current_user(token)


def test_concurrent_requests_share_one_tfs_call(env):
    c, ado, rt, secrets, state, app = env
    state["delay"] = 0.15
    rt.tfs.invalidate()
    ado.requests.clear()
    barrier = threading.Barrier(4)
    errors = []

    def dashboard():
        try:
            barrier.wait()
            rt.tfs.kpis()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=carry_user(dashboard)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    wiql = [r for r in ado.requests if r.url.path.endswith("/_apis/wit/wiql")]
    types = [r for r in ado.requests if r.url.path.endswith("/_apis/wit/workitemtypes")]
    assert len(types) == 1 and len(wiql) == 4  # 4 counts once, not 4 counts x 4 requests


def test_cache_shares_errors_and_does_not_keep_them():
    from qa_platform.tfs.service import TTLCache

    cache = TTLCache(60)
    calls = []

    def boom():
        calls.append(1)
        time.sleep(0.1)
        raise RuntimeError("TFS down")

    results = []

    def ask():
        try:
            cache.get_or_set("k", boom)
        except RuntimeError as exc:
            results.append(str(exc))

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["TFS down"] * 3 and len(calls) == 1
    assert cache.get_or_set("k", lambda: 5) == 5  # an error is not remembered


def test_dashboard_shows_the_rest_when_tfs_is_slow(env, monkeypatch):
    c, ado, rt, secrets, state, app = env
    monkeypatch.setattr(analytics_mod, "DASHBOARD_TFS_BUDGET", 0.3)
    rt.tfs.invalidate()
    state["delay"] = 0.4
    started = time.monotonic()
    a = c.get("/api/analytics/summary").json()
    s = c.get("/api/dashboard/summary").json()
    assert time.monotonic() - started < 3  # was: every TFS call one after the other, the page waiting for all
    assert a["tfs"]["pending"] and not a["tfs"]["connected"] and "slowly" in a["tfs"]["reason"]
    assert s["tfs"]["pending"] and s["tfs"]["error_code"] == "TFS_PENDING"
    assert "functional" in a and "coverage" in a  # the figures that do not need TFS are there
    state["delay"] = 0.0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:  # the query went on in the background; the next refresh has the figures
        a = c.get("/api/analytics/summary").json()
        if a["tfs"].get("connected"):
            break
        time.sleep(0.2)
    assert a["tfs"]["connected"] and a["tfs"]["requirement"] == 1 and a["acceptance_criteria"]["available"]


def test_dashboard_tfs_figures_are_the_signed_in_persons(env):
    c, ado, rt, secrets, state, app = env
    lina = _person(app, rt, "lina", "Mobile")
    token = set_current_user(lina)
    try:
        ado.requests.clear()
        out = rt.analytics.tfs_counts(5)
        assert out["connected"] is False  # "Mobile" does not exist on the fake server ...
        assert any("/acme/Mobile/" in str(r.url) for r in ado.requests)  # ... but it was asked for, not "Shop"
        assert not any("/acme/Shop/" in str(r.url) for r in ado.requests)
    finally:
        reset_current_user(token)


def test_connection_test_skips_the_home_page_and_still_reports_every_check(env):
    c, ado, rt, *_ = env
    ado.requests.clear()
    r = c.post("/api/tfs/test/online").json()
    assert r["ok"] is True
    assert [x["name"] for x in r["checks"]] == ["Server Reachability", "Authentication", "Organization/Collection",
                                                "Project", "Work Item API", "Test Management API"]
    assert all(x["status"] == "PASS" for x in r["checks"])
    assert not any(req.url.path == "/acme" for req in ado.requests)  # the sign-in page is not fetched any more
    assert len(ado.requests) == 6


def test_connection_test_reports_an_unreachable_server(env):
    c, ado, rt, *_ = env
    ado.unreachable = True
    r = c.post("/api/tfs/test/online").json()
    checks = {x["name"]: x["status"] for x in r["checks"]}
    assert r["ok"] is False and checks["Server Reachability"] == "FAIL" and checks["Authentication"] == "SKIP"


def test_connection_test_reports_a_wrong_pat(env):
    c, ado, rt, *_ = env
    rt.tfs.save_profile("online", pat="expired-pat")
    r = c.post("/api/tfs/test/online").json()
    checks = {x["name"]: x["status"] for x in r["checks"]}
    assert checks["Server Reachability"] == "PASS" and checks["Authentication"] == "FAIL"
    assert checks["Project"] == "SKIP"


def test_background_work_keeps_the_persons_project(env):
    c, ado, rt, secrets, state, app = env
    lina = _person(app, rt, "lina", "Mobile")
    seen = {}
    token = set_current_user(lina)
    try:
        carried = threading.Thread(target=carry_user(lambda: seen.setdefault("carried", rt.tfs.profile()["project"])))
        plain = threading.Thread(target=lambda: seen.setdefault("plain", rt.tfs.profile()["project"]))
        carried.start(), plain.start()
        carried.join(), plain.join()
    finally:
        reset_current_user(token)
    assert seen == {"carried": "Mobile", "plain": "Shop"}  # a plain thread falls back to the shared project


def test_test_design_job_runs_as_the_person(env, monkeypatch):
    """The design API starts its job with run_in_executor; the job now sees the person who started it."""
    import qa_platform.api.design as design_api

    src = open(design_api.__file__, encoding="utf-8").read()
    assert "run_in_executor(None, work)" not in src and src.count("run_in_executor(None, carry_user(work))") == 3


def test_request_thread_pool_is_wider(env):
    c, *_ = env

    async def tokens():
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert c.portal.call(tokens) >= REQUEST_THREADS


def test_status_light_is_kept_per_person(env):
    c, ado, rt, secrets, state, app = env
    lina = _person(app, rt, "lina", "Mobile")
    shared = rt.health.summary(force=True)["items"][0]
    token = set_current_user(lina)
    try:
        mine = rt.health.summary()["items"][0]
    finally:
        reset_current_user(token)
    assert shared["status"] == "PASS" and "Shop" in shared["detail"]
    assert "Shop" not in mine.get("detail", "")  # Lina's light is about her own project, not the cached shared one
