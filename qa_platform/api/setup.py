"""First-run setup API: TFS, the local LLM model and (optionally) the voice model, on one screen."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..runtime import Runtime
from .deps import runtime

router = APIRouter(prefix="/api/setup")


class SetupIn(BaseModel):
    tfs_url: str = Field(max_length=500)
    tfs_project: str = Field(max_length=200)
    tfs_pat: str | None = Field(default=None, max_length=500, description="Write-only. Stored in the OS credential store.")
    verify_tls: bool | None = None
    llm_model: str = Field(max_length=500)
    llm_server: str | None = Field(default=None, max_length=500)
    voice_model: str | None = Field(default=None, max_length=500)


@router.get("")
def status(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.setup.status()}


@router.post("")
def save(body: SetupIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.setup.save(**body.model_dump())}


@router.post("/dismiss")
def dismiss(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.setup.dismiss()}
