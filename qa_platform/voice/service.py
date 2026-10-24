"""Voice Command Agent.

USER SPEAKS -> speech recognition -> intent analysis -> command validation ->
voice acknowledgement -> execute a supported dashboard action -> monitor it ->
voice completion or failure response.

* Speech recognition runs on the bundled offline recognizer (offline_stt.py),
  in the browser, or on a local Whisper server (whisper.cpp ``/inference`` or an
  OpenAI-compatible ``/audio/transcriptions`` endpoint) configured by URL.
  Nothing is downloaded and no other host is called.
* Replies can be rendered with the PC's Windows voices (tts.py) so the
  dashboard can play them as radio transmissions.
* Every action calls the same services the dashboard pages use; there is no
  separate automation path.
* Conversations keep context (the user story, test case, run and performance
  plan last referred to) so follow-ups like "generate automation for the smoke
  test" or "run it again" resolve to the current item.
* Commands that write to TFS ask for spoken confirmation first.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, Field

from ..agents.base import BaseAgent, new_run_id
from ..ai.gateway import AIGateway
from ..automation.service import AutomationService
from ..clock import now, now_iso
from ..config import Settings
from ..defects.service import LIKELY_DUPLICATE, DefectService
from ..design.service import DesignService
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus
from ..events.state import AgentStateManager
from ..logging_setup import get_logger
from ..performance.service import PerformanceService
from ..performance.tools import find_java, find_jmeter
from ..runs import RunStore
from ..storage.db import Database, dumps, loads
from ..tfs.service import TfsService
from . import intents
from .intents import Intent
from .offline_stt import OfflineRecognizer
from .tts import SystemVoice

log = get_logger("voice")

UNSUPPORTED = "I cannot perform that instruction because no supported QA action is mapped to it."
PENDING_SECONDS = 120
DESIGN_AGENTS = {"requirements", "test_planning", "test_case_design", "test_data", "test_suite_design"}
FAILURE_PHRASES = {
    "locator": "a locator failure on {element}",
    "assertion": "an unexpected result at {step}",
    "navigation": "a page that did not load or respond in time",
    "interaction": "an action that could not be completed on {element}",
    "test_setup": "a test setup problem: {error}",
}


class VoiceCommandAgent(BaseAgent):
    agent_id = "voice_command"


class AIVoiceIntent(BaseModel):
    """What the On-Premise AI may return when the rules do not match: one supported command or 'unsupported'."""
    command: Literal[intents.COMMANDS + ("unsupported",)]  # type: ignore[valid-type]
    user_story_id: int | None = None
    test_case_id: int | None = None
    name: str | None = Field(default=None, max_length=120)
    kind: Literal["load", "stress", "spike", "soak"] | None = None
    users: int | None = Field(default=None, ge=1, le=100000)
    duration_s: int | None = Field(default=None, ge=10, le=7 * 86400)
    page: str | None = Field(default=None, max_length=80)


AI_SYSTEM = (
    "You map a QA engineer's spoken request to exactly one command of a QA dashboard. Commands: "
    "open_page (page = the page name), open_work_item (test_case_id or user_story_id), generate_test_cases "
    "(user_story_id), generate_automation (test_case_id or name), run_test_case (test_case_id or name), "
    "run_all_tests, start_recording, run_performance (name of the plan, kind, users, duration_s), stop_run, "
    "show_failed, show_performance_report, analyze_failure, create_defects, status, help. "
    "Use only numbers and names that appear in the request; never invent IDs. If the request does not clearly "
    "ask for one of these commands, answer with command 'unsupported'. Reply with JSON only."
)


@dataclass
class Conversation:
    id: str
    user_story_id: int | None = None
    test_case_id: int | None = None
    script_id: str | None = None
    run_id: str | None = None
    run_kind: str | None = None
    perf_plan_id: str | None = None
    base_url: str | None = None
    pending: dict[str, Any] | None = None  # {"kind": "confirm"|"input", "intent": Intent, "param": str, "at": float}
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Outcome:
    status: str  # completed | accepted | failed | unsupported | needs_confirmation | needs_input
    say: str
    navigate: str | None = None
    job: Callable[[Callable[[str], None]], tuple[str, str | None]] | None = None  # (say_progress) -> (say, navigate)
    job_title: str | None = None
    failure: str = "That did not complete"


def _sec(ms: float | None) -> str:
    if ms is None:
        return "unknown"
    return f"{int(round(ms))} milliseconds" if ms < 1000 else f"{ms / 1000:.1f} seconds"


def _duration_words(s: int) -> str:
    if s % 3600 == 0:
        return f"{s // 3600}-hour"
    if s % 60 == 0:
        return f"{s // 60}-minute"
    return f"{s}-second"


def _match_score(query: str, *candidates: str | None) -> float:
    q = query.lower().strip()
    best = 0.0
    for c in candidates:
        c = (c or "").lower()
        if not c:
            continue
        if q in c:
            best = max(best, 0.9 + 0.1 * len(q) / max(len(c), 1))
        q_tokens, c_tokens = set(re.findall(r"\w+", q)), set(re.findall(r"\w+", c))
        if q_tokens and q_tokens <= c_tokens:
            best = max(best, 0.85)
        best = max(best, SequenceMatcher(None, q, c).ratio() * 0.8)
    return best


class VoiceService:
    def __init__(self, settings: Settings, bus: EventBus, db: Database, runs: RunStore, state: AgentStateManager,
                 tfs: TfsService, ai: AIGateway, design: DesignService, automation: AutomationService,
                 performance: PerformanceService, defects: DefectService,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.bus = bus
        self.db = db
        self.runs = runs
        self.state = state
        self.tfs = tfs
        self.ai = ai
        self.design = design
        self.automation = automation
        self.performance = performance
        self.defects = defects
        self.transport = transport
        self.agent = VoiceCommandAgent(bus)
        self.offline = OfflineRecognizer(settings)
        self.system_voice = SystemVoice(settings.path.parent / "voice-cache")
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    # ---- settings and speech recognition ------------------------------------------------------

    def config(self) -> dict[str, Any]:
        cfg = self.settings.section("voice")
        # A local speech server without a URL cannot work; earlier versions saved exactly that as the default.
        if cfg.get("stt_engine") == "local" and not (cfg.get("stt_url") or "").strip():
            cfg = {**cfg, "stt_engine": "builtin"}
        return cfg

    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        cfg = self.config()
        if not audio:
            raise QAError("VOICE_NO_AUDIO", "No audio was received.", status=422)
        if cfg.get("stt_engine") == "builtin":
            text = self.offline.transcribe(audio)
            if not text:
                raise QAError("VOICE_NOTHING_HEARD", "No speech was recognized. Try again a little closer to the "
                                                     "microphone.", status=422)
            return text
        if cfg.get("stt_engine") != "local":
            raise QAError("VOICE_STT_NOT_LOCAL", "Speech recognition is not set to the local speech server.", status=409)
        url = (cfg.get("stt_url") or "").rstrip("/")
        if not url:
            raise QAError("VOICE_STT_NOT_CONFIGURED", "No local speech recognition server URL is set in Voice settings.",
                          status=409)
        language = cfg.get("language") or "en"
        if cfg.get("stt_api") == "openai":
            path, data = "/audio/transcriptions", {"model": cfg.get("stt_model") or "whisper-1",
                                                   "response_format": "json", "language": language}
        else:
            path, data = "/inference", {"response_format": "json", "language": language, "temperature": "0"}
        try:
            with httpx.Client(timeout=float(cfg.get("stt_timeout_seconds") or 60), trust_env=False,
                              transport=self.transport) as c:
                resp = c.post(url + path, data=data, files={"file": ("speech.wav", audio, content_type)})
        except httpx.HTTPError as exc:
            raise QAError("VOICE_STT_UNREACHABLE", f"The speech recognition server at {url} is not reachable.",
                          status=502, details=type(exc).__name__) from exc
        if resp.status_code != 200:
            raise QAError("VOICE_STT_FAILED", f"The speech recognition server returned HTTP {resp.status_code}.",
                          status=502, details=resp.text[:300])
        try:
            text = str(resp.json().get("text") or "").strip()
        except ValueError as exc:
            raise QAError("VOICE_STT_INVALID", "The speech recognition server did not return JSON.", status=502) from exc
        text = re.sub(r"\[(BLANK_AUDIO|MUSIC|NOISE)\]|\((?:silence|music)\)", "", text, flags=re.I).strip()
        if not text:
            raise QAError("VOICE_NOTHING_HEARD", "No speech was recognized. Try again a little closer to the microphone.",
                          status=422)
        return text

    def diagnostics(self) -> dict[str, Any]:
        cfg = self.config()
        checks: list[dict[str, str]] = []
        engine = cfg.get("stt_engine")
        if engine == "builtin":
            status, detail = self.offline.status()
            checks.append({"name": "Built-in offline speech recognition", "status": status, "detail": detail})
        elif engine == "local":
            url = (cfg.get("stt_url") or "").rstrip("/")
            if not url:
                checks.append({"name": "Local speech server", "status": "NOT_CONFIGURED",
                               "detail": "Set the URL of your whisper.cpp or OpenAI-compatible speech server."})
            else:
                try:
                    with httpx.Client(timeout=5, trust_env=False, transport=self.transport) as c:
                        r = c.get(url + ("/models" if cfg.get("stt_api") == "openai" else "/"))
                    checks.append({"name": "Local speech server", "status": "PASS" if r.status_code < 500 else "FAIL",
                                   "detail": f"{url} answered HTTP {r.status_code}"})
                except httpx.HTTPError as exc:
                    checks.append({"name": "Local speech server", "status": "FAIL",
                                   "detail": f"{url} is not reachable ({type(exc).__name__})"})
        elif engine == "browser":
            checks.append({"name": "Browser speech recognition", "status": "WARN",
                           "detail": "Recognition is done by the browser. Chrome and Edge send audio to their online "
                                     "service, so this needs Internet. Use a local speech server for offline PCs."})
        else:
            checks.append({"name": "Speech recognition", "status": "NOT_CONFIGURED",
                           "detail": "Voice input is off. Typed commands still work."})
        if cfg.get("speak_replies") and cfg.get("reply_style") == "radio":
            n = len(self.system_voice.voices())
            checks.append({"name": "Radio voice (Windows voices)", "status": "PASS" if n else "WARN",
                           "detail": f"{n} Windows voices found; replies are played as radio transmissions." if n else
                           "No Windows voices here; the browser's voice is used with radio static instead."})
        name = "On-Premise AI for free-form requests"
        if not self.ai.config()["base_url"]:
            checks.append({"name": name, "status": "NOT_CONFIGURED", "detail": "Only the fixed command phrases are understood."})
        else:
            try:
                self.ai.models(timeout=3)
                checks.append({"name": name, "status": "PASS",
                               "detail": "Requests outside the fixed command phrases are mapped by the On-Premise AI."})
            except QAError as exc:
                checks.append({"name": name, "status": "WARN",
                               "detail": f"{exc.message} Only the fixed command phrases are understood until it is reachable."})
        return {"checks": checks}

    def radio_voices(self) -> dict[str, Any]:
        return {"available": self.system_voice.available(), "voices": self.system_voice.voices()}

    def speak(self, text: str) -> bytes:
        cfg = self.config()
        return self.system_voice.render(text, cfg.get("radio_voice") or "", float(cfg.get("tts_rate") or 1))

    # ---- conversations -------------------------------------------------------------------------

    def _conv(self, conversation_id: str) -> Conversation:
        with self._lock:
            conv = self._conversations.get(conversation_id)
            if conv is None:
                conv = self._conversations[conversation_id] = Conversation(conversation_id)
            return conv

    def context(self, conversation_id: str) -> dict[str, Any]:
        c = self._conv(conversation_id)
        pending = None
        if c.pending and time.monotonic() - c.pending["at"] < PENDING_SECONDS:
            pending = {"kind": c.pending["kind"], "command": c.pending["intent"].command}
        return {"user_story_id": c.user_story_id, "test_case_id": c.test_case_id, "run_id": c.run_id,
                "perf_plan_id": c.perf_plan_id, "base_url": c.base_url, "pending": pending}

    @staticmethod
    def _apply_page(conv: Conversation, page: dict[str, Any] | None) -> None:
        """The page the user is looking at is context too ("generate automation for this test case")."""
        if not page:
            return
        route, params = page.get("route") or "", page.get("params") or {}
        try:
            if route == "user-stories" and params.get("id"):
                conv.user_story_id = int(params["id"])
            elif route == "test-cases" and params.get("id"):
                conv.test_case_id = int(params["id"])
            elif route == "test-design" and params.get("story"):
                conv.user_story_id = int(params["story"])
            elif route == "history" and params.get("run"):
                conv.run_id, conv.run_kind = params["run"], None
            elif route == "perf-results" and params.get("run"):
                conv.run_id, conv.run_kind = params["run"], "performance"
            elif route.startswith("perf-") and params.get("plan"):
                conv.perf_plan_id = params["plan"]
        except (TypeError, ValueError):
            return

    def history(self, conversation_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if conversation_id:
            rows = self.db.query("SELECT * FROM voice_commands WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                                 (conversation_id, limit))
        else:
            rows = self.db.query("SELECT * FROM voice_commands ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = loads(r["params"]) if r["params"] else {}
        return rows[::-1]

    # ---- entry point ---------------------------------------------------------------------------

    def handle(self, text: str, *, conversation_id: str, source: str = "typed",
               page: dict[str, Any] | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise QAError("VALIDATION_FAILED", "Say or type a command.", status=422)
        conv = self._conv(conversation_id)
        with conv.lock:
            self._apply_page(conv, page)
            command_id = self.db.execute(
                "INSERT INTO voice_commands (conversation_id, text, source, status, created_at) VALUES (?,?,?,?,?)",
                (conversation_id, text, source, "received", now_iso())).lastrowid
            base = {"conversation_id": conversation_id, "command_id": command_id}
            self.agent.emit(names.VOICE_COMMAND_RECEIVED, f'Voice command received: "{text}"', details=base)
            intent = self._understand(text, conv)
            outcome = self._dispatch(conv, intent)
            self.db.execute("UPDATE voice_commands SET command = ?, params = ?, via = ?, status = ?, reply = ?,"
                            " finished_at = ? WHERE id = ?",
                            (intent.command if intent else None, dumps(intent.params) if intent else None,
                             intent.via if intent else None, "running" if outcome.job else outcome.status,
                             outcome.say, None if outcome.job else now_iso(), command_id))
        details = {**base, "command": intent.command if intent else None, "say": outcome.say,
                   "navigate": outcome.navigate}
        if outcome.status in ("failed", "unsupported"):
            self.agent.emit(names.VOICE_COMMAND_FAILED, outcome.say, details=details)
        elif outcome.job:
            self.agent.emit(names.VOICE_COMMAND_ACCEPTED, outcome.say, details=details)
            self._start_job(conv, command_id, text, outcome)
        else:
            self.agent.emit(names.VOICE_COMMAND_COMPLETED, outcome.say, details=details)
        return {"command_id": command_id, "status": outcome.status, "say": outcome.say, "navigate": outcome.navigate,
                "command": intent.command if intent else None, "via": intent.via if intent else None,
                "context": self.context(conversation_id)}

    def _start_job(self, conv: Conversation, command_id: int, text: str, outcome: Outcome) -> None:
        assert outcome.job is not None
        base = {"conversation_id": conv.id, "command_id": command_id, "background": True}

        def work() -> None:
            status, say, navigate = "completed", "", None
            try:
                with self.agent.activity(outcome.job_title or f'Voice command: "{text}"') as act:
                    def progress(message: str) -> None:
                        act.progress(None, message, **base, speak=message)
                    say, navigate = outcome.job(progress)  # type: ignore[misc]
                    act.complete(say)
            except QAError as exc:
                status, say = "failed", f"{outcome.failure}: {exc.message}"
            except Exception as exc:  # reported, never silent
                log.exception("Voice command job failed")
                status, say = "failed", f"{outcome.failure} because of an unexpected error: {type(exc).__name__}."
            self.db.execute("UPDATE voice_commands SET status = ?, final_reply = ?, finished_at = ? WHERE id = ?",
                            (status, say, now_iso(), command_id))
            self.agent.emit(names.VOICE_COMMAND_COMPLETED if status == "completed" else names.VOICE_COMMAND_FAILED,
                            say, details={**base, "speak": say, "say": say, "navigate": navigate})

        threading.Thread(target=work, name=f"voice-{command_id}", daemon=True).start()

    # ---- intent analysis -----------------------------------------------------------------------

    def _understand(self, text: str, conv: Conversation) -> Intent | None:
        intent = intents.parse(text)
        pending = conv.pending if conv.pending and time.monotonic() - conv.pending["at"] < PENDING_SECONDS else None
        if pending and pending["kind"] == "input" and (intent is None or intent.command in ("open_page",)):
            value = intents.normalize(text)
            follow = Intent(pending["intent"].command, dict(pending["intent"].params), via="follow_up")
            if pending["param"].endswith("_id"):
                m = re.search(r"\d+", value)
                if m:
                    follow.params[pending["param"]] = int(m.group())
                    return follow
            else:
                follow.params[pending["param"]] = value
                return follow
        if intent is None:
            intent = self._ai_intent(text)
        return intent

    def _ai_intent(self, text: str) -> Intent | None:
        if not self.ai.config()["base_url"]:
            return None
        try:
            r = self.ai.complete_json(AI_SYSTEM, f"Request: {text}", AIVoiceIntent, max_tokens=300)
        except QAError as exc:
            log.info(f"AI intent analysis unavailable: {exc.message}")
            return None
        if r.command == "unsupported":
            return None
        digits = set(re.findall(r"\d+", intents.normalize(text)))
        params: dict[str, Any] = {}
        for key in ("user_story_id", "test_case_id", "users"):
            value = getattr(r, key)
            if value is not None and str(value) in digits:  # an ID the user did not say is never used
                params[key] = value
        if r.duration_s and any(str(int(r.duration_s / f)) in digits for f in (1, 60, 3600) if r.duration_s % f == 0):
            params["duration_s"] = r.duration_s
        if r.kind:
            params["kind"] = r.kind
        if r.name and r.name.lower() in text.lower():
            params["name"] = r.name
        if r.command == "open_page":
            route = intents.match_page(intents.normalize(r.page or ""))
            if not route:
                return None
            params = {"route": route}
        if r.command == "open_work_item":
            if "test_case_id" in params:
                params = {"kind": "test case", "id": params["test_case_id"]}
            elif "user_story_id" in params:
                params = {"kind": "user story", "id": params["user_story_id"]}
            else:
                return None
        return Intent(r.command, params, via="ai")

    # ---- command validation and dispatch -------------------------------------------------------

    def _dispatch(self, conv: Conversation, intent: Intent | None) -> Outcome:
        if intent is None:
            return Outcome("unsupported", UNSUPPORTED)
        pending = conv.pending if conv.pending and time.monotonic() - conv.pending["at"] < PENDING_SECONDS else None
        if intent.command == "confirm":
            if not pending or pending["kind"] != "confirm":
                conv.pending = None
                return Outcome("completed", "There is nothing waiting for your confirmation.")
            conv.pending = None
            intent = Intent(pending["intent"].command, {**pending["intent"].params, "confirmed": True},
                            via=pending["intent"].via)
        elif intent.command == "cancel":
            conv.pending = None
            return Outcome("completed", "Cancelled." if pending else "There is nothing to cancel.")
        else:
            conv.pending = None
        handler = getattr(self, f"_cmd_{intent.command}")
        try:
            return handler(conv, intent)
        except QAError as exc:
            return Outcome("failed", f"I couldn't do that. {exc.message}")

    def _ask(self, conv: Conversation, intent: Intent, param: str, question: str) -> Outcome:
        conv.pending = {"kind": "input", "intent": intent, "param": param, "at": time.monotonic()}
        return Outcome("needs_input", question)

    # ---- commands ------------------------------------------------------------------------------

    def _cmd_help(self, conv: Conversation, intent: Intent) -> Outcome:
        return Outcome("completed", "You can say, for example: open user story 118, generate test cases, generate "
                                    "automation for test case 1280, play test case 1280, run all automated tests, run "
                                    "a 100 user load test for checkout for five minutes, show failed tests, analyze "
                                    "the failure, create defects for failed tests, or stop the running test.",
                       navigate="#/voice")

    def _cmd_status(self, conv: Conversation, intent: Intent) -> Outcome:
        running = [s["name"] for s in self.state.snapshot() if s["status"] == "RUNNING" and s["agent_id"] != "voice_command"]
        today = now().date().isoformat()
        rows = [r for r in self.runs.list(limit=500) if (r.get("started_at") or "").startswith(today)]
        passed = sum(r["status"] == "PASSED" for r in rows)
        failed = sum(r["status"] == "FAILED" for r in rows)
        parts = [("Right now " + ", ".join(running) + (" is" if len(running) == 1 else " are") + " running.")
                 if running else "No agent is running right now."]
        parts.append(f"Today there were {len(rows)} runs: {passed} passed and {failed} failed." if rows
                     else "There have been no runs today.")
        return Outcome("completed", " ".join(parts))

    def _cmd_open_page(self, conv: Conversation, intent: Intent) -> Outcome:
        route = intent.params["route"]
        return Outcome("completed", f"{intents.PAGE_LABELS[route]} opened.", navigate=f"#/{route}")

    def _cmd_open_work_item(self, conv: Conversation, intent: Intent) -> Outcome:
        item_id = int(intent.params["id"])
        item = self.tfs.detail(item_id)
        wtype = item.get("type") or "Work item"
        cats = (self.tfs.meta().get("categories") or {})

        def is_type(category: str) -> bool:
            return wtype in ((cats.get(category) or {}).get("types") or [])

        if is_type("requirement"):
            route = "user-stories"
            conv.user_story_id = item_id
        elif is_type("testcase"):
            route = "test-cases"
            conv.test_case_id = item_id
        elif is_type("bug"):
            route = "defects"
        else:
            route = "tfs-explorer"
        return Outcome("completed", f"{wtype} {item_id} opened: {item.get('title') or 'untitled'}.",
                       navigate=f"#/{route}?id={item_id}")

    def _cmd_generate_test_cases(self, conv: Conversation, intent: Intent) -> Outcome:
        sid = intent.params.get("user_story_id") or conv.user_story_id
        if not sid:
            return self._ask(conv, intent, "user_story_id", "Which user story? Say its number.")
        if any(s["status"] == "RUNNING" for s in self.state.snapshot() if s["agent_id"] in DESIGN_AGENTS):
            return Outcome("failed", "A test design run is already in progress. I'll be able to start another one when it finishes.")
        if not self.ai.config()["base_url"]:
            return Outcome("failed", "Test case generation needs the On-Premise AI, and no AI server is configured.",
                           navigate="#/ai-settings")
        sid = int(sid)
        conv.user_story_id = sid
        run_id = new_run_id("DESIGN")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            result = self.design.run(sid, run_id)
            n = len(result.get("test_cases") or [])
            return (f"Test case generation completed successfully. {n} test case{'s were' if n != 1 else ' was'} "
                    f"generated for user story {sid}. Review them on AI Test Design before saving them to TFS.",
                    f"#/test-design?story={sid}&run={run_id}")

        return Outcome("accepted", f"Yes, I will generate the test cases for user story {sid} now.",
                       navigate=f"#/test-design?story={sid}", job=job,
                       job_title=f"Voice: generating test cases for User Story #{sid}", failure="Test case generation failed")

    def _resolve_test_case(self, conv: Conversation, intent: Intent) -> int | None:
        if intent.params.get("test_case_id"):
            return int(intent.params["test_case_id"])
        name = intent.params.get("name")
        if name:
            candidates: list[tuple[float, int]] = []
            if conv.user_story_id:  # the test cases of the user story in context
                story = self.tfs.detail(conv.user_story_id)
                candidates += [(_match_score(name, tc.get("title")), tc["id"]) for tc in story["traceability"]["test_cases"]]
            candidates += [(_match_score(name, s["name"]), int(s["test_case_id"])) for s in self.automation.scripts.list()
                           if s.get("test_case_id") and str(s["test_case_id"]).isdigit()]
            candidates = [c for c in candidates if c[0] >= 0.6]
            if not candidates:
                where = f" in user story {conv.user_story_id}" if conv.user_story_id else ""
                raise QAError("VOICE_NOT_FOUND", f"I could not find a test case called {name}{where}.", status=404)
            return max(candidates)[1]
        return conv.test_case_id

    def _known_base_url(self, conv: Conversation, test_case_id: int) -> tuple[str | None, str]:
        if conv.base_url:
            return conv.base_url, "the address used earlier in this conversation"
        scripts = self.automation.scripts.list()
        for key, value, why in (("test_case_id", str(test_case_id), "the existing script for this test case"),
                                ("user_story_id", str(conv.user_story_id or ""), "a script of the same user story")):
            for s in scripts:
                if value and str(s.get(key) or "") == value and s.get("base_url"):
                    return s["base_url"], why
        disc = self.automation.discovery.list(limit=1)
        if disc:
            return disc[0]["base_url"], "your latest application discovery"
        return None, ""

    def _cmd_generate_automation(self, conv: Conversation, intent: Intent) -> Outcome:
        tc = self._resolve_test_case(conv, intent)
        if not tc:
            return self._ask(conv, intent, "test_case_id", "Which test case? Say its number.")
        conv.test_case_id = tc
        base_url, why = self._known_base_url(conv, tc)
        if not base_url:
            return Outcome("completed", f"Test case {tc} is selected on AI-Driven Automation. Enter the application "
                                        "address there, because I don't know which application to test yet.",
                           navigate=f"#/ai-automation?tc={tc}")
        if not self.ai.config()["base_url"]:
            return Outcome("failed", "Automation generation needs the On-Premise AI, and no AI server is configured.",
                           navigate="#/ai-settings")
        conv.base_url = base_url
        run_id = new_run_id("GEN")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            script = self.automation.generate(run_id, tc, base_url)
            conv.script_id = script.id
            return (f"Automation generated for test case {tc}: {len(script.steps)} steps in the script "
                    f"{script.name}. Say play test case {tc} to run it.", f"#/guided-automation?script={script.id}")

        return Outcome("accepted", f"Yes, I will generate automation for test case {tc} on {base_url}, taken from {why}.",
                       job=job, job_title=f"Voice: generating automation for Test Case #{tc}",
                       failure="Automation generation failed")

    def _find_script(self, conv: Conversation, intent: Intent) -> dict[str, Any] | None:
        scripts = self.automation.scripts.list()
        if intent.params.get("test_case_id"):
            tc = str(intent.params["test_case_id"])
            return next((s for s in scripts if str(s.get("test_case_id") or "") == tc), None)
        if intent.params.get("name"):
            scored = [(_match_score(intent.params["name"], s["name"]), i) for i, s in enumerate(scripts)]
            scored = [x for x in scored if x[0] >= 0.6]
            return scripts[max(scored)[1]] if scored else None
        if conv.script_id:
            return next((s for s in scripts if s["id"] == conv.script_id), None)
        if conv.test_case_id:
            return next((s for s in scripts if str(s.get("test_case_id") or "") == str(conv.test_case_id)), None)
        return None

    def _cmd_run_test_case(self, conv: Conversation, intent: Intent) -> Outcome:
        script = self._find_script(conv, intent)
        if script is None:
            tc = intent.params.get("test_case_id") or (None if intent.params.get("name") else conv.test_case_id)
            if tc:
                return Outcome("failed", f"There is no automated script for test case {tc} yet. Say generate automation "
                                         f"for test case {tc} to create one.")
            if intent.params.get("name"):
                return Outcome("failed", f"I could not find an automated test called {intent.params['name']}.")
            return self._ask(conv, intent, "test_case_id", "Which test case should I run? Say its number.")
        play = bool(intent.params.get("play"))
        tc = script.get("test_case_id")
        label = f"test case {tc}" if tc else f"the {script['name']} test"
        conv.script_id = script["id"]
        if tc and str(tc).isdigit():
            conv.test_case_id = int(tc)
        conv.base_url = script.get("base_url") or conv.base_url

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            record = self.automation.run_script(script["id"], headless=False if play else None, background=False)
            assert isinstance(record, dict)
            conv.run_id, conv.run_kind = record["run_id"], "functional"
            return self._functional_result(record, label, progress), f"#/history?run={record['run_id']}"

        return Outcome("accepted", f"Yes. I will {'play' if play else 'run'} {label} now.", navigate="#/workflow",
                       job=job, job_title=f"Voice: running {label}", failure="The test could not run")

    def _functional_result(self, record: dict[str, Any], label: str, progress: Callable[[str], None]) -> str:
        script = self.automation.scripts.get(record["script_id"]) if record.get("script_id") else None
        healing = [h for h in self.automation.runner.healing_events(record.get("script_id"), limit=50)
                   if h.get("run_id") == record["run_id"]]

        def element(index: int | None) -> str:
            if script is None or index is None or index >= len(script.steps):
                return "the element"
            target = script.steps[index].target
            return (target.description or target.selector) if target else "the element"

        if record["status"] == "PASSED":
            healed = [h for h in healing if h["result"] == "healed"]
            if healed:
                return (f"The original locator failed, but the self-healing agent recovered {element(healed[0]['step_index'])} "
                        f"and {label} completed successfully.")
            return f"{label[0].upper()}{label[1:]} passed. All {len(record.get('steps') or [])} steps completed."
        index = record.get("failed_step")
        step = next((s for s in record.get("steps") or [] if s.get("index") == index), {})
        desc = step.get("description") or (f"step {index + 1}" if index is not None else "an unknown step")
        from ..defects.analysis import classify_step_failure
        heal_failed = any(h["result"] == "failed" and h["step_index"] == index for h in healing)
        category = classify_step_failure(script.steps[index].model_dump() if script and index is not None and
                                         index < len(script.steps) else None, record.get("error") or "", heal_failed)
        phrase = FAILURE_PHRASES.get(category, "an action that could not be completed on {element}").format(
            element=element(index), step=desc, error=record.get("error") or "")
        where = f" at step {index + 1}, {desc}" if index is not None else ""
        say = f"The test execution failed{where}{' after self-healing attempts' if heal_failed else ''}. I found {phrase}."
        progress(say)
        return self._defect_followup("functional", record["run_id"], progress) or say

    def _defect_followup(self, kind: str, run_id: str, progress: Callable[[str], None]) -> str:
        mode = self.defects.mode
        if mode == "disabled":
            return ""
        if mode == "automatic":
            progress(f"I am checking for an existing {kind} defect.")
        self.defects.wait_for_automatic(run_id, 60)
        row = self.db.query_one("SELECT id FROM defect_drafts WHERE occurrences LIKE ? ORDER BY id DESC LIMIT 1",
                                (f"%{run_id}%",))
        draft = self.defects.get(row["id"]) if row else None
        if draft is None:
            return "No defect draft was made for this failure."
        dup = (draft.get("duplicates") or [None])[0]
        if draft.get("tfs_id") and len(draft.get("occurrences") or []) > 1:
            return f"Existing defect {draft['tfs_id']} was found and the new execution evidence was linked."
        if draft["status"] == "created":
            return f"I found no duplicate defect. A new {kind} defect {draft['tfs_id']} has been created in TFS."
        if dup and dup["score"] >= LIKELY_DUPLICATE:
            return (f"It looks like existing defect {dup['id']}, so I did not create a new one. "
                    "The draft is waiting for your review.")
        return "A defect draft is waiting for your review on Defect Review."

    def _cmd_run_all_tests(self, conv: Conversation, intent: Intent) -> Outcome:
        scripts = self.automation.scripts.list()
        if not scripts:
            return Outcome("failed", "There are no automated tests yet.", navigate="#/ai-automation")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            passed = failed = 0
            for s in scripts:
                try:
                    record = self.automation.run_script(s["id"], background=False)
                    ok = isinstance(record, dict) and record["status"] == "PASSED"
                except QAError:
                    ok = False
                passed, failed = passed + ok, failed + (not ok)
            say = f"All automated tests finished: {passed} passed and {failed} failed."
            return (say + (" Say show failed tests to see them." if failed else ""),
                    "#/history?status=FAILED" if failed else "#/history")

        n = len(scripts)
        return Outcome("accepted", f"Yes. I will run all {n} automated test{'s' if n != 1 else ''}.",
                       navigate="#/workflow", job=job, job_title=f"Voice: running {n} automated tests")

    def _cmd_start_recording(self, conv: Conversation, intent: Intent) -> Outcome:
        return Outcome("completed", "Guided Browser Automation is open. Enter the start address and press Record, "
                                    "because a recording needs the application address.",
                       navigate="#/guided-automation")

    def _cmd_run_performance(self, conv: Conversation, intent: Intent) -> Outcome:
        plans = self.performance.list_plans()
        if not plans:
            return Outcome("failed", "There are no performance test plans yet. Create one on Performance Test Plans.",
                           navigate="#/perf-plans")
        p = intent.params
        plan_row = None
        if p.get("name"):
            scored = []
            for row in plans:
                plan = self.performance.get_plan(row["id"])
                scored.append((_match_score(p["name"], plan.name, *[r.name for r in plan.requests],
                                            *[r.url for r in plan.requests]), row["id"]))
            scored = [x for x in scored if x[0] >= 0.6]
            if not scored:
                return Outcome("failed", f"I couldn't find a performance test plan for {p['name']}. Your plans are: "
                                         + ", ".join(r["name"] for r in plans[:6]) + ".")
            plan_row = next(r for r in plans if r["id"] == max(scored)[1])
        elif conv.perf_plan_id and any(r["id"] == conv.perf_plan_id for r in plans):
            plan_row = next(r for r in plans if r["id"] == conv.perf_plan_id)
        elif len(plans) == 1:
            plan_row = plans[0]
        else:
            return self._ask(conv, intent, "name", "Which performance test plan? You have: "
                             + ", ".join(r["name"] for r in plans[:6]) + ".")
        if self.performance.running():
            return Outcome("failed", "A performance test is already running. Say stop the running test to stop it.")
        if not find_java(self.settings) or not find_jmeter(self.settings):
            return Outcome("failed", "Java or JMeter was not found. Set them under Performance tool settings.",
                           navigate="#/perf-plans")
        overrides = {k: p[k] for k in ("kind", "users", "duration_s") if p.get(k)}
        plan = self.performance.with_overrides(self.performance.get_plan(plan_row["id"]), overrides)
        conv.perf_plan_id = plan.id
        kind = plan.kind
        users = plan.peak_users()
        run_id = new_run_id("PERF")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            out: dict[str, Any] = {}

            def run() -> None:
                try:
                    out["result"] = self.performance.run(plan.id, run_id, overrides)
                except QAError as exc:
                    out["error"] = exc

            t = threading.Thread(target=run, name=f"voice-perf-{run_id}", daemon=True)
            t.start()
            deadline = time.monotonic() + 60
            while t.is_alive() and run_id not in self.performance.running() and time.monotonic() < deadline:
                time.sleep(0.2)
            if run_id in self.performance.running():
                progress(f"The {kind} test has started.")
            t.join()
            conv.run_id, conv.run_kind = run_id, "performance"
            if "error" in out:
                raise out["error"]
            return self._performance_result(out["result"], progress), f"#/perf-results?run={run_id}"

        return Outcome("accepted", f"Yes. I will prepare and run a {_duration_words(plan.profile.duration_s)} "
                                   f"{plan.name} {kind} test with {users} virtual users.",
                       navigate="#/workflow", job=job, job_title=f"Voice: {users}-user {plan.name} {kind} test",
                       failure="The performance test could not run")

    def _performance_result(self, run: dict[str, Any], progress: Callable[[str], None]) -> str:
        if run.get("error"):
            return f"The performance test could not complete: {run['error']}"
        o = (run.get("metrics") or {}).get("overall") or {}
        sla = run.get("sla") or {}
        if sla.get("status") != "FAIL":
            return (f"The performance test completed successfully. P95 response time was {_sec(o.get('p95_ms'))} and "
                    f"the error rate was {o.get('error_pct')} percent.")
        failed = sla.get("failed") or []
        worst = max(failed, key=lambda c: (c["actual"] / c["limit"]) if c["name"] != "Throughput" and c["limit"] else 0)
        scope = "" if worst["scope"] == "ALL REQUESTS" else f" for {worst['scope']}"
        if worst["unit"] == "ms":
            detail = f"{worst['name']}{scope} was {_sec(worst['actual'])} against the configured {_sec(worst['limit'])} SLA"
        elif worst["name"] == "Throughput":
            detail = f"throughput{scope} was {worst['actual']} requests per second against the required {worst['limit']}"
        else:
            detail = f"the error rate{scope} was {worst['actual']} percent against the allowed {worst['limit']} percent"
        requests = (run.get("metrics") or {}).get("requests") or []
        slowest = max(requests, key=lambda r: r.get("p95_ms") or 0) if len(requests) > 1 else None
        say = f"The performance test failed. {detail[0].upper()}{detail[1:]}."
        if slowest:
            say += f" The slowest transaction was {slowest['label']}, with a P95 of {_sec(slowest['p95_ms'])}."
        progress(say)
        return self._defect_followup("performance", run["run_id"], progress) or say

    def _cmd_stop_run(self, conv: Conversation, intent: Intent) -> Outcome:
        stopped = []
        for run_id in self.performance.running():
            if self.performance.stop(run_id):
                stopped.append("the performance test")
        for run_id in self.automation.runner.running():
            if self.automation.runner.stop(run_id):
                stopped.append("the automated test")
        if not stopped:
            return Outcome("completed", "Nothing is running right now.")
        return Outcome("completed", "Stopping " + " and ".join(dict.fromkeys(stopped)) + ". Results so far are kept.")

    def _cmd_show_failed(self, conv: Conversation, intent: Intent) -> Outcome:
        failed = [r for r in self.runs.list(limit=200) if r["status"] == "FAILED" and r["kind"] in ("functional", "performance")]
        if not failed:
            return Outcome("completed", "There are no failed tests.", navigate="#/history")
        return Outcome("completed", f"Showing {len(failed)} failed test run{'s' if len(failed) != 1 else ''}. The latest is "
                                    f"{failed[0].get('title') or failed[0]['run_id']}.", navigate="#/history?status=FAILED")

    def _cmd_show_performance_report(self, conv: Conversation, intent: Intent) -> Outcome:
        runs = self.performance.list_runs(limit=1)
        if not runs:
            return Outcome("completed", "There are no performance results yet.", navigate="#/perf-results")
        r = runs[0]
        conv.run_id, conv.run_kind = r["run_id"], "performance"
        return Outcome("completed", f"Showing the latest performance results: the {r['kind']} test {r['name']}, "
                                    f"which {'passed' if r['status'] == 'PASSED' else 'failed'}.",
                       navigate=f"#/perf-results?run={r['run_id']}")

    def _latest_failure(self, conv: Conversation) -> tuple[str, str] | None:
        if conv.run_id:
            run = self.runs.get(conv.run_id)
            if run and run["status"] == "FAILED" and run["kind"] in ("functional", "performance"):
                return run["kind"], run["run_id"]
        for run in self.runs.list(limit=200):
            if run["status"] != "FAILED":
                continue
            if run["kind"] == "functional" or (run["kind"] == "performance" and
                                               ((self.runs.get(run["run_id"]) or {}).get("summary") or {}).get("sla") == "FAIL"):
                return run["kind"], run["run_id"]
        return None

    def _cmd_analyze_failure(self, conv: Conversation, intent: Intent) -> Outcome:
        found = self._latest_failure(conv)
        if not found:
            return Outcome("completed", "There is no failed run to analyze.")
        kind, run_id = found
        draft = self.defects.analyze(kind, run_id)
        conv.run_id, conv.run_kind = run_id, kind
        finding = (draft.get("facts") or {}).get("finding") or "The failure was recorded."
        route = "perf-defects" if kind == "performance" else "defect-review"
        return Outcome("completed", f"{finding} The root cause is not determined from the evidence. Defect draft "
                                    f"{draft['id']} is ready for review.", navigate=f"#/{route}?id={draft['id']}")

    def _cmd_create_defects(self, conv: Conversation, intent: Intent) -> Outcome:
        source = intent.params.get("source") or "functional"
        drafts = self.defects.list(source=source, status="open")
        if not drafts:
            return Outcome("completed", f"There are no open {source} defect drafts to create.",
                           navigate="#/perf-defects" if source == "performance" else "#/defect-review")
        n = len(drafts)
        if not intent.params.get("confirmed"):
            conv.pending = {"kind": "confirm", "intent": intent, "at": time.monotonic()}
            return Outcome("needs_confirmation", f"I will create {n} defect{'s' if n != 1 else ''} in TFS from the "
                                                 f"{source} failure drafts, skipping likely duplicates. Say confirm to "
                                                 "continue, or cancel.")
        ids = [d["id"] for d in drafts]

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            created, skipped, failed = [], [], []
            for draft_id in ids:
                d = self.defects.get(draft_id)
                dup = (d.get("duplicates") or [None])[0]
                if dup and dup["score"] >= LIKELY_DUPLICATE:
                    skipped.append(dup["id"])
                    continue
                try:
                    created.append(self.defects.create(draft_id)["tfs_id"])
                except QAError:
                    failed.append(draft_id)
            parts = []
            if created:
                parts.append(f"Created defect{'s' if len(created) != 1 else ''} {', '.join(map(str, created))} in TFS.")
            if skipped:
                parts.append(f"Skipped {len(skipped)} likely duplicate{'s' if len(skipped) != 1 else ''} of "
                             f"{', '.join(map(str, skipped))}.")
            if failed:
                parts.append(f"{len(failed)} could not be created; see Defect Review.")
            return " ".join(parts) or "Nothing was created.", "#/defect-review?status=all"

        return Outcome("accepted", f"Yes. Creating {n} defect{'s' if n != 1 else ''} in TFS now.", job=job,
                       job_title=f"Voice: creating {n} defects in TFS")

    # confirm/cancel are resolved in _dispatch
    def _cmd_confirm(self, conv: Conversation, intent: Intent) -> Outcome:  # pragma: no cover
        return Outcome("completed", "There is nothing waiting for your confirmation.")

    _cmd_cancel = _cmd_confirm
