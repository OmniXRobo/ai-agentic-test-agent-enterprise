"""Voice permissions, interruptions and conversation helpers (23.3.6, milestone 9).

Voice permissions: the application has no user accounts (one person, on this PC), so what the voice agent may do is
a profile in Voice Settings, checked for every command before it runs, including a confirmed one:
* viewer: look things up and navigate only,
* tester: also create and generate, run tests, drive the guided browser and change run settings; no deletions,
* full (default, the behaviour before this version): everything; deletions and consequential actions still ask first.

Interruptions: "stop" / "wait" drops the question the agent was waiting on and stops what can be stopped safely (a
running test, a performance test, a script generation job). Nothing already saved is undone, and the reply says so.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..clock import now
from .intents import Intent
from .model import Conversation, Outcome

ROLES = ("viewer", "tester", "full")
ROLE_NAMES = {"viewer": "Viewer", "tester": "Tester", "full": "Full"}
# What each command does; anything not listed is a lookup ("read").
KIND = {
    **{c: "write" for c in ("generate_test_cases", "generate_test_plan", "save_test_cases", "create_test_data",
                            "write_test_data", "generate_automation", "automate_user_story", "create_test_plan",
                            "update_test_plan", "select_suites", "save_suites", "generate_suite_cases",
                            "generate_test_data", "generate_report", "create_defects", "automate_selection",
                            "regenerate_script")},
    **{c: "delete" for c in ("delete_test_data", "archive_script", "delete_suite")},
    **{c: "run" for c in ("run_test_case", "run_all_tests", "run_performance")},
    **{c: "browser" for c in ("start_recording", "stop_recording", "browser_action")},
    **{c: "settings" for c in ("automation_settings", "switch_tfs")},
}
ALLOWED = {"viewer": {"read"}, "tester": {"read", "write", "run", "browser", "settings"},
           "full": {"read", "write", "run", "browser", "settings", "delete"}}
WHAT = {"write": "creating or changing test assets", "delete": "deleting", "run": "running tests",
        "browser": "driving the guided browser", "settings": "changing settings"}


def _ago(iso: str | None) -> str:
    try:
        then = datetime.fromisoformat(iso)
        secs = max(0, int((now() - then).total_seconds()))
    except (TypeError, ValueError):
        return ""
    if secs < 60:
        return f" {secs} seconds ago"
    return f" {secs // 60} minute{'s' if secs // 60 != 1 else ''} ago"


class SafetyCommands:
    """Relies on VoiceService: self.config, self.db, self.studio, _cmd_stop_run."""

    def voice_role(self) -> str:
        role = str(self.config().get("voice_role") or "full").lower()
        return role if role in ROLES else "full"

    def _permission_denied(self, intent: Intent) -> Outcome | None:
        kind = KIND.get(intent.command, "read")
        if intent.command == "automation_settings" and not {k for k in intent.params if k != "confirmed"} - {"framework"}:
            kind = "read"  # reading the settings back
        from ..auth import current_user  # 23.3.34: a view-only account may look things up and run tests only

        user = current_user()
        if user is not None and user.view_only and kind not in ("read", "run"):
            return Outcome("failed", f"Your account is view only, which doesn't allow {WHAT[kind]}, so I didn't do it. "
                                     "Ask your admin if you need more access.")
        role = self.voice_role()
        if kind in ALLOWED[role]:
            return None
        return Outcome("failed", f"Your voice permissions are set to {ROLE_NAMES[role]}, which doesn't allow "
                                 f"{WHAT[kind]} by voice, so I didn't do it. Voice permissions are in Voice Settings.")

    # ---- interruptions and follow-ups ---------------------------------------------------------------------------

    def _cmd_interrupt(self, conv: Conversation, intent: Intent) -> Outcome:
        said = []
        if conv.pending:
            conv.pending = None
            said.append("dropped the question I was waiting on")
        stopped = self._cmd_stop_run(conv, intent)
        if not stopped.say.startswith("Nothing"):
            said.append(stopped.say[0].lower() + stopped.say[1:].rstrip("."))
        if self.studio is not None:
            n = 0
            for j in self.studio.jobs(20):
                if j.get("status") == "RUNNING" and self.studio.stop_job(j["id"]):
                    n += 1
            if n:
                said.append(f"stopped {n} script generation{'s' if n != 1 else ''} after the current test case")
        if self._active_recording(conv):
            said.append("left the guided browser open; say stop recording to end it")
        if not said:
            return Outcome("completed", "Stopped. Nothing was running.")
        text = "; ".join(said)
        return Outcome("completed", f"Stopped. I {text}. Anything already saved stays saved.")

    def _last_row(self, conv: Conversation) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM voice_commands WHERE conversation_id = ? AND command IS NOT NULL AND"
                                 " command NOT IN ('repeat', 'explain_activity', 'continue_work') ORDER BY id DESC LIMIT 1",
                                 (conv.id,))

    def _cmd_repeat(self, conv: Conversation, intent: Intent) -> Outcome:
        row = self._last_row(conv)
        if row is None:
            return Outcome("completed", "I haven't said anything yet.")
        return Outcome("completed", row.get("final_reply") or row.get("reply") or "I have nothing to repeat.")

    def _cmd_continue_work(self, conv: Conversation, intent: Intent) -> Outcome:
        if conv.pending:
            if conv.pending["kind"] == "confirm":
                return Outcome("unclear", "I'm waiting for your confirmation. Say confirm to continue, or cancel.")
            return Outcome("unclear", "I'm waiting for your answer to my last question.")
        return Outcome("completed", "There's nothing paused. Tell me what you'd like to do next.")

    def _cmd_explain_activity(self, conv: Conversation, intent: Intent) -> Outcome:
        rows = self.db.query("SELECT reply, created_at FROM voice_commands WHERE conversation_id = ? AND status = 'running'"
                             " ORDER BY id", (conv.id,))
        if rows:
            parts = [f"{r['reply'].rstrip('.')}, started{_ago(r['created_at'])}" for r in rows[:3]]
            return Outcome("completed", "Right now: " + "; ".join(parts) + ". I'll tell you when it's done.")
        row = self._last_row(conv)
        last = (row.get("final_reply") or row.get("reply")) if row else None
        return Outcome("completed", "Nothing is running right now." + (f" The last thing I did: {last}" if last else ""))


def interruption_intent(t: str):  # -> Intent | None
    from .intents import Intent as _Intent
    if re.fullmatch(r"(?:please\s+)?(?:stop|wait|hold on|hold it|pause|stop that|stop it|stop everything|"
                    r"cancel everything|stop stop)(?:\s+please)?", t):
        return _Intent("interrupt")
    if re.fullmatch(r"(?:please\s+)?(?:repeat(?: that| it)?|say (?:that|it) again|what did you (?:just )?say|come again|"
                    r"pardon)(?:\s+please)?", t):
        return _Intent("repeat")
    if re.fullmatch(r"(?:please\s+)?(?:continue|go on|carry on|resume|keep going)(?:\s+please)?", t):
        return _Intent("continue_work")
    if re.fullmatch(r"(?:please\s+)?(?:explain )?what (?:are|were) you doing(?: now| right now)?|explain what you(?:'re| are) "
                    r"doing|what(?:'s| is) going on", t):
        return _Intent("explain_activity")
    return None
