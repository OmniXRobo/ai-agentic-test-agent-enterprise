"""Test Design: requirements -> plan -> test cases -> test data -> TFS.

Each step is done by its own agent, so the Agent Workflow shows exactly which
agent is working. All content comes from the user story in TFS and the
On-Premise AI; nothing is invented when either is unavailable (the agent fails
with the reason instead).
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..agents.base import BaseAgent, new_run_id
from ..ai.gateway import AIGateway
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus
from ..runs import RunStore
from ..storage.db import Database, dumps, loads
from ..tfs.service import TfsService
from . import models

_TAG = re.compile(r"<[^>]+>")


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h\d)\s*/?>", "\n", value)
    text = re.sub(r"(?i)<\s*li[^>]*>", "- ", text)
    text = html.unescape(_TAG.sub("", text))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def split_criteria(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if line:
            items.append(line)
    return items


def steps_xml(steps: list[models.TestStep]) -> str:
    """Azure DevOps Microsoft.VSTS.TCM.Steps format."""
    parts = []
    for index, step in enumerate(steps, start=2):
        action = html.escape(f"<P>{html.escape(step.action)}</P>")
        expected = html.escape(f"<P>{html.escape(step.expected)}</P>")
        parts.append(
            f'<step id="{index}" type="ValidateStep"><parameterizedString isformatted="true">{action}'
            f'</parameterizedString><parameterizedString isformatted="true">{expected}</parameterizedString>'
            "<description/></step>"
        )
    return f'<steps id="0" last="{len(steps) + 1}">' + "".join(parts) + "</steps>"


STORY_CONTEXT = """User story #{id}: {title}
State: {state}; Iteration: {iteration}

Description:
{description}

Acceptance criteria:
{criteria}"""


class RequirementsAgent(BaseAgent):
    agent_id = "requirements"


class TestPlanningAgent(BaseAgent):
    agent_id = "test_planning"


class TestCaseDesignAgent(BaseAgent):
    agent_id = "test_case_design"


class TestDataAgent(BaseAgent):
    agent_id = "test_data"


class TestSuiteDesignAgent(BaseAgent):
    agent_id = "test_suite_design"


class DesignService:
    def __init__(self, bus: EventBus, db: Database, runs: RunStore, tfs: TfsService, ai: AIGateway) -> None:
        self.db = db
        self.runs = runs
        self.tfs = tfs
        self.ai = ai
        self.requirements = RequirementsAgent(bus)
        self.planning = TestPlanningAgent(bus)
        self.case_design = TestCaseDesignAgent(bus)
        self.data = TestDataAgent(bus)
        self.suite = TestSuiteDesignAgent(bus)

    # ---- pipeline ----------------------------------------------------------------

    def run(self, user_story_id: int, run_id: str | None = None, *, max_cases: int = 12) -> dict[str, Any]:
        run_id = run_id or new_run_id("DESIGN")
        sid = str(user_story_id)
        self.runs.start(run_id, "design", f"Test design for User Story #{sid}", user_story_id=sid)
        result: dict[str, Any] = {"run_id": run_id, "user_story_id": user_story_id}
        try:
            story = self._read_story(user_story_id, run_id)
            result["story"] = story
            context = STORY_CONTEXT.format(
                id=story["id"], title=story["title"], state=story["state"], iteration=story["iteration"],
                description=story["description"],
                criteria="\n".join(f"- {c}" for c in story["criteria"]) or "(none)")

            with self.requirements.activity(f"Analyzing requirements of User Story #{sid}", run_id=run_id,
                                            user_story_id=sid) as act:
                analysis = self.ai.complete_json(
                    "You are a senior QA requirements analyst. Extract atomic, testable requirements from the user "
                    "story. Use only what the story says; list unclear points as ambiguities instead of guessing.",
                    context, models.RequirementsAnalysis)
                act.complete(f"Requirements extracted: {len(analysis.requirements)}",
                             requirements=len(analysis.requirements))
                act.event(names.REQUIREMENTS_COMPLETED, f"Requirements extracted: {len(analysis.requirements)}")
            result["requirements"] = analysis.model_dump()

            with self.planning.activity(f"Building test strategy for User Story #{sid}", run_id=run_id,
                                        user_story_id=sid) as act:
                act.event(names.TESTPLAN_STARTED, "Test planning started")
                plan = self.ai.complete_json(
                    "You are a QA test manager. Write a concise, risk-based test strategy for this user story. "
                    "Include performance SLAs only if the story states or clearly implies them.",
                    context + "\n\nRequirements:\n" + analysis.model_dump_json(), models.TestPlan)
                act.event(names.TESTPLAN_COMPLETED, f"Test strategy ready; suggested suite '{plan.suite_name}'")
                act.complete(f"Test strategy ready ({len(plan.test_types)} test types)")
            result["plan"] = plan.model_dump()

            with self.case_design.activity(f"Generating test cases for User Story #{sid}", run_id=run_id,
                                           user_story_id=sid) as act:
                act.event(names.TESTCASE_STARTED, "Test case generation started")
                cases = self.ai.complete_json(
                    f"You are a senior QA engineer. Design at most {max_cases} test cases covering every requirement "
                    "with positive, negative and boundary cases. Each step has one concrete action and one "
                    "observable expected result. Reference requirement ids.",
                    context + "\n\nRequirements:\n" + analysis.model_dump_json()
                    + "\n\nStrategy:\n" + plan.model_dump_json(), models.TestCaseSet, max_tokens=6000)
                cases.test_cases = cases.test_cases[:max_cases]
                covered = {r for tc in cases.test_cases for r in tc.requirement_ids}
                missing = [r.id for r in analysis.requirements if r.id not in covered]
                act.event(names.TESTCASE_COMPLETED, f"Test cases generated: {len(cases.test_cases)}",
                          uncovered_requirements=missing)
                act.complete(f"Test cases generated: {len(cases.test_cases)}"
                             + (f"; uncovered requirements: {', '.join(missing)}" if missing else ""))
            result["test_cases"] = cases.model_dump()["test_cases"]
            result["uncovered_requirements"] = missing

            with self.data.activity(f"Building test data for {len(cases.test_cases)} test cases", run_id=run_id,
                                    user_story_id=sid) as act:
                act.event(names.TESTDATA_STARTED, "Test data generation started")
                data = self.ai.complete_json(
                    "You are a test data engineer. For each test case produce the datasets it needs (valid, invalid, "
                    "boundary). Use realistic but clearly synthetic values; never real personal data or real "
                    "credentials. Use placeholders like {{PASSWORD}} for secrets.",
                    cases.model_dump_json(), models.TestDataSet, max_tokens=6000)
                refs = {tc.ref for tc in cases.test_cases}
                data.datasets = [d for d in data.datasets if d.test_case_ref in refs]
                act.event(names.TESTDATA_COMPLETED, f"Datasets built: {len(data.datasets)}")
                act.complete(f"Datasets built: {len(data.datasets)}")
            result["test_data"] = data.model_dump()["datasets"]
        except Exception as exc:
            result["error"] = exc.message if isinstance(exc, QAError) else str(exc)
            self._save(run_id, sid, result)
            self.runs.finish(run_id, "FAILED", {"error": result["error"]})
            raise
        self._save(run_id, sid, result)
        self.runs.finish(run_id, "PASSED", {"test_cases": len(result["test_cases"]),
                                            "datasets": len(result["test_data"])})
        return result

    def _read_story(self, user_story_id: int, run_id: str) -> dict[str, Any]:
        sid = str(user_story_id)
        with self.requirements.activity(f"Reading User Story #{sid}", run_id=run_id, user_story_id=sid) as act:
            act.event(names.REQUIREMENTS_STARTED, f"Requirements Intelligence Agent reading User Story #{sid}")
            item = self.tfs.detail(user_story_id)
            criteria = split_criteria(html_to_text(item.get("acceptance_criteria")))
            linked = [l["title"] for l in item["traceability"]["acceptance_criteria"]]
            criteria += [c for c in linked if c not in criteria]
            act.complete(f"Acceptance Criteria retrieved: {len(criteria)}", acceptance_criteria=len(criteria))
        return {
            "id": item["id"],
            "title": item["title"],
            "state": item["state"],
            "iteration": item["iteration_path"],
            "description": html_to_text(item.get("description")) or "(none)",
            "criteria": criteria,
            "area_path": item.get("area_path"),
        }

    # ---- persistence ------------------------------------------------------------------

    def _save(self, run_id: str, story_id: str, result: dict[str, Any]) -> None:
        self.db.execute("INSERT OR REPLACE INTO design_results (run_id, user_story_id, created_at, result) VALUES (?,?,?,?)",
                        (run_id, story_id, now_iso(), dumps(result)))

    def get(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM design_results WHERE run_id = ?", (run_id,))
        if not row:
            raise QAError("DESIGN_NOT_FOUND", f"No test design result for {run_id}.", status=404, run_id=run_id)
        return loads(row["result"])

    def latest_for_story(self, user_story_id: int) -> list[dict[str, Any]]:
        return self.db.query("SELECT run_id, created_at FROM design_results WHERE user_story_id = ? ORDER BY created_at DESC",
                             (str(user_story_id),))

    # ---- publish to TFS -----------------------------------------------------------------

    def publish(self, run_id: str, refs: list[str] | None = None, plan_id: int | None = None,
                suite_id: int | None = None) -> dict[str, Any]:
        result = self.get(run_id)
        if result.get("error") or not result.get("test_cases"):
            raise QAError("DESIGN_INCOMPLETE", "This test design run has no test cases to publish.", status=409)
        cases = [models.TestCase.model_validate(tc) for tc in result["test_cases"]]
        if refs:
            cases = [c for c in cases if c.ref in set(refs)]
        story = result["story"]
        sid = str(story["id"])
        tc_type = self.tfs.default_type("testcase")
        story_url = f"{self.tfs.profile()['url']}/_apis/wit/workItems/{story['id']}"
        created: list[dict[str, Any]] = []
        with self.suite.activity(f"Publishing {len(cases)} test cases for User Story #{sid} to TFS", run_id=run_id,
                                 user_story_id=sid) as act:
            for index, case in enumerate(cases):
                act.progress(int(index * 100 / max(1, len(cases))), f"Creating test case {case.ref}: {case.title}")
                values = {
                    "title": case.title,
                    "priority": case.priority,
                    "iteration_path": story.get("iteration"),
                    "area_path": story.get("area_path"),
                    "tags": "AI Generated; " + case.category,
                    "Microsoft.VSTS.TCM.Steps": steps_xml(case.steps),
                    "description": html.escape(case.preconditions or ""),
                }
                patch, skipped = self.tfs.build_patch(tc_type, values)
                patch.append({"op": "add", "path": "/relations/-",
                              "value": {"rel": "Microsoft.VSTS.Common.TestedBy-Reverse", "url": story_url}})
                item = self.tfs.create_raw(tc_type, patch)
                created.append({"ref": case.ref, "id": item["id"], "title": case.title, "skipped_fields": skipped})
                self._attach_data(run_id, sid, case.ref, item["id"], result.get("test_data", []))
            if plan_id and suite_id:
                act.progress(95, f"Adding {len(created)} test cases to suite {suite_id}")
                self.tfs.add_to_suite(plan_id, suite_id, [c["id"] for c in created])
            act.complete(f"Published {len(created)} test cases to TFS", created=[c["id"] for c in created])
        return {"created": created, "plan_id": plan_id, "suite_id": suite_id}

    def _attach_data(self, run_id: str, story_id: str, ref: str, tfs_id: int, datasets: list[dict[str, Any]]) -> None:
        ts = now_iso()
        for d in datasets:
            if d.get("test_case_ref") != ref:
                continue
            self.db.execute(
                "INSERT INTO test_data (user_story_id, test_case_ref, tfs_test_case_id, name, kind, dataset,"
                " source_run_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (story_id, ref, tfs_id, d.get("name", ref), d.get("kind"), dumps(d.get("values", {})), run_id, ts, ts),
            )

    # ---- test data store ----------------------------------------------------------------

    def list_test_data(self, tfs_test_case_id: int | None = None, user_story_id: str | None = None,
                       limit: int = 500) -> list[dict[str, Any]]:
        clauses, params = [], []
        if tfs_test_case_id:
            clauses.append("tfs_test_case_id = ?")
            params.append(tfs_test_case_id)
        if user_story_id:
            clauses.append("user_story_id = ?")
            params.append(user_story_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT * FROM test_data {where} ORDER BY id DESC LIMIT ?", [*params, limit])
        for r in rows:
            r["dataset"] = loads(r["dataset"])
        return rows

    def update_test_data(self, data_id: int, name: str | None, values: dict[str, Any] | None) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM test_data WHERE id = ?", (data_id,))
        if not row:
            raise QAError("TEST_DATA_NOT_FOUND", f"Test data {data_id} does not exist.", status=404)
        self.db.execute("UPDATE test_data SET name = ?, dataset = ?, updated_at = ? WHERE id = ?",
                        (name or row["name"], dumps(values) if values is not None else row["dataset"], now_iso(), data_id))
        updated = self.db.query_one("SELECT * FROM test_data WHERE id = ?", (data_id,)) or row
        updated["dataset"] = loads(updated["dataset"])
        return updated

    def delete_test_data(self, data_id: int) -> None:
        self.db.execute("DELETE FROM test_data WHERE id = ?", (data_id,))
