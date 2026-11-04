"""Voice commands for test plans and test suites (23.3.1, milestones 3 and 4).

Mixin for VoiceService. Everything goes through the services the Test Plans and Test Suites pages use
(TfsService.test_plans / ensure_test_plan / update_test_plan / delete_test_suite and SuiteCases.suites /
create_suites), and every write is read back from TFS before the agent says it is done.

Context: the conversation remembers the current test plan (found, opened, created or selected on the page), so
"open it", "show me more details", "go to Test Suites" and "select Regression" act on that plan. If no plan is
known, or a name matches several plans, the agent asks instead of guessing.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..design import testing_types
from ..design.service import plan_name
from ..errors import QAError
from . import intents
from .intents import Intent
from .model import Conversation, Outcome

TYPE_NAME = {k: t["name"] for k, t in testing_types.BY_KEY.items()}


def _and(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _names(keys: list[str]) -> str:
    return _and([TYPE_NAME[k] for k in keys])


def _norm(s: str | None) -> str:
    return intents.normalize(s or "")


def original_case(text: str, value: str) -> str:
    """The words as the user wrote or the recognizer capitalized them ('checkout regression' -> 'Checkout Regression')."""
    m = re.search(r"\s+".join(re.escape(w) for w in value.split()), text, re.I)
    return m.group(0) if m else value


class PlanCommands:
    """Relies on VoiceService: self.tfs, self.design, self._ask, self._remember_list."""

    # ---- helpers --------------------------------------------------------------------------------------------------

    def _project(self) -> str:
        return self.tfs.profile().get("project") or ""

    def _sprint_of(self, plan: dict[str, Any]) -> str | None:
        it = (plan.get("iteration") or "").replace("/", "\\")
        last = it.split("\\")[-1].strip()
        return last if last and last != self._project() else None

    def _plan_by_id(self, plan_id: int | None, fresh: bool = False) -> dict[str, Any] | None:
        if not plan_id:
            return None
        if fresh:
            self.tfs.invalidate()
        return next((p for p in self.tfs.test_plans() if int(p["id"]) == int(plan_id)), None)

    def _focus_plan(self, conv: Conversation, plan: dict[str, Any]) -> None:
        conv.test_plan_id, conv.test_plan_name = int(plan["id"]), plan.get("name")
        conv.focus = "test_plan"
        sprint = self._sprint_of(plan)
        if sprint:
            conv.sprint = sprint

    def _iterations_matching(self, sprint: str) -> list[str]:
        wanted = _norm(sprint)
        its = [i for i in (self.tfs.meta().get("iterations") or []) if i != self._project()]
        exact = [i for i in its if _norm(i.replace("/", "\\").split("\\")[-1]) == wanted]
        if exact:
            return exact
        pattern = re.compile(rf"\b{re.escape(wanted)}\b")
        return [i for i in its if pattern.search(_norm(i))]

    def _current_plan(self, conv: Conversation) -> dict[str, Any] | None:
        plan = self._plan_by_id(conv.test_plan_id)
        if plan is None and conv.test_plan_id:
            conv.test_plan_id = conv.test_plan_name = None
        return plan

    def _no_plan(self) -> Outcome:
        return Outcome("unclear", "Which test plan? Say, for example, find the test plan for Sprint 24, or create a "
                                  "test plan for Sprint 25.")

    def _plan_line(self, plan: dict[str, Any]) -> str:
        sprint = self._sprint_of(plan)
        return f"{plan['name']}" + (f" for {sprint}" if sprint and sprint.lower() not in (plan["name"] or "").lower() else "")

    def _open_plan(self, conv: Conversation, plan: dict[str, Any], say: str) -> Outcome:
        self._focus_plan(conv, plan)
        return Outcome("completed", say, navigate=f"#/test-plans?id={plan['id']}",
                       verify={"route": "test-plans", "label": f"Test plan {plan['name']}"})

    # ---- find / open ----------------------------------------------------------------------------------------------

    def _cmd_find_test_plan(self, conv: Conversation, intent: Intent) -> Outcome:
        p = intent.params
        plans = self.tfs.test_plans()
        if not plans:
            return Outcome("failed", f"There are no test plans in project {self._project()} yet. Say create a test plan "
                                     "for a sprint.", navigate="#/test-plans")
        if p.get("which") == "current":
            plan = self._current_plan(conv)
            if not plan:
                return self._no_plan()
            return self._open_plan(conv, plan, f"I'm opening {plan['name']}.")
        if p.get("which") == "last" and not any(k in p for k in ("id", "sprint", "text", "state")):
            plan = self._current_plan(conv) or max(plans, key=lambda x: int(x["id"]))
            return self._open_plan(conv, plan, f"I'm opening the last test plan, {plan['name']}.")
        found = plans
        what = "matching that"
        if p.get("id"):
            found = [x for x in found if int(x["id"]) == int(p["id"])]
            what = f"with ID {p['id']}"
        if p.get("sprint"):
            wanted = _norm(p["sprint"])
            rx = re.compile(rf"\b{re.escape(wanted)}\b")
            found = [x for x in found if _norm(self._sprint_of(x)) == wanted or rx.search(_norm(x.get("name")))]
            what = f"for {p['sprint'].title()}"
        if p.get("state"):
            found = [x for x in found if (x.get("state") or "").lower() == p["state"].lower()]
            what = f"{what} that is {p['state'].lower()}" if what != "matching that" else f"that is {p['state'].lower()}"
        if p.get("text"):
            from .service import _match_score
            scored = sorted(((_match_score(p["text"], x.get("name"), self._sprint_of(x)), x) for x in found),
                            key=lambda s: -s[0])
            found = [x for score, x in scored if score >= 0.75]
            what = f"matching {p['text']}"
        if not found:
            return Outcome("failed", f"I couldn't find a test plan {what}.")
        if len(found) == 1:
            plan = found[0]
            return self._open_plan(conv, plan, f"I've found the test plan {what}: {self._plan_line(plan)}. I'm opening it now.")
        found = sorted(found, key=lambda x: -int(x["id"]))[:9]
        self._remember_list(conv, [{"kind": "test plan", "id": x["id"], "label": f"test plan {x['name']}",
                                    "navigate": f"#/test-plans?id={x['id']}", "name": x["name"]} for x in found])
        listed = "; ".join(f"{i + 1}, {self._plan_line(x)}" for i, x in enumerate(found))
        return Outcome("unclear", f"I found {len(found)} test plans {what}: {listed}. Which one should I open? Say the first one, "
                                  "the second one, or its number.", navigate="#/test-plans")

    def _cmd_open_current(self, conv: Conversation, intent: Intent) -> Outcome:
        if conv.focus == "test_plan" and conv.test_plan_id:
            return self._cmd_find_test_plan(conv, Intent("find_test_plan", {"which": "current"}))
        if conv.focus == "test_case" and conv.test_case_id:
            return self._cmd_open_work_item(conv, Intent("open_work_item", {"kind": "test case", "id": conv.test_case_id}))
        if conv.focus == "user_story" and conv.user_story_id:
            return self._cmd_open_work_item(conv, Intent("open_work_item", {"kind": "user story", "id": conv.user_story_id}))
        if conv.test_plan_id:
            return self._cmd_find_test_plan(conv, Intent("find_test_plan", {"which": "current"}))
        return Outcome("unclear", "What should I open? Say, for example, open the test plan for Sprint 24.")

    def _cmd_show_details(self, conv: Conversation, intent: Intent) -> Outcome:
        if conv.focus not in (None, "test_plan") and not conv.test_plan_id:
            return Outcome("unclear", "Details of what? Say, for example, show the test plan for Sprint 24.")
        plan = self._current_plan(conv)
        if not plan:
            return Outcome("unclear", "Details of which item? Open a test plan first, for example: find the test plan for Sprint 24.")
        suites = [s for s in self.design.cases.suites(plan["id"]) if s.get("parent_id")]
        sprint = self._sprint_of(plan)
        parts = [f"Test plan {plan['id']}, {plan['name']}.", f"State {plan.get('state') or 'not set'}.",
                 f"Sprint {sprint}." if sprint and not sprint.lower().startswith("sprint") else f"{sprint}." if sprint else "No sprint."]
        parts.append(f"It has {len(suites)} suite{'s' if len(suites) != 1 else ''}: {_and([s['name'] for s in suites])}." if suites
                     else "It has no suites yet.")
        self._focus_plan(conv, plan)
        return Outcome("completed", "I'm opening the test plan details. " + " ".join(parts),
                       navigate=f"#/test-plans?id={plan['id']}", verify={"route": "test-plans", "label": f"Test plan {plan['name']}"})

    def _cmd_list_suites(self, conv: Conversation, intent: Intent) -> Outcome:
        plan = self._current_plan(conv)
        if not plan:
            return self._no_plan()
        suites = [s for s in self.design.cases.suites(plan["id"]) if s.get("parent_id")]
        if not suites:
            return Outcome("completed", f"{plan['name']} has no suites yet. Say, for example, select Functional and Regression.")
        return Outcome("completed", f"{plan['name']} has {len(suites)} suites: {_and([s['name'] for s in suites])}.")

    # ---- create / change ------------------------------------------------------------------------------------------

    def _cmd_create_test_plan(self, conv: Conversation, intent: Intent) -> Outcome:
        p = intent.params
        project = self._project()
        iteration = None
        if p.get("sprint"):
            matches = self._iterations_matching(p["sprint"])
            if not matches:
                return self._ask(conv, intent, "sprint", f"I can't find {p['sprint'].title()} in project {project}. "
                                                         "Which sprint should the test plan be for?")
            if len(matches) > 1:
                return self._ask(conv, intent, "sprint", f"{p['sprint'].title()} matches {len(matches)} sprints: "
                                                         f"{_and(matches)}. Which one? Say the full sprint name.")
            iteration = matches[0]
        name = (p.get("name") or "").strip() or plan_name(project, iteration)
        if not name or not project:
            return Outcome("failed", "No TFS project is set. Open TFS Connection settings first.", navigate="#/tfs-settings")
        sprint_label = iteration.replace("/", "\\").split("\\")[-1] if iteration else None
        with_suites = bool(p.get("default_suites"))

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            plan = self.tfs.ensure_test_plan(name, iteration=iteration)
            saved = self._plan_by_id(plan["id"], fresh=True)
            if not saved:
                raise QAError("PLAN_NOT_VERIFIED", f"TFS did not list test plan {plan['id']} after creating it")
            self._focus_plan(conv, saved)
            conv.suite_selection = {}
            extra = ""
            if with_suites:
                made = self.design.cases.create_suites(saved["id"], list(testing_types.DEFAULT_CHECKED))
                extra = f" It has the default suites: {_and([s['name'] for s in made])}."
            else:
                extra = " It has no suites yet. Tell me which test suites to add."
            where = f" for {sprint_label}" if sprint_label else ""
            head = (f"The test plan {saved['name']}{where} has been created." if plan.get("created")
                    else f"A test plan named {saved['name']} already exists, so I'm using that one; nothing was duplicated.")
            return head + extra, f"#/test-plans?id={saved['id']}"

        return Outcome("accepted", f"I'm preparing the test plan {name}.", job=job, owner="test_planning",
                       job_title=f"Voice: test plan {name}", failure="The test plan could not be created")

    def _cmd_update_test_plan(self, conv: Conversation, intent: Intent) -> Outcome:
        plan = self._current_plan(conv)
        if not plan:
            return self._no_plan()
        p = intent.params
        changes: dict[str, Any] = {}
        said = []
        if p.get("name"):
            changes["name"] = p["name"]
            said.append(f"rename it to {changes['name']}")
        if p.get("state"):
            changes["state"] = p["state"]
            said.append(f"set its state to {p['state']}")
        if p.get("sprint"):
            matches = self._iterations_matching(p["sprint"])
            if len(matches) != 1:
                return Outcome("unclear", f"I can't tell which sprint {p['sprint'].title()} is in project {self._project()}. Say the full sprint name.")
            changes["iteration"] = matches[0]
            said.append(f"move it to {matches[0].split(chr(92))[-1]}")
        if not changes:
            return Outcome("unclear", "What should I change on the test plan: its name, its state or its sprint?")
        plan_id = int(plan["id"])

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.tfs.update_test_plan(plan_id, **changes)
            saved = self._plan_by_id(plan_id, fresh=True)
            if not saved:
                raise QAError("PLAN_NOT_VERIFIED", f"test plan {plan_id} is no longer listed in TFS")
            wrong = [k for k, v in (("name", changes.get("name")), ("state", changes.get("state"))) if v and (saved.get(k) or "") != v]
            if wrong:
                raise QAError("PLAN_NOT_VERIFIED", f"TFS still shows the old {' and '.join(wrong)}")
            self._focus_plan(conv, saved)
            return f"Test plan {plan_id} is updated: {_and(said)}.", f"#/test-plans?id={plan_id}"

        return Outcome("accepted", f"I'm updating test plan {plan['name']}.", job=job, owner="test_planning",
                       job_title=f"Voice: update test plan {plan_id}", failure="The test plan could not be updated",
                       confirm_text=f"I will {_and(said)} for test plan {plan['name']} in TFS.")

    # ---- suites -----------------------------------------------------------------------------------------------------

    def _suite_job(self, conv: Conversation, plan: dict[str, Any], check: list[str], uncheck: list[str]) -> Outcome:
        plan_id = int(plan["id"])
        existing = {s.get("testing_type"): s for s in self.design.cases.suites(plan_id) if s.get("parent_id") and s.get("testing_type")}
        to_add = [k for k in check if k not in existing]
        already = [k for k in check if k in existing]
        kept = [k for k in uncheck if k in existing]
        absent = [k for k in uncheck if k not in existing]
        conv.suite_selection = {**{k: True for k in check}, **{k: False for k in uncheck}}
        query = f"#/test-suites?plan={plan_id}" + (f"&checked={','.join(check)}" if check else "") + \
            (f"&unchecked={','.join(uncheck)}" if uncheck else "")
        verify = {"route": "test-suites", "label": "Test Suites"}

        def notes() -> str:
            out = []
            if already:
                out.append(f"{_names(already)} {'was' if len(already) == 1 else 'were'} already in the plan.")
            if absent:
                out.append(f"{_names(absent)} {'is' if len(absent) == 1 else 'are'} not included.")
            if kept:
                out.append(f"{_names(kept)} already {'exists' if len(kept) == 1 else 'exist'} in this plan, so unchecking "
                           f"did not delete {'it' if len(kept) == 1 else 'them'}. Say delete the {TYPE_NAME[kept[0]]} suite to remove it.")
            return " ".join(out)

        if not to_add:
            return Outcome("completed", (f"Nothing to add to {plan['name']}. " + notes()).strip(), navigate=query, verify=verify)

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.design.cases.create_suites(plan_id, to_add)
            now = {s.get("testing_type") for s in self.design.cases.suites(plan_id) if s.get("parent_id")}
            missing = [k for k in to_add if k not in now]
            if missing:
                raise QAError("SUITES_NOT_VERIFIED", f"TFS does not show {_names(missing)} in the plan after saving")
            self._focus_plan(conv, plan)
            return f"Your test suite selections have been saved: {_names(to_add)} added to {plan['name']}. {notes()}".strip(), query + "&saved=1"

        return Outcome("accepted", f"I'm updating the test suites of {plan['name']}: adding {_names(to_add)}.", job=job,
                       owner="test_suite_design", job_title=f"Voice: suites for test plan {plan_id}",
                       failure="The test suites could not be saved", navigate=query)

    def _cmd_select_suites(self, conv: Conversation, intent: Intent) -> Outcome:
        plan = self._current_plan(conv)
        if not plan:
            return Outcome("unclear", "Which test plan should I change the suites of? Say, for example, open the test plan "
                                      "for Sprint 25.")
        check = [k for k in intent.params.get("check") or [] if k in testing_types.BY_KEY]
        uncheck = [k for k in intent.params.get("uncheck") or [] if k in testing_types.BY_KEY]
        if not check and not uncheck:
            return Outcome("unclear", "Which suites? For example: select Functional and Regression, and uncheck Performance.")
        return self._suite_job(conv, plan, check, uncheck)

    def _cmd_save_suites(self, conv: Conversation, intent: Intent) -> Outcome:
        plan = self._current_plan(conv)
        if not plan:
            return self._no_plan()
        sel = conv.suite_selection or {}
        if not sel:
            return Outcome("unclear", "There is no suite selection to save yet. Say, for example, select Functional and "
                                      "Regression, and uncheck Performance.")
        return self._suite_job(conv, plan, [k for k, v in sel.items() if v], [k for k, v in sel.items() if not v])

    def _cmd_delete_suite(self, conv: Conversation, intent: Intent) -> Outcome:
        plan = self._current_plan(conv)
        if not plan:
            return self._no_plan()
        key = intent.params.get("testing_type")
        suites = [s for s in self.design.cases.suites(plan["id"]) if s.get("parent_id") and s.get("testing_type") == key]
        if not suites:
            return Outcome("failed", f"{plan['name']} has no {TYPE_NAME.get(key, key)} suite.")
        if len(suites) > 1:
            return Outcome("unclear", f"{plan['name']} has {len(suites)} {TYPE_NAME[key]} suites: "
                                      f"{_and([str(s['name']) + ' (ID ' + str(s['id']) + ')' for s in suites])}. Delete one on the Test Suites page.",
                           navigate=f"#/test-suites?plan={plan['id']}")
        suite = suites[0]
        plan_id, suite_id = int(plan["id"]), int(suite["id"])

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.tfs.delete_test_suite(plan_id, suite_id)
            self.design.cases.forget_suite(suite_id)
            if any(int(s["id"]) == suite_id for s in self.tfs.test_suites(plan_id)):
                raise QAError("SUITE_NOT_VERIFIED", "TFS still lists the suite")
            return f"The {suite['name']} suite was deleted from {plan['name']}.", f"#/test-suites?plan={plan_id}"

        return Outcome("accepted", f"I'm deleting the {suite['name']} suite.", job=job, owner="test_suite_design",
                       job_title=f"Voice: delete suite {suite_id}", failure="The suite could not be deleted",
                       confirm_text=f"This deletes the {suite['name']} suite (ID {suite_id}) from {plan['name']} in TFS. "
                                    "Its test case work items stay in the project, and TFS cannot undo this.")
