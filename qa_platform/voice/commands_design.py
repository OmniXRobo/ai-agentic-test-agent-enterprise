"""Voice commands for test cases and test data (23.3.2, milestone 5).

Mixin for VoiceService, placed before MoreCommands so "save the test cases" can also save the suite runs started
by voice. Generation uses the engines the Test Cases and Test Data pages use:
* SuiteCases.run: suite-aware test cases from the acceptance criteria of the sprint's user stories, one run per
  testing-type suite, with traceability, duplicate detection and validation; the cases wait for review,
* SuiteCases.publish_ready: saves the reviewed cases without errors (duplicates of existing TFS cases are linked),
* DataSets.generate: test data from each test case's steps, expected results, criteria and suite type.
Nothing is reported generated or saved unless the engine returned it.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..agents.base import new_run_id
from ..errors import QAError
from .commands_more import MoreCommands
from .commands_plans import _and
from .intents import Intent
from .model import Conversation, Outcome

DESIGN_AGENT_IDS = {"requirements", "test_planning", "test_case_design", "test_data", "test_suite_design"}


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _stories(n: int) -> str:
    return f"{n} user {'story' if n == 1 else 'stories'}"


def _ids_text(ids: list[int]) -> str:
    return _and([str(i) for i in ids])


class DesignCommands:
    """Relies on VoiceService: self.tfs, self.design, self.state, self.runs, self.ai, _current_plan, _ask."""

    def _design_busy(self, agents: set[str] = DESIGN_AGENT_IDS) -> bool:
        return any(s["status"] == "RUNNING" for s in self.state.snapshot() if s["agent_id"] in agents)

    # ---- test cases ---------------------------------------------------------------------------------------------

    def _cmd_generate_suite_cases(self, conv: Conversation, intent: Intent) -> Outcome:
        p = intent.params
        if not p.get("sprint") and not conv.test_plan_id and conv.user_story_id:
            # No plan in the conversation: the older per-user-story design ("generate test cases" for story 118).
            return self._cmd_generate_test_cases(conv, Intent("generate_test_cases", {}, via=intent.via))
        if conv.focus == "user_story" and conv.user_story_id and not p.get("sprint"):
            return self._cmd_generate_test_cases(conv, Intent("generate_test_cases", {}, via=intent.via))
        plan = None
        if p.get("sprint"):
            found = self._cmd_find_test_plan(conv, Intent("find_test_plan", {"sprint": p["sprint"]}))
            if found.status != "completed":
                if found.status == "failed":
                    return Outcome("unclear", f"There is no test plan for {p['sprint'].title()} yet. Say create a test plan "
                                              f"for {p['sprint'].title()} first, then select its suites.")
                return found  # several plans: the agent asks which one
            plan = self._current_plan(conv)
        else:
            plan = self._current_plan(conv)
        if not plan:
            return Outcome("unclear", "For which sprint or test plan? Say, for example, generate test cases for Sprint 25.")
        if not self.ai.config()["base_url"]:
            return Outcome("failed", "Test case generation needs the On-Premise AI, and no AI server is configured.",
                           navigate="#/ai-settings")
        if self._design_busy():
            return Outcome("failed", "A test design run is already in progress. I can start this one when it finishes.")
        suites = [s for s in self.design.cases.suites(plan["id"]) if s.get("parent_id") and s.get("testing_type")]
        chosen = [k for k, v in (conv.suite_selection or {}).items() if v]
        if chosen:
            suites = [s for s in suites if s["testing_type"] in chosen] or suites
        if not suites:
            return Outcome("unclear", f"{plan['name']} has no test suites yet, and each suite decides the kind of test "
                                      "cases. Say, for example, select Functional and Regression first.")
        iteration = plan.get("iteration")
        sprint = self._sprint_of(plan)
        if not sprint:
            return Outcome("unclear", f"{plan['name']} is not linked to a sprint, so I can't tell which user stories to "
                                      "use. Say generate test cases for user story, and its number.")
        stories = self.design.cases.sprint_stories(iteration)
        if not stories:
            return Outcome("failed", f"{sprint} has no user stories, so there are no acceptance criteria to generate test cases from.")
        story_ids = [int(s["id"]) for s in stories][:50]
        plan_id = int(plan["id"])
        runs = []
        for s in suites:
            run_id = new_run_id("TCGEN")
            self.runs.start(run_id, "design", f"{s['testing_type_name']} test cases for {len(story_ids)} user stories",
                            user_story_id=str(story_ids[0]))
            runs.append({"run_id": run_id, "suite_id": int(s["id"]), "suite": s["name"], "type": s["testing_type_name"]})
        conv.case_runs = [r["run_id"] for r in runs]
        query = f"#/test-cases?plan={plan_id}&suites={','.join(str(r['suite_id']) for r in runs)}&runs={','.join(conv.case_runs)}"
        self._focus_plan(conv, plan)

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            totals, issues, dups, failures = [], 0, 0, []
            for r in runs:
                progress(f"Generating {r['type']} test cases for {_stories(len(story_ids))}.")
                try:
                    result = self.design.cases.run(plan_id, r["suite_id"], story_ids, run_id=r["run_id"])
                except QAError as exc:
                    failures.append(f"{r['type']}: {exc.message}")
                    continue
                n = sum(len(st.get("test_cases") or []) for st in result.get("stories", []))
                totals.append((r["type"], n))
                for st in result.get("stories", []):
                    v = st.get("validation") or {}
                    issues += sum(1 for lst in (v.get("per_case") or {}).values() if any(i.get("level") == "error" for i in lst))
                    dups += len(v.get("duplicates") or [])
            made = sum(n for _, n in totals)
            if not totals:
                raise QAError("GENERATION_FAILED", "; ".join(failures) or "no test cases were generated")
            parts = [f"{_n(made, 'test case')} generated from the acceptance criteria of {_stories(len(story_ids))} "
                     f"in {sprint}: " + _and([f"{n} {t}" for t, n in totals]) + "."]
            if dups:
                parts.append(f"{_n(dups, 'possible duplicate')} found.")
            if issues:
                parts.append(f"{_n(issues, 'case')} {'has' if issues == 1 else 'have'} errors to fix before saving.")
            if failures:
                parts.append("Not generated: " + "; ".join(failures) + ".")
            parts.append("They are open for review. Say save the test cases to add them to TFS.")
            conv.last_action = "generate_suite_cases"
            return " ".join(parts), query

        return Outcome("accepted", f"I'm checking the acceptance criteria of {_stories(len(story_ids))} "
                                   f"in {sprint} and preparing {_and([r['type'] for r in runs])} test cases.",
                       navigate=query, job=job, owner="test_case_design", job_title=f"Voice: test cases for {plan['name']}",
                       failure="Test case generation failed")

    def _cmd_save_test_cases(self, conv: Conversation, intent: Intent) -> Outcome:
        story_said = intent.params.get("user_story_id")
        if not conv.case_runs or story_said or (conv.focus == "user_story" and conv.design_run_id):
            return MoreCommands._cmd_save_test_cases(self, conv, intent)
        runs = []
        for run_id in conv.case_runs:
            try:
                result = self.design.cases.get(run_id)
            except QAError:
                continue
            waiting = sum(1 for st in result.get("stories", []) for c in st.get("test_cases") or [] if not c.get("published_id"))
            if waiting:
                runs.append((run_id, (result.get("suite") or {}).get("name") or "suite", waiting))
        if not runs:
            return Outcome("failed", "There are no generated test cases waiting to be saved. Say generate test cases first.")
        total = sum(n for _, _, n in runs)

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            created = reused = held = 0
            ids: list[int] = []
            for run_id, _name, _n_ in runs:
                out = self.design.cases.publish_ready(run_id)
                for c in out.get("created") or []:
                    if c.get("action") == "reused":
                        reused += 1
                    else:
                        created += 1
                    if c.get("id"):
                        ids.append(int(c["id"]))
                held += int(out.get("held") or 0)
            conv.saved_case_ids = list(dict.fromkeys(ids))
            if ids:
                conv.test_case_id, conv.focus = ids[0], "test_cases"
            parts = [f"{_n(created, 'test case')} created in TFS" + (f" and {reused} existing ones linked instead of duplicated" if reused else "") + "."]
            if held:
                parts.append(f"{_n(held, 'case')} with errors {'was' if held == 1 else 'were'} not saved; fix {'it' if held == 1 else 'them'} on Test Cases.")
            return " ".join(parts), None

        return Outcome("accepted", f"I'm saving the reviewed test cases of {_and([n for _, n, _ in runs])} to TFS.", job=job,
                       owner="test_case_design", job_title="Voice: save generated test cases", failure="The test cases could not be saved",
                       confirm_text=f"I will save up to {_n(total, 'generated test case')} to TFS; cases with errors are held back.")

    def _cmd_explain_test_case(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = intent.params.get("test_case_id") or conv.test_case_id
        if not tc:
            return Outcome("unclear", "Which test case? Say, for example, explain test case 105.")
        item = self.tfs.detail(int(tc))
        conv.test_case_id, conv.focus = int(tc), "test_case"
        from ..automation.generator import parse_tfs_steps

        raw = item.get("steps")
        steps = parse_tfs_steps(raw) if isinstance(raw, str) else list(raw or [])
        parts = [f"Test case {tc}: {item.get('title') or 'no title'}."]
        if steps:
            parts.append(f"It has {_n(len(steps), 'step')}. It starts with: {str(steps[0].get('action') or '').strip().rstrip('.')}.")
            last = str(steps[-1].get("expected") or "").strip().rstrip(".")
            if last:
                parts.append(f"The expected result is: {last}.")
        else:
            parts.append("It has no steps yet.")
        return Outcome("completed", " ".join(parts), navigate=f"#/test-cases?id={tc}")

    # ---- test data ----------------------------------------------------------------------------------------------

    def _cmd_generate_test_data(self, conv: Conversation, intent: Intent) -> Outcome:
        p = intent.params
        raw_ids = p.get("test_case_ids") or []
        if isinstance(raw_ids, str):  # the answer to "for which test cases?"
            raw_ids = re.findall(r"\d{1,9}", raw_ids)
        ids = [int(i) for i in raw_ids]
        if not ids and p.get("which") == "these" and conv.saved_case_ids:
            ids = list(conv.saved_case_ids)
        if not ids and conv.test_case_id:
            ids = [int(conv.test_case_id)]
        if not ids:
            return self._ask(conv, Intent("generate_test_data", dict(p)), "test_case_ids",
                             "For which test cases? Say their numbers, for example 105, 106 and 107.")
        ids = list(dict.fromkeys(ids))[:50]
        if self._design_busy({"test_data"}):
            return Outcome("failed", "Test data is already being generated. I can start this when it finishes.")
        rows = max(1, min(60, int(p.get("rows") or 12)))
        note = ""
        if p.get("only"):
            note = (f" The data engine picks positive, negative and boundary rows from each test case's suite type; it can't be "
                    f"limited to {p['only']} rows yet, so check them on Test Data.")
        missing = []
        for tc in ids:
            try:
                self.tfs.detail(tc)
            except QAError:
                missing.append(tc)
        if missing:
            return Outcome("failed", f"I couldn't find test case {_ids_text(missing)} in TFS, so I didn't start.")
        conv.test_case_id, conv.data_case_ids = ids[0], ids
        run_id = new_run_id("TDATA")
        self.runs.start(run_id, "design", f"Test data for {len(ids)} test cases")
        query = f"#/test-data?tc={','.join(str(i) for i in ids)}"

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            try:
                result = self.design.datasets.generate(ids, max_rows=rows, run_id=run_id)
            except Exception as exc:
                self.runs.finish(run_id, "FAILED", {"error": getattr(exc, "message", None) or str(exc)})
                raise
            self.runs.finish(run_id, "PASSED", result)
            per = result.get("results") or []
            total = sum(len(r.get("rows") or []) for r in per)
            empty = [r.get("test_case_id") for r in per if not r.get("rows")]
            gaps = sum(len(r.get("gaps") or []) for r in per)
            parts = [f"{_n(total, 'row')} of test data generated for test case{'s' if len(ids) > 1 else ''} {_ids_text(ids)}, "
                     "each linked to its test case and criteria."]
            if empty:
                parts.append(f"No rows for {_ids_text([e for e in empty if e])}; Test Data shows why.")
            if gaps:
                parts.append(f"{_n(gaps, 'gap')} noted where the requirement does not say enough.")
            parts.append("Review them on Test Data, then say save this test data to write it to TFS.")
            return " ".join(parts), query

        return Outcome("accepted", f"I'm checking test case{'s' if len(ids) > 1 else ''} {_ids_text(ids)} and generating up to "
                                   f"{rows} rows each.{note}", navigate=query, job=job, owner="test_data",
                       job_title=f"Voice: test data for {len(ids)} test cases", failure="Test data generation failed")
