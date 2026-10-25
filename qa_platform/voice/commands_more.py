"""Voice command handlers added for the Voice Command Center (see registry.py for the list).

Each handler calls the same service a dashboard page calls and reports what that service
returned. Nothing is announced as done before the service says so: long work runs as a job
whose result is spoken when it ends.
"""

from __future__ import annotations

from typing import Any, Callable

from ..errors import QAError
from . import intents
from .intents import Intent
from .model import Conversation, Outcome

KIND_CATEGORY = {"user story": "requirement", "test case": "testcase", "defect": "bug"}
KIND_ROUTE = {"user story": "user-stories", "test case": "test-cases", "defect": "defects"}
STUDIO_TAB = {"result": "results", "logs": "logs", "evidence": "evidence"}
ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class MoreCommands:
    """Mixin for VoiceService; relies on its services (tfs, design, automation, studio, reports...)."""

    # ---- navigation and lists ---------------------------------------------------------------

    def _cmd_go_back(self, conv: Conversation, intent: Intent) -> Outcome:
        return Outcome("completed", "Going back.", navigate="back")

    def _remember_list(self, conv: Conversation, items: list[dict[str, Any]]) -> None:
        conv.last_list = items[:50]

    def _cmd_select_item(self, conv: Conversation, intent: Intent) -> Outcome:
        if not conv.last_list:
            return Outcome("failed", "There is no list to choose from. Ask me to show or search for items first.")
        pos = int(intent.params.get("position") or 1)
        index = len(conv.last_list) - 1 if pos == -1 else pos - 1
        if not 0 <= index < len(conv.last_list):
            return Outcome("failed", f"The list has only {_plural(len(conv.last_list), 'item')}.")
        item = conv.last_list[index]
        if item.get("kind") == "user story":
            conv.user_story_id = int(item["id"])
        elif item.get("kind") == "test case":
            conv.test_case_id = int(item["id"])
        elif item.get("kind") == "run":
            conv.run_id, conv.run_kind = item["id"], item.get("run_kind")
        return Outcome("completed", f"Selected {item['label']}.", navigate=item.get("navigate"))

    def _cmd_search_work_items(self, conv: Conversation, intent: Intent) -> Outcome:
        p = intent.params
        kind = p.get("kind") or "user story"
        sprint = None
        if p.get("sprint"):
            wanted = intents.normalize(p["sprint"])
            iterations = self.tfs.meta().get("iterations") or []
            sprint = next((i for i in iterations if intents.normalize(i.split("\\")[-1]) == wanted), None) or \
                next((i for i in iterations if wanted in intents.normalize(i)), None)
            if not sprint:
                return Outcome("failed", f"I could not find {p['sprint']} in this TFS project.")
        result = self.tfs.query(category=KIND_CATEGORY[kind], iteration=sprint, state=p.get("state") or None,
                                text=p.get("text") or None, top=50)
        items = result["items"]
        route = KIND_ROUTE[kind]
        query = "&".join(f"{k}={v}" for k, v in (("iteration", sprint), ("state", p.get("state")), ("q", p.get("text"))) if v)
        where = f" in {sprint.split(chr(92))[-1]}" if sprint else ""
        if not items:
            return Outcome("completed", f"I found no {kind}s{where} matching that.", navigate=f"#/{route}?{query}")
        self._remember_list(conv, [{"kind": kind, "id": i["id"], "label": f"{kind} {i['id']}, {i['title']}",
                                    "navigate": f"#/{route}?id={i['id']}"} for i in items])
        first = items[0]
        return Outcome("completed", f"I found {_plural(len(items), kind)}{where}. The first is {first['id']}, "
                                    f"{first['title']}. Say select the second one, or a number, to open one.",
                       navigate=f"#/{route}?{query}")

    # ---- TFS test plans, test cases and test data -------------------------------------------

    def _story_for(self, conv: Conversation, intent: Intent) -> int | None:
        sid = intent.params.get("user_story_id") or conv.user_story_id
        return int(sid) if sid else None

    def _cmd_generate_test_plan(self, conv: Conversation, intent: Intent) -> Outcome:
        sid = self._story_for(conv, intent)
        if not sid:
            return self._ask(conv, intent, "user_story_id", "Which user story should the test plan be for? Say its number.")
        from ..design.service import DEFAULT_SUITES, plan_name

        story = self.tfs.detail(sid)
        name = plan_name(self.tfs.profile()["project"], story.get("iteration_path"))
        conv.user_story_id = sid

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            plan = self.tfs.ensure_test_plan(name, area_path=story.get("area_path"), iteration=story.get("iteration_path"))
            suites = self.tfs.ensure_suites(plan["id"], DEFAULT_SUITES, plan.get("root_suite_id"))
            new = sum(1 for s in suites if s.get("created"))
            return (f"Test plan {plan['id']}, {plan['name']}, was {'created' if plan.get('created') else 'reused'}, with "
                    f"{_plural(len(suites), 'suite')} ({new} new).", "#/test-plans")

        return Outcome("accepted", f"Creating the test plan {name} with its default suites for user story {sid}.",
                       job=job, owner="test_suite_design", job_title=f"Voice: test plan for User Story #{sid}",
                       failure="The test plan could not be created",
                       confirm_text=f"I will create or reuse the test plan {name} and its default suites in TFS.")

    def _cmd_save_test_cases(self, conv: Conversation, intent: Intent) -> Outcome:
        sid = self._story_for(conv, intent)
        run_id = conv.design_run_id if conv.design_run_id and (not sid or sid == conv.user_story_id) else None
        if not run_id and sid:
            runs = self.design.latest_for_story(sid)
            run_id = runs[0]["run_id"] if runs else None
        if not run_id:
            return Outcome("failed", "There is no generated test design to save. Say generate test cases for a user story first.")
        result = self.design.get(run_id)
        n = len(result.get("test_cases") or [])
        story = (result.get("story") or {}).get("id")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            out = self.design.publish(run_id, create_plan=True)
            created = out.get("created") or []
            return (f"{_plural(len(created), 'test case')} saved to TFS for user story {story}.",
                    f"#/test-design?story={story}&run={run_id}")

        return Outcome("accepted", f"Saving {_plural(n, 'test case')} of user story {story} to TFS.", job=job,
                       owner="test_suite_design", job_title=f"Voice: saving test cases of User Story #{story}",
                       failure="The test cases could not be saved",
                       confirm_text=f"I will create {_plural(n, 'test case')} for user story {story} in TFS, with the test plan.")

    def _test_case(self, conv: Conversation, intent: Intent) -> int | None:
        tc = intent.params.get("test_case_id") or conv.test_case_id
        return int(tc) if tc else None

    def _cmd_create_test_data(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case needs test data? Say its number.")
        conv.test_case_id = tc
        rows = self.design.list_test_data(tc)
        if rows:
            return Outcome("completed", f"Test case {tc} has {_plural(len(rows), 'data set')}. They are open on Test Data "
                                        "for editing.", navigate=f"#/test-data?tc={tc}")
        return Outcome("failed", f"Test case {tc} has no saved test data. Test data is generated together with the test "
                                 "cases of its user story; say generate test cases for that user story.",
                       navigate=f"#/test-data?tc={tc}")

    def _cmd_write_test_data(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case's data should I write to TFS?")
        if not self.design.list_test_data(tc):
            return Outcome("failed", f"Test case {tc} has no saved test data to write.")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.design.write_test_data_to_tfs(tc, summary=True, parameters=True)
            return f"Test data written to test case {tc} in TFS.", f"#/test-data?tc={tc}"

        return Outcome("accepted", f"Writing the test data of test case {tc} to TFS.", job=job, owner="test_data",
                       job_title=f"Voice: test data to TFS for Test Case #{tc}", failure="The test data could not be written",
                       confirm_text=f"I will overwrite the test data summary and parameters of test case {tc} in TFS.")

    def _cmd_delete_test_data(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case's data should I delete?")
        rows = self.design.list_test_data(tc)
        if not rows:
            return Outcome("failed", f"Test case {tc} has no saved test data.")
        ids = [r["id"] for r in rows]

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            for data_id in ids:
                self.design.delete_test_data(data_id)
            left = len(self.design.list_test_data(tc))
            if left:
                raise QAError("DELETE_INCOMPLETE", f"{left} data sets are still there.")
            return f"Deleted {_plural(len(ids), 'data set')} of test case {tc}.", f"#/test-data?tc={tc}"

        return Outcome("accepted", f"Deleting the test data of test case {tc}.", job=job, owner="test_data",
                       job_title=f"Voice: deleting test data of Test Case #{tc}", failure="The test data could not be deleted",
                       confirm_text=f"I will delete {_plural(len(ids), 'data set')} of test case {tc}. This cannot be undone.")

    # ---- Script Studio ------------------------------------------------------------------------

    def _studio_script(self, tc: int) -> dict[str, Any] | None:
        if self.studio is None:
            return None
        return next((s for s in self.studio.inventory() if str(s.get("test_case_id") or "") == str(tc)), None)

    def _cmd_open_script(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case's script should I open?")
        script = self._studio_script(tc)
        if not script:
            return Outcome("failed", f"Test case {tc} has no automated script yet. Say generate automation for test case {tc}.")
        conv.test_case_id, conv.script_id = tc, script["id"]
        tab = intent.params.get("tab") or "view"
        if not script.get("framework"):
            return Outcome("completed", f"Opening the recorded steps of test case {tc}.",
                           navigate=f"#/ai-automation-classic?script={script['id']}")
        tab = STUDIO_TAB.get(tab, tab)
        run = f"&run={script['last_run_id']}" if tab in ("results", "logs", "evidence") and script.get("last_run_id") else ""
        label = {"view": "script", "edit": "script editor", "flow": "flow logic", "data": "test data",
                 "credentials": "credentials", "history": "version history", "results": "latest result", "logs": "logs",
                 "evidence": "evidence"}.get(tab, tab)
        return Outcome("completed", f"Opening the {label} of test case {tc}.",
                       navigate=f"#/ai-automation?script={script['id']}&tab={tab}{run}")

    def _cmd_archive_script(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case's script should I archive?")
        script = self._studio_script(tc)
        if not script or not script.get("framework"):
            return Outcome("failed", f"Test case {tc} has no generated framework script to archive.")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.studio.archive(script["id"])
            return f"The script of test case {tc} was archived. Its versions are kept.", "#/ai-automation"

        return Outcome("accepted", f"Archiving the script of test case {tc}.", job=job, owner="automation_engineering",
                       job_title=f"Voice: archiving script of Test Case #{tc}", failure="The script could not be archived",
                       confirm_text=f"I will archive the automated script of test case {tc}.")

    # ---- recording and browser actions ------------------------------------------------------

    def _active_recording(self, conv: Conversation) -> str | None:
        recs = getattr(self.automation, "recordings", {})
        if conv.recording_run_id and conv.recording_run_id in recs and recs[conv.recording_run_id]["session"].active:
            return conv.recording_run_id
        return next((rid for rid, r in recs.items() if r["session"].active), None)

    def _cmd_start_recording(self, conv: Conversation, intent: Intent) -> Outcome:
        if self._active_recording(conv):
            return Outcome("failed", "A browser recording is already in progress. Say stop recording first.")
        url = intent.params.get("url") or conv.base_url
        why = "the address used earlier in this conversation" if url else ""
        if not url and conv.test_case_id:
            url, why = self._known_base_url(conv, conv.test_case_id)
        if not url:
            disc = self.automation.discovery.list(limit=1)
            if disc:
                url, why = disc[0]["base_url"], "your latest application discovery"
        if not url:
            return Outcome("completed", "Guided Browser Automation is open. Enter the application address and press "
                                        "Record, because I don't know which application to open yet.",
                           navigate="#/guided-automation")
        name = intent.params.get("scenario")
        run_id = self.automation.start_recording(url, name=name or None,
                                                 test_case_id=str(conv.test_case_id) if conv.test_case_id else None)
        conv.recording_run_id, conv.base_url = run_id, url
        return Outcome("completed", f"Recording started on {url}, taken from {why}. Use the browser, or tell me what to "
                                    "do there, for example open the calendar and select tomorrow. Say stop recording "
                                    "when you are done.", navigate=f"#/guided-automation?recording={run_id}",
                       owner="guided_browser")

    def _cmd_stop_recording(self, conv: Conversation, intent: Intent) -> Outcome:
        run_id = self._active_recording(conv)
        if not run_id:
            return Outcome("failed", "No browser recording is in progress.")
        script = self.automation.stop_recording(run_id)
        conv.recording_run_id, conv.script_id = None, script.id
        return Outcome("completed", f"Recording saved as {script.name} with {_plural(len(script.steps), 'step')}.",
                       navigate=f"#/guided-automation?script={script.id}", owner="guided_browser")

    def _cmd_browser_action(self, conv: Conversation, intent: Intent) -> Outcome:
        run_id = self._active_recording(conv)
        if not run_id:
            return Outcome("failed", "That operation is only available during a browser recording. Say start manual "
                                     "recording first.")
        p = dict(intent.params)
        if p.get("action") == "fill" and not p.get("value"):
            return Outcome("needs_input", "Which value should I enter, and in which field?")
        done = self.automation.voice_action(run_id, p)
        return Outcome("completed", done, owner="guided_browser")

    # ---- results and reports ----------------------------------------------------------------

    def _cmd_show_result(self, conv: Conversation, intent: Intent) -> Outcome:
        what = intent.params.get("what") or "result"
        run_id = conv.run_id if conv.run_kind == "functional" else None
        if not run_id:
            latest = next((r for r in self.runs.list(limit=100) if r["kind"] == "functional"), None)
            run_id = latest["run_id"] if latest else None
        if not run_id:
            return Outcome("completed", "There are no test runs yet.", navigate="#/history")
        conv.run_id, conv.run_kind = run_id, "functional"
        run = self.runs.get(run_id) or {}
        return Outcome("completed", f"Showing the {what} of run {run_id}, which {str(run.get('status', 'ran')).lower()}.",
                       navigate=f"#/history?run={run_id}")

    def _cmd_generate_report(self, conv: Conversation, intent: Intent) -> Outcome:
        if self.reports is None:
            return Outcome("failed", "The reporting service is not available.")
        scope = intent.params.get("scope") or "both"
        days = int(intent.params.get("days") or 30)
        params: dict[str, Any] = {"scope": scope, "days": days}
        if conv.user_story_id:
            params["user_story_id"] = conv.user_story_id

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            report = self.reports.start(params, background=False)
            if report.get("status") != "completed":
                raise QAError("REPORT_FAILED", report.get("error") or "The report did not complete.")
            pdf = "" if (report.get("summary") or {}).get("pdf_error") else " and PDF"
            return f"The {scope if scope != 'both' else 'QA'} report for the last {days} days is ready as HTML{pdf}.", \
                f"#/reports?id={report['id']}"

        return Outcome("accepted", f"Building the {scope if scope != 'both' else 'functional and performance'} report "
                                   f"for the last {days} days.", job=job, owner="reporting",
                       job_title=f"Voice: {scope} report", failure="The report could not be generated")

    def _cmd_show_report(self, conv: Conversation, intent: Intent) -> Outcome:
        if self.reports is None:
            return Outcome("failed", "The reporting service is not available.")
        items = [r for r in self.reports.list(10) if r.get("status") == "completed"]
        if not items:
            return Outcome("completed", "There are no finished reports yet. Say generate the report.", navigate="#/reports")
        r = items[0]
        return Outcome("completed", f"Showing the latest report, {r['title']}.", navigate=f"#/reports?id={r['id']}")

    # ---- connections and AI -------------------------------------------------------------------

    def _cmd_test_tfs(self, conv: Conversation, intent: Intent) -> Outcome:
        mode = intent.params.get("mode") or self.tfs.active_mode()
        label = "online" if mode == "online" else "offline"

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            result = self.tfs.test_connection(mode)
            if self.health is not None:
                self.health.tfs_tested(result)
            checks = result.get("checks") or []
            if result.get("ok"):
                return f"TFS {label} connection passed all {len(checks)} checks.", "#/tfs-settings"
            failed = next((c for c in checks if c["status"] == "FAIL"), None)
            reason = f"{failed['name']} failed. {failed['detail']}" if failed else "The connection test failed."
            raise QAError("TFS_TEST_FAILED", f"TFS {label} connection failed. {reason}")

        return Outcome("accepted", f"Testing the {label} TFS connection.", job=job, owner="orchestration",
                       job_title=f"Voice: TFS {label} connection test", failure="The TFS test failed")

    def _cmd_switch_tfs(self, conv: Conversation, intent: Intent) -> Outcome:
        mode = intent.params.get("mode")
        if mode not in ("online", "offline"):
            return self._ask(conv, intent, "mode", "Online or offline TFS?")
        if mode == self.tfs.active_mode():
            return Outcome("completed", f"The {mode} TFS profile is already active.")
        profile = self.tfs.profile(mode)
        if not profile.get("url"):
            return Outcome("failed", f"The {mode} TFS profile has no server address yet. Set it on TFS Connection.",
                           navigate="#/tfs-settings")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            self.settings.update({"tfs": {"active_mode": mode}})
            self.tfs.invalidate()
            return f"The {mode} TFS profile, {profile['project'] or 'no project'}, is now active.", "#/tfs-settings"

        return Outcome("accepted", f"Switching to the {mode} TFS profile.", job=job, owner="orchestration",
                       job_title=f"Voice: switching to {mode} TFS", failure="The TFS profile could not be switched",
                       confirm_text=f"I will make the {mode} TFS profile the active connection for every page.")

    def _cmd_test_ai(self, conv: Conversation, intent: Intent) -> Outcome:
        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            result = self.ai.diagnostics()
            if self.health is not None:
                self.health.ai_tested(result)
            checks = result.get("checks") or []
            if result.get("ok"):
                return f"The local AI passed all {len(checks)} checks. Model {result.get('model') or 'loaded'} is ready.", \
                    "#/ai-settings"
            failed = next((c for c in checks if c["status"] == "FAIL"), None)
            raise QAError("AI_TEST_FAILED", f"{failed['name']} failed. {failed['detail']}" if failed else "Diagnostics failed.")

        return Outcome("accepted", "Testing the local AI.", job=job, owner="orchestration",
                       job_title="Voice: On-Premise AI diagnostics", failure="The local AI test failed")

    def _cmd_read_notifications(self, conv: Conversation, intent: Intent) -> Outcome:
        if self.notifications is None:
            return Outcome("failed", "Notifications are not available.")
        items = self.notifications.list(limit=3, unread_only=True)
        if not items:
            return Outcome("completed", "There are no unread notifications.")
        said = " ".join(f"{n['title']}." for n in items)
        return Outcome("completed", f"{_plural(len(items), 'unread notification')}: {said}", navigate="#/notifications")

