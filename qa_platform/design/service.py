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


# Default testing types: one static suite each under the test plan's root suite, in this order.
DEFAULT_SUITES = ["Functional Testing", "Positive Testing", "Negative Testing", "Boundary / Edge Case Testing",
                  "End-to-End Testing", "Smoke Testing"]
CATEGORY_SUITE = {
    "functional": "Functional Testing", "regression": "Functional Testing", "positive": "Positive Testing",
    "negative": "Negative Testing", "boundary": "Boundary / Edge Case Testing", "edge": "Boundary / Edge Case Testing",
    "end_to_end": "End-to-End Testing", "smoke": "Smoke Testing",
}
_TEST_DATA_HEADING = re.compile(r"<h3>\s*Test Data\s*</h3>.*", re.S | re.I)


def suite_for(category: str) -> str:
    return CATEGORY_SUITE.get(category, "Functional Testing")


def titled(case: models.TestCase) -> str:
    """'Negative Testing - <title>', the naming used in the TFS suites."""
    prefix = suite_for(case.category)
    return case.title if case.title.lower().startswith(prefix.lower()) else f"{prefix} - {case.title}"


def plan_name(project: str, iteration: str | None) -> str:
    sprint = (iteration or "").replace("/", "\\").split("\\")[-1].strip()
    return f"{project} - {sprint} - Test Plan" if sprint and sprint != project else f"{project} - Test Plan"


def strategy_text(plan: dict[str, Any] | None) -> str | None:
    if not plan:
        return None
    lines = [f"Scope: {plan.get('scope', '')}", f"Approach: {plan.get('approach', '')}",
             "Test types: " + ", ".join(plan.get("test_types", []))]
    for label, key in (("Risks", "risks"), ("Entry criteria", "entry_criteria"), ("Exit criteria", "exit_criteria")):
        if plan.get(key):
            lines.append(f"{label}: " + "; ".join(plan[key]))
    return "\n".join(lines)


def _param_name(key: str) -> str:
    name = re.sub(r"\W+", "_", str(key)).strip("_") or "value"
    return name if not name[0].isdigit() else f"p_{name}"


def test_data_html(datasets: list[dict[str, Any]], preconditions: str | None = None, story_id: Any = None) -> str:
    """Summary (System.Description) text: preconditions plus a Test Data table, one row per dataset."""
    parts = [f"<p><strong>Preconditions:</strong> {html.escape(preconditions)}</p>"] if preconditions else []
    if datasets:
        keys = list(dict.fromkeys(k for d in datasets for k in (d.get("values") or {})))
        head = "".join(f"<th>{html.escape(str(k))}</th>" for k in keys)
        rows = "".join(
            f"<tr><td>{n}</td><td>{html.escape(str(d.get('name', '')))}</td><td>{html.escape(str(d.get('kind') or ''))}</td>"
            + "".join(f"<td>{html.escape(_cell((d.get('values') or {}).get(k)))}</td>" for k in keys) + "</tr>"
            for n, d in enumerate(datasets, start=1))
        parts.append(f'<h3>Test Data</h3><table border="1" cellpadding="4"><tr><th>#</th><th>Data set</th><th>Type</th>'
                     f"{head}</tr>{rows}</table>")
    else:
        parts.append("<h3>Test Data</h3><p>No variable test data is required for this Test Case.</p>")
    if story_id:
        parts.append(f"<p><em>Generated by AI Agentic Test Agent Enterprise from User Story #{html.escape(str(story_id))}."
                     "</em></p>")
    return "".join(parts)


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def parameters_xml(datasets: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Microsoft.VSTS.TCM.Parameters and LocalDataSource: the test case's Parameter values grid, one row per dataset."""
    keys = list(dict.fromkeys(k for d in datasets for k in (d.get("values") or {})))
    if not keys:
        return None
    names_ = list(dict.fromkeys(_param_name(k) for k in keys))
    params = "<parameters>" + "".join(f'<param name="{n}" bind="default" />' for n in names_) + "</parameters>"
    schema = ("<xs:schema id='NewDataSet' xmlns:xs='http://www.w3.org/2001/XMLSchema' "
              "xmlns:msdata='urn:schemas-microsoft-com:xml-msdata'><xs:element name='NewDataSet' msdata:IsDataSet='true' "
              "msdata:Locale=''><xs:complexType><xs:choice minOccurs='0' maxOccurs='unbounded'><xs:element name='Table1'>"
              "<xs:complexType><xs:sequence>"
              + "".join(f"<xs:element name='{n}' type='xs:string' minOccurs='0' />" for n in names_)
              + "</xs:sequence></xs:complexType></xs:element></xs:choice></xs:complexType></xs:element></xs:schema>")
    rows = ""
    for d in datasets:
        values = {_param_name(k): v for k, v in (d.get("values") or {}).items()}
        rows += "<Table1>" + "".join(f"<{n}>{html.escape(_cell(values.get(n)), quote=False)}</{n}>" for n in names_) + "</Table1>"
    return params, f"<NewDataSet>{schema}{rows}</NewDataSet>"


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
        self.cases: Any = None  # SuiteCaseService, attached by the runtime
        self.datasets: Any = None  # TestDataService, attached by the runtime

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
                    f"You are a senior QA engineer. Design at most {max_cases} test cases covering every requirement. "
                    "Set each category to the type of testing: functional (at least one per acceptance criterion), "
                    "positive, negative (invalid input, missing data, errors), boundary or edge (limits), end_to_end "
                    "(one complete user journey) and smoke (one quick check of the critical path). Each step has one "
                    "concrete action and one observable expected result. Reference requirement ids.",
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
                suite_id: int | None = None, *, create_plan: bool = False, plan_title: str | None = None,
                default_suites: bool = True, data_in_summary: bool = True,
                data_as_parameters: bool = True) -> dict[str, Any]:
        """Create the test cases in TFS, optionally with their test plan and one suite per testing type.

        * ``create_plan``: reuse or create the plan "<project> - <sprint> - Test Plan" (or ``plan_title``).
        * ``default_suites`` (with a plan and no ``suite_id``): every case goes into the suite for its testing type.
        * ``data_in_summary`` / ``data_as_parameters``: write the test data into the Summary (Description) and the
          Parameter values grid of each test case.
        """
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
        plan: dict[str, Any] | None = None
        suites: dict[str, int] = {}
        with self.suite.activity(f"Publishing {len(cases)} test cases for User Story #{sid} to TFS", run_id=run_id,
                                 user_story_id=sid) as act:
            if create_plan and not plan_id:
                name = (plan_title or "").strip() or plan_name(self.tfs.profile()["project"], story.get("iteration"))
                act.progress(2, f"Preparing test plan '{name}'")
                plan = self.tfs.ensure_test_plan(name, area_path=story.get("area_path"),
                                                 iteration=story.get("iteration"),
                                                 description=strategy_text(result.get("plan")))
                plan_id = plan["id"]
                act.event(names.TESTPLAN_COMPLETED, f"Test plan #{plan_id} {'created' if plan['created'] else 'reused'}: "
                          f"{plan['name']}", plan_id=plan_id)
            if plan_id and not suite_id:
                if default_suites:
                    act.progress(5, "Preparing the testing-type suites")
                    made = self.tfs.ensure_suites(plan_id, DEFAULT_SUITES, (plan or {}).get("root_suite_id"))
                    suites = {s["name"]: s["id"] for s in made}
                    act.event(names.TESTPLAN_COMPLETED, f"Test suites ready: {len(made)} "
                              f"({sum(1 for s in made if s['created'])} new)", plan_id=plan_id)
                else:
                    root = next((s for s in self.tfs.test_suites(plan_id) if not s.get("parent_id")), None)
                    suite_id = root["id"] if root else None
            for index, case in enumerate(cases):
                act.progress(10 + int(index * 85 / max(1, len(cases))), f"Creating test case {case.ref}: {case.title}")
                datasets = [d for d in result.get("test_data", []) if d.get("test_case_ref") == case.ref]
                values: dict[str, Any] = {
                    "title": titled(case),
                    "priority": case.priority,
                    "iteration_path": story.get("iteration"),
                    "area_path": story.get("area_path"),
                    "tags": "AI Generated; " + suite_for(case.category),
                    "Microsoft.VSTS.TCM.Steps": steps_xml(case.steps),
                    "description": (test_data_html(datasets, case.preconditions, sid) if data_in_summary
                                    else html.escape(case.preconditions or "")),
                }
                params = parameters_xml(datasets) if data_as_parameters else None
                if params:
                    values["Microsoft.VSTS.TCM.Parameters"], values["Microsoft.VSTS.TCM.LocalDataSource"] = params
                patch, skipped = self.tfs.build_patch(tc_type, values)
                patch.append({"op": "add", "path": "/relations/-",
                              "value": {"rel": "Microsoft.VSTS.Common.TestedBy-Reverse", "url": story_url}})
                item = self.tfs.create_raw(tc_type, patch)
                target = suite_id or suites.get(suite_for(case.category))
                created.append({"ref": case.ref, "id": item["id"], "title": values["title"], "category": case.category,
                                "suite": suite_for(case.category) if not suite_id else None, "suite_id": target,
                                "datasets": len(datasets), "skipped_fields": skipped})
                self._attach_data(run_id, sid, case.ref, item["id"], result.get("test_data", []))
            if plan_id:
                by_suite: dict[int, list[int]] = {}
                for c in created:
                    if c["suite_id"]:
                        by_suite.setdefault(c["suite_id"], []).append(c["id"])
                for target, ids in by_suite.items():
                    act.progress(96, f"Adding {len(ids)} test cases to suite {target}")
                    self.tfs.add_to_suite(plan_id, target, ids)
            act.complete(f"Published {len(created)} test cases to TFS"
                         + (f" in test plan #{plan_id}" if plan_id else ""), created=[c["id"] for c in created])
        out = {"created": created, "plan_id": plan_id, "suite_id": suite_id,
               "plan": plan, "suites": [{"name": n, "id": i} for n, i in suites.items()]}
        result.setdefault("published", []).append({"at": now_iso(), **out})
        self._save(run_id, sid, result)
        return out

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

    def write_test_data_to_tfs(self, test_case_id: int, *, summary: bool = True,
                               parameters: bool = True) -> dict[str, Any]:
        """Write the saved test data of one TFS test case into its Summary and Parameter values."""
        rows = self.list_test_data(tfs_test_case_id=test_case_id)
        datasets = [{"name": r["name"], "kind": r["kind"], "values": r["dataset"] if isinstance(r["dataset"], dict) else {}}
                    for r in reversed(rows)]
        item = self.tfs.detail(test_case_id)
        values: dict[str, Any] = {}
        if summary:
            current = _TEST_DATA_HEADING.sub("", item.get("description") or "")
            current = re.sub(r"<p><em>Generated by AI Agentic Test Agent Enterprise[^<]*</em></p>", "", current)
            values["description"] = current + test_data_html(datasets)
        params = parameters_xml(datasets) if parameters else None
        if params:
            values["Microsoft.VSTS.TCM.Parameters"], values["Microsoft.VSTS.TCM.LocalDataSource"] = params
        if not values:
            raise QAError("TEST_DATA_EMPTY", f"Test Case #{test_case_id} has no saved test data to write.", status=409)
        with self.data.activity(f"Writing test data to Test Case #{test_case_id} in TFS",
                                test_case_id=str(test_case_id)) as act:
            updated = self.tfs.update(test_case_id, values)
            act.complete(f"Test data written to Test Case #{test_case_id}: {len(datasets)} data set(s)")
        return {"test_case_id": test_case_id, "datasets": len(datasets), "skipped_fields": updated["skipped_fields"]}

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
