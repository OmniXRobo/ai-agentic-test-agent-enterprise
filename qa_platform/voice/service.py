"""Voice Command Agent.

USER SPEAKS -> speech recognition -> intent analysis -> command validation ->
voice acknowledgement -> execute a supported dashboard action -> monitor it ->
voice completion or failure response.

* Speech recognition runs on the bundled offline recognizer (offline_stt.py),
  in the browser, on the Faster-Whisper server this application starts itself
  (fw_service.py, 23.3.7), or on another local Whisper server (whisper.cpp
  ``/inference`` or an OpenAI-compatible ``/audio/transcriptions`` endpoint) configured by URL.
  Nothing is downloaded and no other host is called.
* Replies can be rendered with the PC's Windows voices (tts.py) so the
  dashboard can play them as radio transmissions.
* Every action calls the same services the dashboard pages use; there is no
  separate automation path.
* Conversations keep context (the user story, test case, run and performance
  plan last referred to) so follow-ups like "generate automation for the smoke
  test" or "run it again" resolve to the current item.
* Commands that change or delete saved data ask "Confirm this operation?" first
  (``destructive`` in registry.py); only the registered commands can run.
* Replies follow the configured phrase style: "OK, I will do your task now." /
  "Please clarify the instruction." / "Unable to complete the command." (standard)
  or "Command received." / "Mission complete." / "Command unsuccessful." (tactical).
  Success is only announced from the result the service returned.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

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
from . import intents, registry
from .intents_guided import guided_action
from .commands_automation import AutomationCommands
from .commands_design import DesignCommands
from .commands_guided import GuidedCommands
from .commands_safety import SafetyCommands
from . import privacy
from .commands_more import MoreCommands
from .commands_plans import PlanCommands, original_case
from .model import Conversation, Outcome
from .intents import Intent
from .offline_stt import OfflineRecognizer
from . import fw_service
from .fw_service import FasterWhisperService
from .tts import SystemVoice

log = get_logger("voice")


def _silence_wav(seconds: float = 0.5) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()

UNSUPPORTED = "That operation is not supported."

PHRASES = {
    "standard": {"ack": "OK, I will do your task now.", "done": "Task complete.", "fail": "Unable to complete the command.",
                 "clarify": "Please clarify the instruction.", "confirm": "Confirm this operation?"},
    "tactical": {"ack": "Command received.", "done": "Mission complete.", "fail": "Command unsuccessful.",
                 "clarify": "Say again. Please clarify the instruction.", "confirm": "Confirm this operation?"},
    # {a} is the configured form of address ("Boss"); with none set, ", {a}" is left out.
    "conversational": {"ack": "On it, {a}.", "understood": "Understood, {a}.", "done": "Done, {a}.",
                       "fail": "Sorry, {a}.", "clarify": "One question, {a}.", "confirm": "Please confirm, {a}."},
}
NEXT_QUESTION = "What would you like to do next?"


def _address(phrase: str, address: str) -> str:
    address = " ".join((address or "").split())
    return phrase.replace("{a}", address) if address else phrase.replace(", {a}", "").replace("{a}", "")


def compose(status: str, say: str, style: str = "standard", background: bool = False, address: str = "") -> str:
    """The full spoken line for a reply: the fixed opening of its kind, then what actually happened."""
    p = PHRASES.get(style) or PHRASES["standard"]
    head = {"accepted": p["ack"], "completed": p["done"] if background else p.get("understood", p["ack"]),
            "failed": p["fail"], "unsupported": p["fail"], "needs_input": p["clarify"], "unclear": p["clarify"],
            "needs_confirmation": p["confirm"]}.get(status, "")
    line = f"{_address(head, address)} {say}".strip()
    if style == "conversational" and background and status == "completed":
        line = f"{line} {NEXT_QUESTION}"
    return line


PENDING_SECONDS = 120
DUPLICATE_SECONDS = 5  # the same sentence again within this time is not executed twice
# Commands that only look or move around may be repeated on purpose ("go back", "go back").
REPEATABLE = {"open_page", "go_back", "help", "status", "show_details", "list_suites", "find_test_plan", "open_current",
              "select_item", "open_work_item", "search_work_items", "read_notifications", "show_failed", "show_result",
              "show_report", "show_performance_report", "confirm", "cancel", "interrupt", "repeat", "continue_work",
              "explain_activity", "where", "automation_settings", "show_failure", "validate_script", None}
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
    """What the On-Premise AI may return: one registered command (or unsupported / unclear) and its parameters.

    Small local models sometimes put the parameters next to "command" instead of inside "params";
    both shapes are accepted, and every parameter is validated against the registry afterwards.
    """
    model_config = ConfigDict(extra="allow")
    command: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    question: str | None = Field(default=None, max_length=200)

    def all_params(self) -> dict[str, Any]:
        return {**(self.model_extra or {}), **(self.params or {})}


AI_SYSTEM = (
    "You map a QA engineer's spoken request to exactly one command of a QA dashboard. Commands:\n"
    + registry.ai_catalog() + "\n"
    "Use only numbers and names that appear in the request; never invent IDs, pages, selectors or commands. "
    "If the request is about QA work but too vague to pick one command, answer command 'unclear' with a short "
    "specific question in 'question'. If it asks for something none of the commands do, answer 'unsupported'. "
    'Reply with JSON only: {"command": "...", "params": {...}, "question": null}.'
)


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


class VoiceService(SafetyCommands, AutomationCommands, GuidedCommands, DesignCommands, MoreCommands, PlanCommands):
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
        self.fw = FasterWhisperService(settings)
        self.system_voice = SystemVoice(settings.path.parent / "voice-cache")
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        # Wired by the runtime after the other services exist (see runtime.build_runtime).
        self.studio: Any = None
        self.reports: Any = None
        self.notifications: Any = None
        self.health: Any = None

    def attach(self, **services: Any) -> None:
        for key, value in services.items():
            setattr(self, key, value)

    def style(self) -> str:
        return self.config().get("phrase_style") or "standard"

    def address(self) -> str:
        """How the agent addresses the user ("Boss"); a setting, never code."""
        return str(self.config().get("address_as") or "")

    def agent_name(self) -> str:
        return self.config().get("agent_display_name") or "HAMI"

    def strip_wake_name(self, text: str) -> str:
        """'Saqr, generate test cases' -> 'generate test cases' (the configured command name, never hard-coded)."""
        cfg = self.config()
        names_ = {n.strip().lower() for n in (cfg.get("agent_command_name"), cfg.get("agent_display_name")) if n}
        t = text.strip()
        for n in sorted(names_, key=len, reverse=True):
            m = re.match(rf"^\s*(?:hey|ok|okay|hi)?[\s,]*{re.escape(n)}\b[\s,.:!-]*", t, re.I)
            if m:
                return t[m.end():].strip()
        return t

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
        if cfg.get("stt_api") == fw_service.API:
            url = self.fw.ensure_ready(timeout=float(cfg.get("stt_timeout_seconds") or 90))
        else:
            url = (cfg.get("stt_url") or "").rstrip("/")
        if not url:
            raise QAError("VOICE_STT_NOT_CONFIGURED", "No local speech recognition server URL is set in Voice settings.",
                          status=409)
        path, data = self._stt_request(cfg)
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

    def vocabulary_prompt(self, cfg: dict[str, Any]) -> str:
        """A short hint so Whisper spells the wake name and the application's words the way the commands expect."""
        names = [n for n in dict.fromkeys((cfg.get("agent_command_name") or "", cfg.get("agent_display_name") or ""))
                 if n and re.fullmatch(r"[A-Za-z][A-Za-z' -]*", n)]
        lead = f"{names[0]}, open Test Plans." if names else "Open Test Plans."
        return (f"{lead} Test Suites, Test Cases, Test Data, user story, acceptance criteria, TFS, Playwright, "
                "regression, smoke test, Sprint 25.")

    def _stt_request(self, cfg: dict[str, Any]) -> tuple[str, dict[str, str]]:
        language = (cfg.get("language") or "en").split("-")[0]
        if cfg.get("stt_api") == "openai":
            return "/audio/transcriptions", {"model": cfg.get("stt_model") or "whisper-1", "response_format": "json",
                                             "language": language, "prompt": self.vocabulary_prompt(cfg)}
        return "/inference", {"response_format": "json", "language": language, "temperature": "0",
                              "prompt": self.vocabulary_prompt(cfg)}

    def _try_recording(self, url: str, cfg: dict[str, Any], timeout: float) -> tuple[str, str]:
        """Send half a second of silence to the real recognition endpoint: PASS only if it answers with text JSON.

        Before 23.3.7 the check only fetched the server's address and counted any answer below HTTP 500 as working,
        so "HTTP 404" (nothing that recognises speech at that address) was shown as PASS.
        """
        path, data = self._stt_request(cfg)
        started = time.monotonic()
        try:
            with httpx.Client(timeout=timeout, trust_env=False, transport=self.transport) as c:
                r = c.post(url + path, data=data, files={"file": ("check.wav", _silence_wav(), "audio/wav")})
        except httpx.HTTPError as exc:
            return "FAIL", f"{url} is not reachable ({type(exc).__name__}). Is the speech server running?"
        ms = int((time.monotonic() - started) * 1000)
        if r.status_code == 404:
            return "FAIL", (f"{url}{path} answered HTTP 404: there is no speech recognition at this address. "
                            "Choose the server type \"Faster-Whisper on this PC\" (installed by patch 23.3.7), "
                            "or the URL and type of your own speech server.")
        if r.status_code != 200:
            return "FAIL", f"{url}{path} answered HTTP {r.status_code}: {r.text[:160]}"
        try:
            body = r.json()
        except ValueError:
            return "FAIL", f"{url}{path} answered, but not with JSON, so it is not a speech server."
        if not isinstance(body, dict) or "text" not in body:
            return "FAIL", f"{url}{path} answered JSON without a \"text\" field, so it is not a speech server."
        return "PASS", f"{url}{path} recognised a test recording in {ms} ms."

    def speech_probe(self) -> tuple[str, str, str]:
        """(status, short label, detail) of the local speech server, for the header badges (cached by health)."""
        cfg = self.config()
        if cfg.get("stt_api") == fw_service.API:
            st = self.fw.status()
            if not st["installed"]:
                return "FAIL", "Not installed", "Faster-Whisper is not installed. Run the 23.3.7 patch."
            if not st["model"]:
                return "FAIL", "No model", "No Faster-Whisper model installed. Run the 23.3.7 patch with -ModelZip."
            if st["state"] == "RUNNING":
                return "PASS", "Faster-Whisper", f"{st['model']} on {st['url']}"
            if st["state"] in ("LOADING", "STARTING"):
                return "WARN", "Loading", f"Faster-Whisper is loading {st['model']}."
            return "FAIL", "Stopped", st["error"] or "The Faster-Whisper server is not running. Click Check Voice Setup."
        url = (cfg.get("stt_url") or "").rstrip("/")
        if not url:
            return "NOT_CONFIGURED", "Not set up", "Set the speech server URL."
        status, detail = self._try_recording(url, cfg, 5)
        return status, "Local server" if status == "PASS" else "Check", detail

    def diagnostics(self) -> dict[str, Any]:
        cfg = self.config()
        checks: list[dict[str, str]] = []
        engine = cfg.get("stt_engine")
        if engine == "builtin":
            status, detail = self.offline.status()
            checks.append({"name": "Built-in offline speech recognition", "status": status, "detail": detail})
        elif engine == "local" and cfg.get("stt_api") == fw_service.API:
            name = "Faster-Whisper speech recognition"
            try:
                url = self.fw.ensure_ready(timeout=120)
                st = self.fw.status()
                status, detail = self._try_recording(url, cfg, 30)
                h = st.get("health") or {}
                if status == "PASS":
                    detail = (f"Model {st['model']} ({h.get('compute_type', '')}, {h.get('device', 'cpu')}) on {url}, "
                              f"started with the application: {detail}")
                checks.append({"name": name, "status": status, "detail": detail})
            except QAError as exc:
                checks.append({"name": name, "status": "FAIL", "detail": exc.message})
        elif engine == "local":
            url = (cfg.get("stt_url") or "").rstrip("/")
            if not url:
                checks.append({"name": "Local speech server", "status": "NOT_CONFIGURED",
                               "detail": "Set the URL of your whisper.cpp or OpenAI-compatible speech server."})
            else:
                status, detail = self._try_recording(url, cfg, 30)
                checks.append({"name": "Local speech server", "status": status, "detail": detail})
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
                "perf_plan_id": c.perf_plan_id, "base_url": c.base_url, "pending": pending,
                "test_plan_id": c.test_plan_id, "test_plan_name": c.test_plan_name, "sprint": c.sprint,
                "suite_selection": dict(c.suite_selection), "focus": c.focus, "last_action": c.last_action,
                "case_runs": list(c.case_runs), "saved_case_ids": list(c.saved_case_ids), "data_case_ids": list(c.data_case_ids),
                "script_id": c.script_id, "script_ids": list(c.script_ids)}

    @staticmethod
    def _apply_page(conv: Conversation, page: dict[str, Any] | None) -> None:
        """The page the user is looking at is context too ("generate automation for this test case")."""
        if not page:
            return
        route, params = page.get("route") or "", page.get("params") or {}
        try:
            if route == "user-stories" and params.get("id"):
                conv.user_story_id, conv.focus = int(params["id"]), "user_story"
            elif route == "test-cases" and params.get("id"):
                conv.test_case_id, conv.focus = int(params["id"]), "test_case"
            elif route == "test-plans" and params.get("id"):
                if conv.test_plan_id != int(params["id"]):
                    conv.test_plan_name, conv.suite_selection = None, {}
                conv.test_plan_id, conv.focus = int(params["id"]), "test_plan"
            elif route in ("test-suites", "test-cases") and params.get("plan"):
                if conv.test_plan_id != int(params["plan"]):
                    conv.test_plan_name, conv.suite_selection = None, {}
                conv.test_plan_id = int(params["plan"])
                if route == "test-suites":
                    conv.focus = "test_plan"
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
        text = self.strip_wake_name(text or "")
        if not text:
            raise QAError("VALIDATION_FAILED", "Say or type a command.", status=422)
        conv = self._conv(conversation_id)
        norm = intents.normalize(text)
        with conv.lock:
            last = conv.last_command
            if last and last[0] == norm and time.monotonic() - last[1] < DUPLICATE_SECONDS and last[2].get("command") not in REPEATABLE \
                    and last[2].get("status") in ("accepted", "completed"):
                # A repeated recognition result or a double click: answer, but never execute twice.
                prev = last[2]
                return {**prev, "duplicate": True, "navigate": None, "verify": None, "status": "completed",
                        "say": "I already have that instruction, so I'm not doing it twice.",
                        "speak": compose("completed", "I already have that instruction, so I'm not doing it twice.",
                                         self.style(), address=self.address())}
            conv.raw_text = text
            self._apply_page(conv, page)
            # 23.3.6: spoken secrets are masked before the transcript is stored, logged or sent to the event feed
            stored = privacy.redact(text) if self.config().get("keep_transcripts", True) else "(transcript not kept)"
            command_id = self.db.execute(
                "INSERT INTO voice_commands (conversation_id, text, source, status, created_at) VALUES (?,?,?,?,?)",
                (conversation_id, stored, source, "received", now_iso())).lastrowid
            self._purge_transcripts()
            base = {"conversation_id": conversation_id, "command_id": command_id}
            self.agent.emit(names.VOICE_COMMAND_RECEIVED, f'Voice command received: "{privacy.redact(text)}"', details=base)
            intent = self._understand(text, conv)
            if intent and isinstance(intent.params.get("name"), str):
                intent.params["name"] = original_case(text, intent.params["name"])  # "Checkout Regression", as said
            outcome = self._dispatch(conv, intent, text)
            self.db.execute("UPDATE voice_commands SET command = ?, params = ?, via = ?, status = ?, reply = ?,"
                            " finished_at = ? WHERE id = ?",
                            (intent.command if intent else None, dumps(privacy.redact_params(intent.params)) if intent else None,
                             intent.via if intent else None, "running" if outcome.job else outcome.status,
                             outcome.say, None if outcome.job else now_iso(), command_id))
        speak = compose(outcome.status, outcome.say, self.style(), address=self.address())
        details = {**base, "command": intent.command if intent else None, "say": outcome.say, "speak_line": speak,
                   "navigate": outcome.navigate, "owner": outcome.owner}
        if outcome.status in ("failed", "unsupported"):
            self.agent.emit(names.VOICE_COMMAND_FAILED, outcome.say, details=details)
        elif outcome.job:
            self.agent.emit(names.VOICE_COMMAND_ACCEPTED, outcome.say, details=details)
            self._start_job(conv, command_id, text, outcome)
        else:
            self.agent.emit(names.VOICE_COMMAND_COMPLETED, outcome.say, details=details)
        reply = {"command_id": command_id, "status": outcome.status, "say": outcome.say, "speak": speak,
                 "navigate": outcome.navigate, "verify": outcome.verify, "owner": outcome.owner, "agent_name": self.agent_name(),
                 "command": intent.command if intent else None, "via": intent.via if intent else None,
                 "context": self.context(conversation_id)}
        conv.last_command = (norm, time.monotonic(), reply)
        if outcome.status in ("completed", "accepted") and intent:
            conv.last_action = intent.command
        return reply

    def _start_job(self, conv: Conversation, command_id: int, text: str, outcome: Outcome) -> None:
        assert outcome.job is not None
        base = {"conversation_id": conv.id, "command_id": command_id, "background": True}

        def work() -> None:
            status, say, navigate = "completed", "", None
            try:
                with self.agent.activity(outcome.job_title or f'Voice command: "{text}"') as act:
                    def progress(message: str) -> None:
                        act.progress(None, message, **base, speak=message)
                    act.event(names.VOICE_HANDOFF, f"Command handed to {outcome.owner or 'the runtime'}",
                              **base, to=outcome.owner, phase="executing")
                    say, navigate = outcome.job(progress)  # type: ignore[misc]
                    act.complete(say)
            except QAError as exc:
                status, say = "failed", f"{outcome.failure}: {exc.message}"
            except Exception as exc:  # reported, never silent
                log.exception("Voice command job failed")
                status, say = "failed", f"{outcome.failure} because of an unexpected error: {type(exc).__name__}."
            line = compose(status, say, self.style(), background=True, address=self.address())
            self.db.execute("UPDATE voice_commands SET status = ?, final_reply = ?, finished_at = ? WHERE id = ?",
                            (status, say, now_iso(), command_id))
            self.agent.emit(names.VOICE_COMMAND_COMPLETED if status == "completed" else names.VOICE_COMMAND_FAILED,
                            say, details={**base, "speak": line, "say": say, "navigate": navigate, "owner": outcome.owner,
                                          "result": status})

        threading.Thread(target=work, name=f"voice-{command_id}", daemon=True).start()

    def _purge_transcripts(self) -> None:
        """Delete voice history older than the retention set in Voice Settings (0 = keep). At most once a minute."""
        days = int(self.config().get("transcript_days") or 0)
        if days <= 0 or time.monotonic() - getattr(self, "_purged_at", -1e9) < 60:
            return
        self._purged_at = time.monotonic()
        from datetime import timedelta
        cutoff = (now() - timedelta(days=days)).isoformat()
        self.db.execute("DELETE FROM voice_commands WHERE created_at < ?", (cutoff,))

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
        # While a browser recording runs, "click search", "select tomorrow" and the like act in that browser
        # ("select security" is only a suite when the sentence says suite or testing).
        if intent is not None and intent.command == "select_suites" and not re.search(r"\b(suites?|testing)\b", intents.normalize(text)) \
                and self._active_recording(conv):
            intent = None
        if intent is None and self._active_recording(conv):  # 23.3.4: credentials, checks, links, values to ask for
            spoken = guided_action(intents.normalize(text))
            if spoken:
                return spoken
        if (intent is None or intent.command == "open_page") and self._active_recording(conv):
            spoken = intents.browser_action(intents.normalize(text))
            if spoken:
                return spoken
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
        if r.command == "unclear" and r.question:
            return Intent("unclear", {"question": r.question.strip()}, via="ai")
        params = registry.validate(r.command, r.all_params(), text)
        if params is None:
            return None  # unsupported, or a command the registry does not offer to the AI
        if r.command == "open_page":
            if "page" not in params:
                return None
            params = {"route": params["page"]}
        if r.command == "open_work_item":
            if "id" not in params:
                return None
            params.setdefault("kind", "work item")
        return Intent(r.command, params, via="ai")

    # ---- command validation and dispatch -------------------------------------------------------

    def _dispatch(self, conv: Conversation, intent: Intent | None, text: str = "") -> Outcome:
        if intent is None:
            question = intents.clarify_question(text)
            if question:
                return Outcome("unclear", question)
            return Outcome("unsupported", UNSUPPORTED)
        if intent.command == "unclear":
            return Outcome("unclear", intent.params.get("question") or "What should I do?")
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
        elif intent.command not in ("interrupt", "continue_work", "repeat", "explain_activity"):
            conv.pending = None  # these four look at, or drop, the waiting question themselves
        spec = registry.BY_ID.get(intent.command)
        handler = getattr(self, f"_cmd_{intent.command}", None)
        if spec is None or handler is None:
            return Outcome("unsupported", UNSUPPORTED)
        denied = self._permission_denied(intent)  # 23.3.6: voice permissions, also for a confirmed command
        if denied:
            return denied
        try:
            outcome = handler(conv, intent)
        except QAError as exc:
            return Outcome("failed", exc.message)
        if spec.destructive and outcome.status == "accepted" and not intent.params.get("confirmed"):
            conv.pending = {"kind": "confirm", "intent": intent, "at": time.monotonic()}
            return Outcome("needs_confirmation", f"{outcome.confirm_text or outcome.say} Say confirm to continue, or cancel.")
        return outcome

    def _ask(self, conv: Conversation, intent: Intent, param: str, question: str) -> Outcome:
        conv.pending = {"kind": "input", "intent": intent, "param": param, "at": time.monotonic()}
        return Outcome("needs_input", question)

    # ---- commands ------------------------------------------------------------------------------

    def _cmd_help(self, conv: Conversation, intent: Intent) -> Outcome:
        name = self.config().get("agent_command_name") or "Hami"
        return Outcome("completed", f"You can say, for example: {name}, load user story 118; generate test cases; "
                                    "create the test plan; generate automation for test case 1280; play test case 1280; "
                                    "run it again; show failed tests; select the second one; show me the report; "
                                    "test the TFS connection; or stop the current test. During a recording: open the "
                                    "calendar, select tomorrow, click search.",
                       navigate="#/command-center")

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
        label = intents.PAGE_LABELS[route]
        if route == "test-suites" and conv.test_plan_id:
            # "Now go to Test Suites": open them for the test plan being worked on.
            plan = self._current_plan(conv)
            if plan:
                return Outcome("completed", f"I'm opening Test Suites for {plan['name']}.", navigate=f"#/test-suites?plan={plan['id']}",
                               verify={"route": "test-suites", "label": "Test Suites"})
        # The dashboard opens the page and checks it really loaded before anything says it is done.
        say = f"I'm opening {label} now." if self.style() == "conversational" else f"{label} opened."
        return Outcome("completed", say, navigate=f"#/{route}", verify={"route": route, "label": label})

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

        conv.design_run_id = run_id

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            result = self.design.run(sid, run_id)
            n = len(result.get("test_cases") or [])
            return (f"Test case generation completed successfully. {n} test case{'s were' if n != 1 else ' was'} "
                    f"generated for user story {sid}. Review them on AI Test Design before saving them to TFS.",
                    f"#/test-design?story={sid}&run={run_id}")

        return Outcome("accepted", f"User story {sid} located. Generating test cases now.",
                       navigate=f"#/test-design?story={sid}", job=job, owner="test_case_design",
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
        if self.studio is not None:  # agentic: requirement -> application discovery -> Page Object script
            return Outcome("accepted", f"Reading the requirement of test case {tc}, using the application at {base_url} "
                                       f"({why}) and creating its automation script.",
                           job=self._studio_job([{"test_case_ids": [int(tc)]}], base_url, f"test case {tc}", conv),
                           owner="requirements", job_title=f"Voice: generating automation for Test Case #{tc}",
                           failure="Automation generation failed")
        run_id = new_run_id("GEN")

        def job(progress: Callable[[str], None]) -> tuple[str, str | None]:
            script = self.automation.generate(run_id, tc, base_url)
            conv.script_id = script.id
            return (f"Automation generated for test case {tc}: {len(script.steps)} steps in the script "
                    f"{script.name}. Say play test case {tc} to run it.", f"#/guided-automation?script={script.id}")

        return Outcome("accepted", f"Generating automation for test case {tc} on {base_url}, taken from {why}.",
                       job=job, owner="automation_engineering", job_title=f"Voice: generating automation for Test Case #{tc}",
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

    def _run_recorded_test_case(self, conv: Conversation, intent: Intent) -> Outcome:
        """Recorded (classic) scripts; generated framework scripts run through AutomationCommands._cmd_run_test_case."""
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

        return Outcome("accepted", f"{'Playing' if play else 'Running'} {label} now.", navigate="#/workflow",
                       job=job, owner="test_execution", job_title=f"Voice: running {label}", failure="The test could not run")

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
        return Outcome("accepted", f"Running all {n} automated test{'s' if n != 1 else ''}.",
                       navigate="#/workflow", job=job, owner="test_execution", job_title=f"Voice: running {n} automated tests")

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

        total = plan.total_duration_s()
        length = _duration_words(total) if total is not None else "loop-based"
        return Outcome("accepted", f"Preparing and running a {length} "
                                   f"{plan.name} {kind} test with {users} virtual users.",
                       navigate="#/workflow", job=job, owner="jmeter_load",
                       job_title=f"Voice: {users}-user {plan.name} {kind} test",
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

        return Outcome("accepted", f"Creating {n} defect{'s' if n != 1 else ''} in TFS now.", job=job, owner="defect_intelligence",
                       job_title=f"Voice: creating {n} defects in TFS")

    # confirm/cancel are resolved in _dispatch
    def _cmd_confirm(self, conv: Conversation, intent: Intent) -> Outcome:  # pragma: no cover
        return Outcome("completed", "There is nothing waiting for your confirmation.")

    _cmd_cancel = _cmd_confirm
