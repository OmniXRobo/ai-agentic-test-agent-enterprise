"""An in-memory TFS project for tests: the TfsService methods the test plan and suite features use.

install(rt) replaces those methods on the running app's TfsService, so the voice agent, the API and the pages all
work against the same simulated project. Every write is kept, so tests can check what TFS would hold.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

from qa_platform.errors import QAError

PROJECT = "Shop"
ITERATIONS = [PROJECT, f"{PROJECT}\\Sprint 23", f"{PROJECT}\\Sprint 24", f"{PROJECT}\\Sprint 25", f"{PROJECT}\\Sprint 26",
              f"{PROJECT}\\Release 1\\Sprint 3", f"{PROJECT}\\Release 2\\Sprint 3"]


class FakeTfs:
    def __init__(self) -> None:
        self.ids = itertools.count(500)
        self.plans: dict[int, dict[str, Any]] = {}
        self.suites: dict[int, list[dict[str, Any]]] = {}
        self.writes: list[tuple] = []
        self.fail_next: str | None = None
        for name, it, state in (("Shop - Sprint 23 - Test Plan", "Sprint 23", "Inactive"),
                                ("Shop - Sprint 24 - Test Plan", "Sprint 24", "Active"),
                                ("Checkout Regression", "Sprint 24", "Active"),
                                ("Shop - Release 1 - Sprint 3 - Test Plan", "Release 1\\Sprint 3", "Active")):
            self._new_plan(name, f"{PROJECT}\\{it}", state)

    def _new_plan(self, name, iteration, state="Active", description=None):
        pid = next(self.ids)
        root = next(self.ids)
        self.plans[pid] = {"id": pid, "name": name, "state": state, "iteration": iteration or PROJECT, "area_path": PROJECT,
                           "owner": None, "start_date": None, "end_date": None, "root_suite_id": root, "description": description}
        self.suites[pid] = [{"id": root, "name": name, "suite_type": "staticTestSuite", "parent_id": None, "test_case_count": 0}]
        return self.plans[pid]

    def _check(self, op):
        if self.fail_next == op:
            self.fail_next = None
            raise QAError("TFS_UNAVAILABLE", "TFS is not reachable (simulated).", status=502)

    # ---- the TfsService surface ----
    def profile(self, mode=None):
        return {"mode": "offline", "label": "TFS (offline)", "url": "http://tfs.test/tfs/DefaultCollection", "project": PROJECT,
                "api_version": "6.0", "verify_tls": True, "has_pat": True}

    def active_mode(self):
        return "offline"

    def meta(self):
        return {"project": PROJECT, "iterations": list(ITERATIONS), "areas": [PROJECT], "categories": {}, "states": {},
                "closed_states": [], "users": []}

    def invalidate(self):
        pass

    def test_plans(self):
        return [copy.deepcopy(p) for p in sorted(self.plans.values(), key=lambda p: p["id"])]

    def create_test_plan(self, name, *, area_path=None, iteration=None, description=None):
        self._check("create_plan")
        p = self._new_plan(name, iteration, description=description)
        self.writes.append(("create_plan", p["id"], name, iteration))
        return {"id": p["id"], "name": name, "root_suite_id": p["root_suite_id"], "created": True}

    def ensure_test_plan(self, name, *, area_path=None, iteration=None, description=None):
        existing = next((p for p in self.plans.values() if p["name"].strip().lower() == name.strip().lower()), None)
        if existing:
            return {"id": existing["id"], "name": existing["name"], "root_suite_id": existing["root_suite_id"], "created": False}
        return self.create_test_plan(name, area_path=area_path, iteration=iteration, description=description)

    def update_test_plan(self, plan_id, *, name=None, iteration=None, area_path=None, state=None, description=None):
        self._check("update_plan")
        p = self.plans[int(plan_id)]
        if name is not None:
            p["name"] = name.strip()
        if iteration is not None:
            p["iteration"] = iteration or PROJECT
        if state:
            p["state"] = state
        if description is not None:
            p["description"] = description
        self.writes.append(("update_plan", plan_id, name, iteration, state))
        return copy.deepcopy(p)

    def delete_test_plan(self, plan_id):
        self.plans.pop(int(plan_id)); self.suites.pop(int(plan_id), None)
        self.writes.append(("delete_plan", plan_id))

    def test_suites(self, plan_id):
        if int(plan_id) not in self.suites:
            raise QAError("TFS_NOT_FOUND", f"Test plan {plan_id} does not exist.", status=404)
        return copy.deepcopy(self.suites[int(plan_id)])

    def ensure_suites(self, plan_id, names, root_suite_id=None):
        self._check("suites")
        suites = self.suites[int(plan_id)]
        root = root_suite_id or next(s["id"] for s in suites if not s["parent_id"])
        out = []
        for n in names:
            s = next((s for s in suites if s["parent_id"] == root and s["name"].lower() == n.lower()), None)
            if s:
                out.append({"id": s["id"], "name": s["name"], "created": False})
            else:
                sid = next(self.ids)
                suites.append({"id": sid, "name": n, "suite_type": "staticTestSuite", "parent_id": root, "test_case_count": 0})
                self.writes.append(("create_suite", plan_id, n))
                out.append({"id": sid, "name": n, "created": True})
        return out

    def update_test_suite(self, plan_id, suite_id, name):
        s = next(s for s in self.suites[int(plan_id)] if s["id"] == int(suite_id))
        s["name"] = name
        return copy.deepcopy(s)

    def delete_test_suite(self, plan_id, suite_id):
        self._check("delete_suite")
        self.suites[int(plan_id)] = [s for s in self.suites[int(plan_id)] if s["id"] != int(suite_id)]
        self.writes.append(("delete_suite", plan_id, suite_id))

    def suite_test_cases(self, plan_id, suite_id):
        return [{"id": i, "title": f"Test case {i}"} for i in getattr(self, "suite_cases", {}).get(int(suite_id), [])]


METHODS = ("profile", "active_mode", "meta", "invalidate", "test_plans", "create_test_plan", "ensure_test_plan",
           "update_test_plan", "delete_test_plan", "test_suites", "ensure_suites", "update_test_suite",
           "delete_test_suite", "suite_test_cases")


def install(rt) -> FakeTfs:
    fake = FakeTfs()
    for m in METHODS:
        setattr(rt.tfs, m, getattr(fake, m))
    return fake


# ---- simulated design engines (test cases and test data), for milestone 5 --------------------------------------

STORIES = {f"{PROJECT}\\Sprint 25": [{"id": 9001, "title": "Login with email"}, {"id": 9002, "title": "Reset password"}]}
CASES = {77: "Existing login case", 105: "Login with valid credentials", 106: "Login with wrong password",
         107: "Login with empty password"}


def install_design(rt, fake: FakeTfs):
    cases = rt.design.cases
    gen: dict[str, dict] = {}
    known = dict(CASES)  # test cases TFS holds, including ones saved during the test
    fake.generated, fake.published, fake.data_calls = gen, [], []

    def sprint_stories(iteration):
        return [dict(s) for s in STORIES.get(iteration, [])]

    def run(plan_id, suite_id, story_ids, *, testing_type=None, max_cases=None, cases_per_criterion=2, run_id=None,
            thorough_review=False):
        suite = cases.suite_info(plan_id, suite_id, testing_type)
        stories = []
        titles = {s["id"]: s["title"] for v in STORIES.values() for s in v}
        for sid in story_ids:
            tcs = [{"ref": f"TC{n}", "title": f"{suite['testing_type_name']} case {n} for US{sid}", "ac_ids": ["AC1"],
                    "condition_ids": [], "priority": 2, "risk": "medium", "scenario": "positive", "automation": "candidate",
                    "preconditions": None, "steps": [{"action": "Open the page", "expected": "It opens"}], "test_data": [],
                    "dependencies": [], "gaps": []} for n in (1, 2)]
            per_case = {"TC2": [{"level": "error", "rule": "expected", "message": "missing expected result"}]} if sid == story_ids[-1] else {}
            v = {"per_case": per_case, "errors": len(per_case), "warnings": 0,
                 "duplicates": [{"ref": "TC1", "kind": "existing", "duplicate_of": 77}] if sid == story_ids[0] else []}
            stories.append({"story": {"id": sid, "title": titles.get(sid, "")}, "test_cases": tcs, "validation": v,
                            "criteria": [{"id": "AC1", "text": "The user can sign in"}], "coverage": [], "impact": [],
                            "limitations": [], "clarifications": [], "not_applicable": [], "notes": []})
        result = {"run_id": run_id, "plan_id": plan_id, "suite": suite, "stories": stories, "meta": {}}
        gen[run_id] = result
        rt.runs.finish(run_id, "PASSED", {})
        return result

    def get(run_id):
        if run_id not in gen:
            raise QAError("GENERATION_NOT_FOUND", f"No test case generation {run_id}.", status=404)
        return gen[run_id]

    def publish_ready(run_id):
        result = gen[run_id]
        created, held = [], 0
        for st in result["stories"]:
            errs = st["validation"].get("per_case") or {}
            for c in st["test_cases"]:
                if c.get("published_id"):
                    continue
                if c["ref"] in errs:
                    held += 1
                    continue
                dup = next((d for d in st["validation"].get("duplicates") or [] if d["ref"] == c["ref"]), None)
                c["published_id"] = dup["duplicate_of"] if dup else next(fake.ids)
                known.setdefault(c["published_id"], c["title"])
                created.append({"id": c["published_id"], "action": "reused" if dup else "created"})
        fake.published.append(run_id)
        fake.suite_cases = getattr(fake, "suite_cases", {})
        fake.suite_cases.setdefault(int(result["suite"]["id"]), []).extend(c["id"] for c in created)
        return {"created": created, "held": held, "suite": result["suite"]["name"]}

    def detail(item_id):
        item_id = int(item_id)
        if item_id not in known:
            raise QAError("TFS_NOT_FOUND", f"Work item {item_id} does not exist.", status=404)
        steps = ('<steps><step id="1" type="ActionStep"><parameterizedString>Open the login page</parameterizedString>'
                 '<parameterizedString></parameterizedString></step><step id="2" type="ValidateStep"><parameterizedString>'
                 'Sign in</parameterizedString><parameterizedString>The dashboard opens</parameterizedString></step></steps>')
        return {"id": item_id, "type": "Test Case", "title": known[item_id], "steps": steps}

    def generate(test_case_ids, *, seed=None, max_rows=12, run_id=None):
        fake.data_calls.append((list(test_case_ids), max_rows))
        return {"results": [{"test_case_id": tc, "rows": list(range(min(max_rows, 3))), "gaps": ["no password policy"] if tc == 107 else []}
                            for tc in test_case_ids]}

    cases.sprint_stories, cases.run, cases.get, cases.publish_ready = sprint_stories, run, get, publish_ready
    rt.tfs.detail = detail
    rt.design.datasets.generate = generate


# ---- simulated Script Studio (AI-Driven Automation), for milestone 6 ------------------------------------------


class FakeStudio:
    """The ScriptStudio surface the voice agent uses. Scripts, jobs and runs are kept so tests can check them."""

    def __init__(self, rt, root):
        self.rt, self.root = rt, root
        self.scripts: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.requests: list = []
        self.runs: list[tuple] = []
        self.outcome: dict[int, str] = {}  # test case -> PASSED | FAILED for its next run
        self.fail_generation: set[int] = set()
        self.regen_unchanged = False
        self.ids = itertools.count(1)

    class _Ws:
        def __init__(self, root):
            self.root = root

        def exists(self, rel):
            return True

    def workspace(self):
        return self._Ws(self.root)

    def inventory(self):
        return [dict(r) for r in self.scripts.values()]

    def _record(self, sid):
        if sid not in self.scripts:
            raise QAError("SCRIPT_NOT_FOUND", f"Script {sid} does not exist.", status=404)
        return dict(self.scripts[sid])

    def start_job(self, req, *, action="generate", script_id=None):
        self.requests.append((action, req))
        jid = f"FWJ-{next(self.ids):04d}"
        items = []
        for sel in req.selections:
            for tc in sel.test_case_ids or []:
                sid = f"FWS-{tc}"
                if tc in self.fail_generation:
                    items.append({"test_case_id": str(tc), "status": "FAILED", "script_id": None,
                                  "error": "The element inventory has no field for step 2."})
                elif action == "regenerate":
                    st = "UNCHANGED" if self.regen_unchanged else "UPDATED"
                    if st == "UPDATED":
                        self.scripts[sid]["version"] += 1
                    items.append({"test_case_id": str(tc), "status": st, "script_id": sid})
                elif sid in self.scripts and req.on_existing == "skip":
                    items.append({"test_case_id": str(tc), "status": "SKIPPED", "script_id": sid})
                else:
                    self.scripts[sid] = {"id": sid, "framework": True, "test_case_id": str(tc),
                                         "test_case_title": CASES.get(tc, f"Test case {tc}"), "user_story_id": "9001",
                                         "file": f"tests/test_tc{tc}.py", "data_file": f"test_data/tc{tc}.json",
                                         "base_url": req.base_url, "application": "Shop", "environment": None,
                                         "version": 1, "last_run_id": None, "last_result": None}
                    items.append({"test_case_id": str(tc), "status": "GENERATED", "script_id": sid})
        self.jobs[jid] = {"id": jid, "status": "COMPLETED", "items": items, "error": None,
                          "summary": {"completed": sum(i["status"] != "FAILED" for i in items),
                                      "failed": sum(i["status"] == "FAILED" for i in items)}}
        return jid

    def job(self, jid):
        return self.jobs[jid]

    def regenerate(self, sid, base_url=None):
        from qa_platform.automation.framework.studio import JobRequest
        rec = self.scripts[sid]
        return self.start_job(JobRequest(selections=[{"test_case_ids": [int(rec["test_case_id"])]}],
                                         base_url=rec["base_url"], on_existing="regenerate"), action="regenerate",
                              script_id=sid)

    def run(self, sid, *, combination=None, headless=None, background=True):
        rec = self.scripts[sid]
        tc = int(rec["test_case_id"])
        status = self.outcome.get(tc, "PASSED")
        run_id = f"RUN-{next(self.ids):04d}"
        pb = self.rt.settings.section("playback")
        self.runs.append((sid, headless, pb.get("browser") or "chromium", pb.get("timeout_seconds")))
        steps = [{"index": 0, "description": "Open the login page", "status": "PASSED"},
                 {"index": 1, "description": "Click Sign in", "status": status}]
        result = {"run_id": run_id, "script_id": sid, "status": status, "steps": steps, "healed": [],
                  "failed_step": 1 if status == "FAILED" else None,
                  "error": "Locator #sign-in was not found within 10000 ms" if status == "FAILED" else None,
                  "failure_analysis": {"summary": "the Sign in button changed its id"} if status == "FAILED" else None}
        rec["last_run_id"], rec["last_result"] = run_id, status
        rec.setdefault("results", {})[run_id] = result
        return result

    def run_result(self, sid, run_id):
        return {**self.scripts[sid]["results"][run_id], "healing": [], "running": False}


def install_studio(rt, root):
    studio = FakeStudio(rt, root)
    rt.voice.studio = studio
    rt.automation.discovery.list = lambda limit=20: [{"base_url": "https://shop.test/"}]
    return studio
