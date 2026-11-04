"""Voice commands for Guided Browser Automation (23.3.4, milestone 7).

Mixin for VoiceService, before the older mixins. It drives the existing Guided Browser Automation recorder: the
same Playwright session, the same page recorder and the same saved script. What is added here:
* "launch my application" opens the application under test in that session (the browser chosen in the automation
  settings, visible on a desktop) and records,
* spoken actions get the value they still need by asking ("Which country?"), and keep the capitals as said,
* the saved application credentials are used for usernames and passwords; a password is never taken by voice.
The browser side (waiting, verifying each result, recording rules) is automation/guided_voice.py.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable
from urllib.parse import urlparse

from ..errors import QAError
from .commands_plans import original_case
from .intents import Intent
from .model import Conversation, Outcome


# Actions with consequences outside the test session: confirmed first, unless the application is an approved test app.
CONSEQUENTIAL = re.compile(r"\b(submit|pay|payment|purchase|buy|place (?:the |my )?order|order now|check ?out|delete|remove|"
                           r"transfer|send|book|confirm|approve|reject|sign|cancel (?:the |my )?(?:order|booking|account|"
                           r"subscription)|close (?:the |my )?account|unsubscribe|withdraw|deposit)\b", re.I)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class GuidedCommands:
    """Relies on VoiceService: self.automation, self._active_recording, self._ask, _run_settings (AutomationCommands)."""

    def _configured_app(self, spoken: str) -> tuple[dict[str, Any] | None, str]:
        """The Application Credentials entry the person named ("e-commerce" -> "E-Commerce Shop"), and why not."""
        rows = [r for r in self.automation.creds.list() if r.get("application")]
        apps: dict[str, dict[str, Any]] = {}
        for r in rows:  # one entry per application; the one with a login address wins
            cur = apps.get(r["application"])
            if cur is None or (not cur.get("login_url") and r.get("login_url")):
                apps[r["application"]] = r
        if not apps:
            return None, ("No test applications are configured yet. Add one in Application Credentials with its login "
                          "address, then say open my application by its name.")
        want = _key(spoken)
        scored = []
        for name, r in apps.items():
            k = _key(name)
            score = 1.0 if want and (want in k or k in want) else SequenceMatcher(None, want, k).ratio()
            scored.append((score, name))
        scored.sort(reverse=True)
        if not scored or scored[0][0] < 0.7:
            names = sorted(apps)
            listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
            return None, (f"I don't have an application called {spoken} configured, so I didn't open anything. "
                          f"The configured test applications are {listed}.")
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            return None, f"{spoken} matches {scored[0][1]} and {scored[1][1]}. Which one should I open?"
        app = apps[scored[0][1]]
        if not app.get("login_url"):
            return None, (f"{app['application']} has no login address in Application Credentials, so I don't know where "
                          "to open it. Add the address there.")
        return app, ""

    def _cmd_start_recording(self, conv: Conversation, intent: Intent) -> Outcome:
        if self._active_recording(conv):
            if intent.params.get("launch"):
                return Outcome("completed", "An application is already open in the guided browser session. Tell me "
                                            "what to do there, or say stop recording first.")
            return Outcome("failed", "A browser recording is already in progress. Say stop recording first.")
        application = None
        if intent.params.get("application"):  # a named third-party application: only configured ones are opened
            app, why_not = self._configured_app(str(intent.params["application"]))
            if app is None:
                return Outcome("failed" if "Which one" not in why_not else "unclear", why_not)
            url, why, application = app["login_url"], f"the {app['application']} entry in Application Credentials", app["application"]
        else:
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
        from ..automation.browser import default_headless
        browser = self._run_settings()["browser"]
        run_id = self.automation.start_recording(url, name=intent.params.get("scenario") or None,
                                                 test_case_id=str(conv.test_case_id) if conv.test_case_id else None,
                                                 browser=browser, headless=default_headless(None), application=application)
        conv.recording_run_id, conv.base_url = run_id, url
        what = f"your configured {application} test application" if application else "your application"
        if not intent.params.get("launch"):
            what = "the recording"
        nav = f"#/guided-automation?recording={run_id}"
        return Outcome("accepted", f"I'm opening {what} at {url}, taken from {why}.", navigate=nav,
                       job=self._launch_job(run_id, application), owner="guided_browser",
                       job_title=f"Voice: opening {application or url} in the guided browser",
                       failure="The application could not be opened")

    def _launch_job(self, run_id: str, application: str | None) -> Callable[..., tuple[str, str | None]]:
        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            rec = self.automation.recording(run_id)
            session = rec["session"]
            try:  # answered by the recording thread once the browser shows the page: proof it really opened
                url = session.command("url", timeout=90)
            except QAError:
                url = None
            except Exception:
                url = None
            if not url:
                time.sleep(0.2)
                reason = session.error or "the browser session ended"
                raise QAError("BROWSER_UNAVAILABLE", f"the browser could not be started ({reason}). Browser access is "
                                                     "needed for this; check the automation browser in Settings.")
            host = urlparse(url).netloc or url
            return (f"{application or 'Your application'} is open at {host}. Recording is on, so every step you do or "
                    "tell me to do is saved as a reusable automation step. Say stop recording when you are done.",
                    f"#/guided-automation?recording={run_id}")

        return job

    def _cmd_browser_action(self, conv: Conversation, intent: Intent) -> Outcome:
        run_id = self._active_recording(conv)
        if not run_id:
            return Outcome("failed", "That works in the guided browser session. Say launch my application, or start "
                                     "manual recording, first.")
        p = dict(intent.params)
        for key in ("value", "target", "text"):
            if p.get(key) and conv.raw_text:
                p[key] = original_case(conv.raw_text, str(p[key]))  # "Smart TV 55", "United States", as said
        from .privacy import is_secret_field
        if p.get("action") == "fill" and p.get("value") and is_secret_field(str(p.get("target") or "")):
            return Outcome("failed", "I don't type passwords or codes said aloud. Say enter the password, and I'll use "
                                     "the one saved in Application Credentials.")
        rec = self.automation.recording(run_id)
        session: Any = rec["session"]
        ask = self._needs_confirmation(conv, intent, p, rec)
        if ask:
            return ask
        session.creds = self.automation.creds  # resolved on the browser side; never passed through the voice layer
        session.application = rec.get("application")
        try:
            done = self.automation.voice_action(run_id, p)
        except QAError as exc:
            if exc.error_code == "VOICE_VALUE_NEEDED":
                return self._ask(conv, Intent("browser_action", p, via=intent.via), "value", exc.message)
            if exc.error_code == "VOICE_CREDENTIAL_MISSING" and p.get("field") == "username":
                ask = Intent("browser_action", {"action": "fill", "target": p.get("target") or "username"}, via=intent.via)
                return self._ask(conv, ask, "value", exc.message.split(". ")[0] + ". What username should I enter?")
            if exc.error_code == "VOICE_CREDENTIAL_MISSING":
                return Outcome("failed", exc.message + " I don't take passwords by voice.")
            return Outcome("failed", exc.message)
        return Outcome("completed", done, owner="guided_browser")

    def _needs_confirmation(self, conv: Conversation, intent: Intent, p: dict[str, Any], rec: dict[str, Any]) -> Outcome | None:
        if p.get("confirmed"):
            return None
        action, target = p.get("action"), str(p.get("target") or "")
        if action != "submit" and not (action == "click" and CONSEQUENTIAL.search(target)):
            return None
        app = rec.get("application") or urlparse(rec.get("start_url") or "").netloc
        approved = {_key(a) for a in (self.config().get("approved_test_apps") or [])}
        if _key(app) in approved:
            return None
        conv.pending = {"kind": "confirm", "intent": Intent("browser_action", p, via=intent.via), "at": time.monotonic()}
        what = "submit this form" if action == "submit" else f"click {target}"
        return Outcome("needs_confirmation", f"I will {what} in {app}, which can send, order, pay or change data there. "
                                             "Say confirm to continue, or cancel.")
