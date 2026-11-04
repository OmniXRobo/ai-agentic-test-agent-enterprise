"""Voice commands for AI-Driven Automation (23.3.3, milestone 6).

Mixin for VoiceService, placed before the older command mixins so the commands below can extend theirs. Everything
goes through the engines the AI-Driven Automation page (Script Studio) uses:
* run settings are the existing "playback" settings the framework reads (browser, element timeout, step screenshots,
  video, trace, headless); failure screenshots are always captured by the framework, and it has no retry setting,
* ScriptStudio.start_job generates Python Playwright scripts (Page Objects, flows, test data) for test cases; a
  script is saved only after it passed the framework validation, and traceability to the user story and its
  acceptance criteria is kept by the studio,
* validator.validate re-checks a saved script (Python syntax, imports, Page Object members, fixtures, collection),
* ScriptStudio.run executes a script and returns its real result; nothing is reported passed without one,
* ScriptStudio.regenerate rediscovers the application and rewrites what changed (old versions stay in History).
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..errors import QAError
from .commands_plans import _and
from .intents import Intent
from .model import Conversation, Outcome

BROWSER_NAMES = {"chromium": "Chromium", "chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox",
                 "webkit": "WebKit"}
TIMEOUT_RANGE = (1, 120)  # seconds an element may take to appear
DONE_ITEMS = {"GENERATED", "UPDATED", "UNCHANGED"}
MAX_CASES = 200
JOB_WAIT_SECONDS = 45 * 60  # a voice command stops waiting for a generation job after this
PRODUCTION = ("prod", "production", "live")


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def browser_available(name: str) -> tuple[bool, str]:
    """Whether Playwright can start ``name`` on this PC, and why not. Chromium is bundled with the application."""
    if name == "chromium":
        return True, ""
    if name in ("chrome", "msedge"):
        if sys.platform == "win32":
            sub = r"Google\Chrome\Application\chrome.exe" if name == "chrome" else r"Microsoft\Edge\Application\msedge.exe"
            roots = [os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
            found = any(r and Path(r, sub).is_file() for r in roots)
        elif sys.platform == "darwin":
            app = "Google Chrome" if name == "chrome" else "Microsoft Edge"
            found = Path(f"/Applications/{app}.app").exists()
        else:
            found = bool(shutil.which("google-chrome" if name == "chrome" else "microsoft-edge")) or \
                Path("/opt/google/chrome/chrome" if name == "chrome" else "/opt/microsoft/msedge/msedge").is_file()
        return found, "" if found else f"{BROWSER_NAMES[name]} is not installed on this PC"
    from ..automation.browser import ensure_browsers_path
    ensure_browsers_path()
    folders = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
               os.path.join(os.environ.get("LOCALAPPDATA") or "", "ms-playwright"),
               os.path.join(os.path.expanduser("~"), ".cache", "ms-playwright")]
    for f in folders:
        if f and os.path.isdir(f) and any(d.startswith(f"{name}-") for d in os.listdir(f)):
            return True, ""
    return False, f"the Playwright {BROWSER_NAMES[name]} browser is not installed with this application"


def _suite_key(name: str) -> str | None:
    from ..design import testing_types
    t = testing_types.from_suite_name(name)
    return (t or {}).get("key") if isinstance(t, dict) else t


class AutomationCommands:
    """Relies on VoiceService: self.settings, self.studio, self.ai, self.tfs, self.automation, _current_plan, _ask."""

    # ---- run settings -------------------------------------------------------------------------------------------

    def _run_settings(self) -> dict[str, Any]:
        from ..automation.browser import default_headless
        pb = self.settings.section("playback")
        return {"browser": pb.get("browser") or os.environ.get("BROWSER") or "chromium",
                "timeout_seconds": pb.get("timeout_seconds") or 10, "step_screenshots": pb.get("step_screenshots", True),
                "video": bool(pb.get("video")), "trace": bool(pb.get("trace")),
                "headless": default_headless(pb.get("headless"))}

    def _settings_line(self) -> str:
        s = self._run_settings()
        t = s["timeout_seconds"]
        return (f"Scripts are Python with Playwright. Runs use {BROWSER_NAMES.get(s['browser'], s['browser'])}"
                f"{' in the background' if s['headless'] else ' with the browser visible'}, wait up to "
                f"{int(t) if float(t).is_integer() else t} seconds for an element, take "
                f"{'a screenshot after every step and ' if s['step_screenshots'] else ''}a screenshot on every failure, "
                f"{'record' if s['video'] else 'do not record'} video and {'save' if s['trace'] else 'do not save'} a trace.")

    def _cmd_automation_settings(self, conv: Conversation, intent: Intent) -> Outcome:
        p = {k: v for k, v in intent.params.items() if k != "confirmed"}
        if not p:
            return Outcome("completed", self._settings_line() + " They are on the Settings page.", navigate="#/settings")
        if "framework" in p:
            if p["framework"] == "python":
                return Outcome("completed", "Python with Playwright is the automation framework here, so it's already "
                                            "selected. Say generate the automation script when you're ready.")
            return Outcome("failed", f"This application generates Python with Playwright scripts only, so I can't make "
                                     f"{p['framework'].title()} scripts. Say generate the Python script to use Python "
                                     "with Playwright.")
        if "retries" in p:
            return Outcome("failed", "The automation framework has no retry setting, so I haven't changed anything. A "
                                     "failed test is reported exactly as it ran; say run it again to repeat it.")
        if "failure_screenshots" in p:
            if p["failure_screenshots"]:
                return Outcome("completed", "Screenshots on failure are already on: the framework always captures the "
                                            "page when a step fails, and they are on the Evidence tab.")
            return Outcome("failed", "Failure screenshots are always kept as evidence, so I left them on. I can turn off "
                                     "the screenshot after every step instead.")
        patch: dict[str, Any] = {}
        said: list[str] = []
        if "browser" in p:
            ok, why = browser_available(p["browser"])
            if not ok:
                return Outcome("failed", f"I kept the current browser, because {why}. Chromium and Microsoft Edge "
                                         "work on every Windows PC.")
            patch["browser"] = p["browser"]
            said.append(f"run in {BROWSER_NAMES[p['browser']]}")
        if "timeout_seconds" in p:
            t = p["timeout_seconds"]
            lo, hi = TIMEOUT_RANGE
            if t is None:
                return self._ask(conv, intent, "timeout_seconds", "How many seconds should the tests wait for an element?")
            if not lo <= float(t) <= hi:
                return Outcome("unclear", f"The timeout can be from {lo} to {hi} seconds. How many seconds should it be?")
            patch["timeout_seconds"] = int(t) if float(t).is_integer() else float(t)
            said.append(f"wait up to {patch['timeout_seconds']} seconds for an element")
        for key, on, off in (("step_screenshots", "take a screenshot after every step", "take screenshots only on failure"),
                             ("video", "record a video", "not record video"), ("trace", "save a Playwright trace",
                                                                                "not save a trace"),
                             ("headless", "run in the background", "run with the browser visible")):
            if key in p:
                patch[key] = bool(p[key])
                said.append(on if p[key] else off)
        if not patch:
            return Outcome("unclear", "Which automation setting should I change? For example: change the browser to "
                                      "Chrome, or set the timeout to 30 seconds.")
        self.settings.update({"playback": patch})
        saved = self.settings.section("playback")
        if any(saved.get(k) != v for k, v in patch.items()):  # read back: report only what was really saved
            return Outcome("failed", "The automation settings could not be saved.")
        return Outcome("completed", f"Saved. Automation runs will now {_and(said)}.")

    # ---- generate scripts for the current plan, suite or test cases ---------------------------------------------

    def _plan_cases(self, conv: Conversation, intent: Intent) -> tuple[list[int], str] | Outcome:
        p = intent.params
        if p.get("which") == "these" or (not p.get("which") and conv.saved_case_ids and conv.focus == "test_cases"):
            if not conv.saved_case_ids:
                return Outcome("unclear", "Which test cases? Save generated test cases first, or say generate automation "
                                          "for test case and its number.")
            ids = list(conv.saved_case_ids)
            return ids, f"the {_n(len(ids), 'test case')} you just saved"
        plan = self._current_plan(conv)
        if plan is None:
            if not p.get("which") and conv.test_case_id:
                return [int(conv.test_case_id)], f"test case {conv.test_case_id}"
            return Outcome("unclear", "Which test plan, suite or test cases should I automate? Say, for example, find the "
                                      "test plan for Sprint 25, or generate automation for test case 105.")
        suites = [s for s in self.tfs.test_suites(plan["id"]) if s.get("parent_id")]
        if p.get("suite"):
            suites = [s for s in suites if _suite_key(s["name"]) == p["suite"]]
            if not suites:
                return Outcome("unclear", f"{plan['name']} has no {p['suite'].title()} Testing suite. Say which suites "
                                          "does it have to hear them.")
        elif conv.suite_selection:
            chosen = {k for k, v in conv.suite_selection.items() if v}
            picked = [s for s in suites if _suite_key(s["name"]) in chosen]
            suites = picked or suites
        if not suites:
            return Outcome("unclear", f"{plan['name']} has no test suites yet. Select the suites first, for example: "
                                      "select functional and regression.")
        ids: list[int] = []
        for s in suites:
            ids += [int(c["id"]) for c in self.tfs.suite_test_cases(plan["id"], s["id"])]
        ids = list(dict.fromkeys(ids))
        names = _and([s["name"] for s in suites])
        if not ids:
            return Outcome("unclear", f"The {names} suite{'s have' if len(suites) > 1 else ' has'} no test cases in TFS "
                                      "yet. Say generate test cases from the acceptance criteria, then save them.")
        return ids, f"{_n(len(ids), 'test case')} in {names} of {plan['name']}"

    def _automation_base_url(self, conv: Conversation, tc: int) -> tuple[str | None, str]:
        url, why = self._known_base_url(conv, tc)
        if url:
            return url, why
        row = self.db.query_one("SELECT base_url FROM framework_scripts WHERE base_url IS NOT NULL AND base_url != ''"
                                " ORDER BY updated_at DESC LIMIT 1")
        return (row["base_url"], "your latest generated script") if row else (None, "")

    def _cmd_automate_selection(self, conv: Conversation, intent: Intent) -> Outcome:
        if self.studio is None:
            return Outcome("failed", "AI-Driven Automation is not available in this runtime.")
        found = self._plan_cases(conv, intent)
        if isinstance(found, Outcome):
            return found
        ids, what = found
        if len(ids) > MAX_CASES:
            return Outcome("unclear", f"That is {len(ids)} test cases. I can generate up to {MAX_CASES} at a time; "
                                      "say which suite to start with.")
        if not self.ai.config()["base_url"]:
            return Outcome("failed", "Automation generation needs the On-Premise AI, and no AI server is configured.",
                           navigate="#/ai-settings")
        base_url, why = self._automation_base_url(conv, ids[0])
        if not base_url:
            return Outcome("completed", "AI-Driven Automation is open. Enter the application address there, because I "
                                        "don't know which application to test yet.", navigate="#/ai-automation?tab=generate")
        conv.base_url = base_url
        s = self._run_settings()
        say = (f"I'm checking the selected test cases and automation parameters. I'm generating Python Playwright "
               f"scripts for {what}, for the application at {base_url} ({why}). They will run in "
               f"{BROWSER_NAMES.get(s['browser'], s['browser'])}.")
        return Outcome("accepted", say, job=self._generation_job(conv, ids, base_url, what), owner="requirements",
                       job_title=f"Voice: generating automation for {_n(len(ids), 'test case')}",
                       failure="Automation generation failed")

    def _generation_job(self, conv: Conversation, ids: list[int], base_url: str, what: str) -> Callable[..., Any]:
        from ..automation.framework.studio import JobRequest

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            job_id = self.studio.start_job(JobRequest(selections=[{"test_case_ids": ids}], base_url=base_url))
            j = self._wait_job(job_id)
            return self._job_line(conv, j, what)

        return job

    def _studio_job(self, selections: list[dict[str, Any]], base_url: str, what: str,
                    conv: Conversation | None = None) -> Callable[..., Any]:
        """Generation for one test case or user story (the older commands): same honest report as above, and the new
        script becomes "this script" for validate, run and regenerate."""
        from ..automation.framework.studio import JobRequest

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            j = self._wait_job(self.studio.start_job(JobRequest(selections=selections, base_url=base_url)))
            return self._job_line(conv or Conversation("-"), j, what)

        return job

    def _wait_job(self, job_id: str, limit: float = JOB_WAIT_SECONDS) -> dict[str, Any]:
        end = time.monotonic() + limit
        while True:  # the generation job runs in its own thread; wait for its real outcome
            j = self.studio.job(job_id)
            if j["status"] in ("COMPLETED", "FAILED", "STOPPED", "INTERRUPTED"):
                return j
            if time.monotonic() > end:
                raise QAError("VOICE_JOB_TIMEOUT", f"the job was still running after {int(limit // 60)} minutes, so I "
                                                   "stopped waiting. It goes on, and its results will be on AI-Driven "
                                                   "Automation.")
            time.sleep(0.5)

    def _job_line(self, conv: Conversation, j: dict[str, Any], what: str) -> tuple[str, str | None]:
        items = j["items"]
        made = [i for i in items if i["status"] in DONE_ITEMS and i.get("script_id")]
        skipped = [i for i in items if i["status"] == "SKIPPED"]
        failed = [i for i in items if i["status"] == "FAILED"]
        if not made and not skipped:
            first = failed[0] if failed else {}
            raise QAError("GENERATION_FAILED", first.get("error") or j.get("error") or "No script was created.")
        conv.script_ids = [i["script_id"] for i in made + skipped if i.get("script_id")]
        if conv.script_ids:
            conv.script_id = conv.script_ids[0]
            first = made[0] if made else skipped[0]
            conv.test_case_id, conv.focus = int(first["test_case_id"]), "script"
        say = f"{_n(len(made), 'Python Playwright script')} generated for {what}"
        say += (f", {len(skipped)} already existed" if skipped else "") + (f", {len(failed)} failed" if failed else "") + "."
        if made:
            say += (" Each one passed the syntax and static checks before it was saved, with links to its test case and "
                    "acceptance criteria. Nothing has been run yet.")
        if failed:
            say += f" Test case {failed[0]['test_case_id']} failed: {failed[0].get('error') or 'no reason given'}."
        if len(made) == 1:
            return say + " The code is open. Say run this test to execute it.", \
                f"#/ai-automation?script={made[0]['script_id']}&tab=view"
        return say + " Say run this test, or open the script of a test case.", f"#/ai-automation?job={j['id']}"

    # ---- one framework script -----------------------------------------------------------------------------------

    def _framework_script(self, conv: Conversation, intent: Intent) -> dict[str, Any] | None:
        if self.studio is None:
            return None
        inv = [s for s in self.studio.inventory() if s.get("framework")]
        tc = intent.params.get("test_case_id")
        if tc:
            return next((s for s in inv if str(s.get("test_case_id") or "") == str(tc)), None)
        if conv.script_id:
            hit = next((s for s in inv if s["id"] == conv.script_id), None)
            if hit:
                return hit
        if conv.test_case_id:
            return next((s for s in inv if str(s.get("test_case_id") or "") == str(conv.test_case_id)), None)
        return None

    def _no_script(self, conv: Conversation, intent: Intent, verb: str) -> Outcome:
        tc = intent.params.get("test_case_id") or conv.test_case_id
        if tc:
            return Outcome("failed", f"Test case {tc} has no generated script yet. Say generate automation for test case {tc}.")
        return self._ask(conv, intent, "test_case_id", f"Which test case's script should I {verb}? Say its number.")

    def _use(self, conv: Conversation, script: dict[str, Any]) -> str:
        conv.script_id, conv.focus = script["id"], "script"
        if str(script.get("test_case_id") or "").isdigit():
            conv.test_case_id = int(script["test_case_id"])
        return f"test case {script['test_case_id']}" if script.get("test_case_id") else script["id"]

    def _cmd_validate_script(self, conv: Conversation, intent: Intent) -> Outcome:
        script = self._framework_script(conv, intent)
        if script is None:
            return self._no_script(conv, intent, "validate")
        label = self._use(conv, script)
        sid = script["id"]

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            from ..automation.framework import validator
            rec = self.studio._record(sid)
            ws = self.studio.workspace()
            if not ws.exists(rec["file"]):
                raise QAError("SCRIPT_FILE_MISSING", f"The script file of {label} is missing; say regenerate the script.")
            files = [rec["file"]] + ([rec["data_file"]] if rec.get("data_file") and ws.exists(rec["data_file"]) else [])
            report = validator.validate(ws.root, files, test_rel=rec["file"])
            bad = [c for c in report.checks if c["status"] == "FAIL"]
            nav = f"#/ai-automation?script={sid}&tab=view"
            if bad:
                return (f"The script of {label} failed {_n(len(bad), 'check')}: "
                        + "; ".join(f"{c['name']}: {c['detail']}" for c in bad[:3])
                        + ". Say fix the locator and regenerate, or edit it on the Edit Script tab.", nav)
            return (f"The script of {label} passed all {_n(len(report.checks), 'check')}: "
                    f"{_and([c['name'] for c in report.checks][:6])}. This checks the code; it has not been run.", nav)

        return Outcome("accepted", f"I'm checking the Python syntax and the static rules of the script of {label}.",
                       job=job, owner="automation_engineering", job_title=f"Voice: validating the script of {label}",
                       failure="The script could not be validated")

    def _cmd_open_script(self, conv: Conversation, intent: Intent) -> Outcome:
        if not intent.params.get("test_case_id") and not conv.test_case_id and conv.script_id:
            script = self._framework_script(conv, intent)
            if script:
                intent = Intent(intent.command, {**intent.params, "test_case_id": script.get("test_case_id")}, via=intent.via)
        return super()._cmd_open_script(conv, intent)

    def _environments(self, script: dict[str, Any], rec: dict[str, Any]) -> set[str]:
        known = {(rec.get("environment") or "").strip().lower()} - {""}
        app = (rec.get("application") or "").strip().lower()
        for c in self.automation.creds.list():
            if c.get("environment") and (not app or (c.get("application") or "").strip().lower() == app):
                known.add(c["environment"].strip().lower())
        return known

    def _cmd_run_test_case(self, conv: Conversation, intent: Intent) -> Outcome:
        script = self._framework_script(conv, intent)
        if script is None:  # recorded (classic) scripts keep their own runner
            if intent.params.get("environment"):
                return Outcome("failed", "Running in a named environment works for generated Python Playwright scripts, "
                                         "and I couldn't find one for this test. Say generate the automation script first.")
            return self._run_recorded_test_case(conv, intent)
        label = self._use(conv, script)
        sid = script["id"]
        rec = self.studio._record(sid)
        env = (intent.params.get("environment") or "").strip().lower()
        where = rec.get("base_url") or "its application address"
        if env:
            known = self._environments(script, rec)
            simple = {env, env.replace(" ", ""), {"test": "qa", "qa": "test"}.get(env, env)}
            if any(e.startswith(PRODUCTION) for e in simple) and not intent.params.get("confirmed"):
                conv.pending = {"kind": "confirm", "intent": intent, "at": time.monotonic()}
                return Outcome("needs_confirmation", f"This runs {label} against production at {where}. Say confirm to "
                                                     "continue, or cancel.")
            if known and not (known & simple):
                return Outcome("failed", f"The script of {label} is set up for {_and(sorted(known))}, not the {env} "
                                         "environment, so I didn't run it. Add that environment in Application "
                                         "Credentials, or say run this test.")
            if not known:
                where = f"{where}, the address it was generated for, because no environment is set for it"
        play = bool(intent.params.get("play"))
        s = self._run_settings()
        browser = BROWSER_NAMES.get(s["browser"], s["browser"])

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            result = self.studio.run(sid, headless=False if play else None, background=False)
            conv.run_id, conv.run_kind = result["run_id"], "functional"
            nav = f"#/ai-automation?script={sid}&tab=results&run={result['run_id']}"
            return self._run_line(label, result), nav

        return Outcome("accepted", f"I'm running {label} in {browser} on {where}.", navigate="#/workflow", job=job,
                       owner="test_execution", job_title=f"Voice: running {label}", failure="The test could not run")

    def _run_line(self, label: str, result: dict[str, Any]) -> str:
        steps = result.get("steps") or []
        if result.get("status") == "PASSED":
            healed = result.get("healed") or []
            extra = " A locator failed and was healed during the run; say show me the failure for details." if healed else ""
            return f"{label[0].upper()}{label[1:]} passed: {_n(len(steps), 'step')} ran and the evidence is saved.{extra}"
        if result.get("status") in ("STOPPED", "CANCELLED"):
            return f"The run of {label} was stopped before it finished."
        idx = result.get("failed_step")
        step = next((x for x in steps if x.get("index") == idx), {}) if idx is not None else {}
        desc = step.get("description") or step.get("name") or ""
        where = f" at step {idx + 1}{', ' + desc if desc else ''}" if isinstance(idx, int) else ""
        err = (result.get("error") or "").splitlines()[0][:200] if result.get("error") else "no error message was recorded"
        return f"{label[0].upper()}{label[1:]} failed{where}: {err}. Say show me the failure, or fix the locator and regenerate."

    def _cmd_show_failure(self, conv: Conversation, intent: Intent) -> Outcome:
        script = self._framework_script(conv, intent)
        if script is None or not script.get("last_run_id"):
            return self._cmd_show_failed(conv, intent)
        label = self._use(conv, script)
        result = self.studio.run_result(script["id"], script["last_run_id"])
        nav = f"#/ai-automation?script={script['id']}&tab=results&run={script['last_run_id']}"
        if result.get("status") == "PASSED":
            healed = [h for h in result.get("healing") or [] if h.get("result") == "healed"]
            if healed:
                h = healed[0]
                return Outcome("completed", f"The last run of {label} passed, but the locator {h.get('original_selector')} "
                                            f"failed and was healed to {h.get('healed_selector')}. The Result tab shows it.",
                               navigate=nav)
            return Outcome("completed", f"The last run of {label} passed, so there is no failure to show.", navigate=nav)
        if result.get("status") == "RUNNING" or result.get("running"):
            return Outcome("completed", f"{label[0].upper()}{label[1:]} is still running.", navigate=nav)
        line = self._run_line(label, result)
        analysis = result.get("failure_analysis") or {}
        cause = analysis.get("summary") or analysis.get("cause") if isinstance(analysis, dict) else None
        return Outcome("completed", line.split(" Say ")[0] + (f" Likely cause: {cause}." if cause else "")
                       + " The screenshot and log are on the Result tab.", navigate=nav)

    def _cmd_regenerate_script(self, conv: Conversation, intent: Intent) -> Outcome:
        script = self._framework_script(conv, intent)
        if script is None:
            return self._no_script(conv, intent, "regenerate")
        if not self.ai.config()["base_url"]:
            return Outcome("failed", "Regenerating needs the On-Premise AI, and no AI server is configured.",
                           navigate="#/ai-settings")
        label = self._use(conv, script)
        sid, version = script["id"], script.get("version")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            j = self._wait_job(self.studio.regenerate(sid))
            item = next((i for i in j["items"] if str(i.get("test_case_id")) == str(script.get("test_case_id"))), None)
            status = (item or {}).get("status") or j["status"]
            nav = f"#/ai-automation?script={sid}&tab=view"
            if status == "UPDATED":
                new = self.studio._record(sid).get("version")
                return (f"The script of {label} was regenerated as version {new} with locators from the application as "
                        f"it is now, and it passed validation. Version {version} is kept in History. It has not been "
                        "run yet; say run this test to check the fix.", nav)
            if status == "UNCHANGED":
                return (f"I regenerated the script of {label}, and nothing changed: the page and the test case give the "
                        "same locators. Say show me the failure to look at the cause.", nav)
            raise QAError("GENERATION_FAILED", (item or {}).get("error") or j.get("error") or "The script was not regenerated.")

        return Outcome("accepted", f"I'm rediscovering the application and regenerating the script of {label}, so its "
                                   "locators come from the page as it is now.", job=job, owner="automation_engineering",
                       job_title=f"Voice: regenerating the script of {label}", failure="The script could not be regenerated")
