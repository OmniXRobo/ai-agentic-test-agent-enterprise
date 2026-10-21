"""Voice Command API: commands (spoken or typed), local speech recognition, settings, diagnostics, history."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
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
    stt_engine: Literal["local", "browser", "off"] | None = None
    stt_url: str | None = Field(default=None, max_length=300)
    stt_api: Literal["whisper_cpp", "openai"] | None = None
    stt_model: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Za-z]{2})?$")
    speak_replies: bool | None = None
    tts_voice: str | None = Field(default=None, max_length=200)
    tts_rate: float | None = Field(default=None, ge=0.5, le=2)


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


@router.get("/settings")
def get_settings(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.voice.config()}


@router.put("/settings")
def put_settings(body: VoiceSettingsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    patch = body.model_dump(exclude_none=True)
    if "stt_url" in patch:
        patch["stt_url"] = patch["stt_url"].strip().rstrip("/")
        if patch["stt_url"] and not patch["stt_url"].lower().startswith(("http://", "https://")):
            raise QAError("VOICE_URL_INVALID", "The speech server URL must start with http:// or https://.")
    rt.settings.update({"voice": patch})
    return {"ok": True, **rt.voice.config()}


@router.post("/diagnostics")
def diagnostics(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.voice.diagnostics()}
