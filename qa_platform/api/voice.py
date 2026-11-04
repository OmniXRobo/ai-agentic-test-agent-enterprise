"""Voice Command API: commands (spoken or typed), local speech recognition, settings, diagnostics, history."""

from __future__ import annotations

import threading
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..errors import QAError
from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/voice")

MAX_AUDIO_BYTES = 10 * 1024 * 1024


class PageIn(BaseModel):
    route: str = Field(default="", max_length=60)
    params: dict[str, str] = {}


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    conversation_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w-]+$")
    source: Literal["speech", "typed"] = "typed"
    page: PageIn | None = None


class VoiceSettingsIn(BaseModel):
    stt_engine: Literal["builtin", "local", "browser", "off"] | None = None
    stt_model_dir: str | None = Field(default=None, max_length=400)
    stt_url: str | None = Field(default=None, max_length=300)
    stt_api: Literal["whisper_cpp", "openai", "faster_whisper"] | None = None
    fw_model: str | None = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    fw_compute_type: Literal["int8", "int8_float32", "float32", "default"] | None = None
    stt_model: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Za-z]{2})?$")
    speak_replies: bool | None = None
    tts_voice: str | None = Field(default=None, max_length=200)
    tts_rate: float | None = Field(default=None, ge=0.5, le=2)
    reply_style: Literal["radio", "plain"] | None = None
    radio_voice: str | None = Field(default=None, max_length=200)
    agent_display_name: str | None = Field(default=None, min_length=1, max_length=40)
    agent_command_name: str | None = Field(default=None, min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z' -]*$")
    voice_enabled: bool | None = None
    listen_mode: Literal["push_to_talk", "wake_name"] | None = None
    wake_listening: bool | None = None
    follow_up_seconds: int | None = Field(default=None, ge=3, le=30)
    address_as: str | None = Field(default=None, max_length=20, pattern=r"^[A-Za-z' -]*$")
    approved_test_apps: list[str] | None = Field(default=None, max_length=30)
    voice_role: Literal["viewer", "tester", "full"] | None = None
    keep_transcripts: bool | None = None
    transcript_days: int | None = Field(default=None, ge=0, le=3650)
    phrase_style: Literal["standard", "tactical", "conversational"] | None = None
    radio_intensity: float | None = Field(default=None, ge=0, le=1)
    wireless_tone: bool | None = None
    static_volume: float | None = Field(default=None, ge=0, le=1)
    ambience_enabled: bool | None = None
    ambience_volume: float | None = Field(default=None, ge=0, le=1)
    voice_volume: float | None = Field(default=None, ge=0, le=1)


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=600)


@router.post("/commands")
def command(body: CommandIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    page = body.page.model_dump() if body.page else None
    return {"ok": True, **rt.voice.handle(body.text, conversation_id=body.conversation_id, source=body.source, page=page)}


@router.get("/commands")
def history(conversation_id: str | None = Query(default=None, max_length=64), limit: int = Query(default=50, ge=1, le=200),
            rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    items = rt.voice.history(conversation_id, limit)
    return {"ok": True, "items": items, "context": rt.voice.context(conversation_id) if conversation_id else None}


@router.post("/transcribe")
async def transcribe(request: Request, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Raw audio body (16 kHz mono WAV from the dashboard) -> text from the local speech server."""
    audio = await request.body()
    if len(audio) > MAX_AUDIO_BYTES:
        raise QAError("VOICE_AUDIO_TOO_LARGE", "The recording is too long. Keep commands under a minute.", status=413)
    content_type = request.headers.get("content-type", "audio/wav").split(";")[0]
    text = await run_in_threadpool(rt.voice.transcribe, audio, content_type)
    return {"ok": True, "text": text}


@router.post("/warmup")
def warmup(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """Load the offline speech model in the background (it is kept for the whole session)."""
    import threading

    cfg = rt.voice.config()
    if cfg.get("stt_engine") == "local" and cfg.get("stt_api") == "faster_whisper":
        rt.voice.fw.start_in_background()  # started with the application already; this only covers a stopped one
        return {"ok": True, "loading": True}
    if cfg.get("stt_engine") != "builtin":
        return {"ok": True, "loading": False}
    threading.Thread(target=rt.voice.offline.warm, name="speech-warmup", daemon=True).start()
    return {"ok": True, "loading": True}


@router.get("/radio-voices")
def radio_voices(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.voice.radio_voices()}


@router.post("/speak")
async def speak(body: SpeakIn, rt: Runtime = Depends(runtime)) -> Response:
    """A reply spoken by a Windows voice as WAV, for the dashboard's radio effect."""
    audio = await run_in_threadpool(rt.voice.speak, body.text)
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/settings")
def get_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.voice.config()}


@router.put("/settings")
def put_settings(body: VoiceSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "address_as" in patch:
        patch["address_as"] = " ".join(patch["address_as"].split())
    if "approved_test_apps" in patch:
        names = [" ".join(str(n).split())[:120] for n in patch["approved_test_apps"]]
        patch["approved_test_apps"] = list(dict.fromkeys(n for n in names if n))
    for key in ("agent_display_name", "agent_command_name"):
        if key in patch:
            patch[key] = " ".join(patch[key].split())
            if not patch[key]:
                raise QAError("VOICE_NAME_INVALID", "The agent name cannot be empty.")
    if "stt_url" in patch:
        patch["stt_url"] = patch["stt_url"].strip().rstrip("/")
        if patch["stt_url"] and not patch["stt_url"].lower().startswith(("http://", "https://")):
            raise QAError("VOICE_URL_INVALID", "The speech server URL must start with http:// or https://.")
    before = rt.voice.fw.config()
    was_fw = rt.voice.fw.selected()
    if patch.get("stt_api") == "faster_whisper":
        patch["stt_url"] = rt.voice.fw.url()  # the service this application runs; never typed by hand
    rt.settings.update({"voice": patch})
    fw = rt.voice.fw
    if fw.selected():
        after = fw.config()
        changed = any(before[k] != after[k] for k in ("model", "compute_type", "language", "threads", "beam_size"))
        threading.Thread(target=lambda: _fw_apply(fw, restart=changed), name="faster-whisper-apply", daemon=True).start()
    elif was_fw:
        threading.Thread(target=fw.stop, name="faster-whisper-stop", daemon=True).start()
    return {"ok": True, **rt.voice.config()}


def _fw_apply(fw: Any, restart: bool) -> None:
    try:
        fw.restart() if restart else fw.start()
    except QAError:
        pass  # Check Voice Setup and the SPEECH badge show why


@router.get("/faster-whisper")
def faster_whisper_status(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    """The local Faster-Whisper service: installed models, state, URL (23.3.7)."""
    return {"ok": True, **rt.voice.fw.status()}


@router.post("/faster-whisper/{action}")
def faster_whisper_control(action: Literal["start", "restart", "stop"], rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    fw = rt.voice.fw
    result = {"start": fw.start, "restart": fw.restart, "stop": fw.stop}[action]()
    return {"ok": True, **result}


@router.post("/diagnostics")
def diagnostics(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.voice.diagnostics()}


# ---- 23.3.6: command audit log and voice history ----------------------------------------------------------


@router.get("/audit")
def audit(limit: int = Query(500, ge=1, le=10000), format: str = Query("json", pattern="^(json|csv)$"),
          rt: Runtime = Depends(runtime)) -> Any:
    """Every voice command: when, what was heard (secrets masked), what ran, with which result."""
    rows = rt.db.query("SELECT id, created_at, finished_at, conversation_id, source, text, command, params, via, status,"
                       " reply, final_reply FROM voice_commands ORDER BY id DESC LIMIT ?", (limit,))
    if format == "json":
        return {"ok": True, "items": rows, "voice_role": rt.voice.voice_role()}
    import csv
    import io
    buf = io.StringIO()
    cols = ["id", "created_at", "finished_at", "conversation_id", "source", "text", "command", "params", "via", "status",
            "reply", "final_reply"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in cols})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="voice-audit.csv"'})


@router.delete("/history")
def delete_history(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    n = rt.db.query_one("SELECT COUNT(*) AS n FROM voice_commands")["n"]
    rt.db.execute("DELETE FROM voice_commands WHERE status NOT IN ('running', 'accepted')")
    return {"ok": True, "deleted": n - rt.db.query_one("SELECT COUNT(*) AS n FROM voice_commands")["n"]}
