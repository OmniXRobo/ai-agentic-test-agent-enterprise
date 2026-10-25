"""Intelligent discovery API: TFS requirement hierarchy (lazy tree, scope), Requirement Memory, Unified QA Knowledge."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..runtime import Runtime
from .deps import runtime

router = APIRouter()


class ScopeIn(BaseModel):
    option: str = "custom"
    iteration: str | None = None
    ids: list[str] = Field(default=[], max_length=5000)
    excluded: list[str] = Field(default=[], max_length=5000)
    stories_in: str = Field(default="sprint", pattern="^(sprint|project)$")


class IntelligentDiscoveryIn(BaseModel):
    source: str = Field(default="tfs_app", pattern="^(application|tfs|tfs_app)$")
    base_url: str | None = None
    max_pages: int = Field(default=15, ge=1, le=200)
    max_depth: int = Field(default=3, ge=0, le=10)
    headless: bool = True
    application: str | None = Field(default=None, max_length=120)
    credential_role: str | None = Field(default=None, max_length=60)
    environment: str | None = Field(default=None, max_length=60)
    explore: bool = True
    scope: ScopeIn | None = None
    # Agentic chain: after mapping, generate the Playwright scripts of the Test Cases in scope.
    generate_scripts: bool = False
    on_existing: str = Field(default="skip", pattern="^(skip|regenerate|new_version)$")


class KnowledgeScriptsIn(BaseModel):
    base_url: str | None = None
    credential_role: str | None = Field(default=None, max_length=60)
    environment: str | None = Field(default=None, max_length=60)
    headless: bool = True
    on_existing: str = Field(default="skip", pattern="^(skip|regenerate|new_version)$")


# ---- TFS requirement hierarchy ---------------------------------------------------------------


@router.get("/api/tfs/hierarchy/config")
def hierarchy_config(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.hierarchy.config(), "mode": rt.tfs.active_mode()}


@router.get("/api/tfs/hierarchy/items")
def hierarchy_items(level: str = Query(..., pattern="^(epic|feature|story|ac|testcase)$"), iteration: str | None = None,
                    text: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.hierarchy.level_items(level, iteration, text)}


@router.get("/api/tfs/hierarchy/children/{item_id}")
def hierarchy_children(item_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, **rt.hierarchy.children(item_id)}


@router.post("/api/tfs/hierarchy/resolve")
def hierarchy_resolve(body: ScopeIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "scope": rt.hierarchy.resolve(body.option, iteration=body.iteration, ids=body.ids,
                                                      excluded=body.excluded, stories_in=body.stories_in)}


@router.post("/api/tfs/hierarchy/refresh")
def hierarchy_refresh(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    rt.hierarchy.refresh()
    return {"ok": True}


# ---- intelligent discovery and knowledge -------------------------------------------------------


@router.post("/api/knowledge/discoveries", status_code=202)
def start(body: IntelligentDiscoveryIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    base_url = (body.base_url or "").strip() or None
    if base_url and not base_url.lower().startswith(("http://", "https://")):
        base_url = None
    run_id = rt.knowledge.start(body.source, base_url=base_url, scope=body.scope.model_dump() if body.scope else None,
                                max_pages=body.max_pages, max_depth=body.max_depth, headless=body.headless,
                                application=body.application, credential_role=body.credential_role,
                                environment=body.environment, explore=body.explore,
                                generate_scripts=body.generate_scripts and body.source == "tfs_app",
                                on_existing=body.on_existing)
    return {"ok": True, "run_id": run_id}


@router.post("/api/knowledge/{kid}/scripts", status_code=202)
def knowledge_scripts(kid: str, body: KnowledgeScriptsIn, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    job_id = rt.knowledge.generate_scripts(kid, base_url=(body.base_url or "").strip() or None,
                                           credential_role=body.credential_role, environment=body.environment,
                                           headless=body.headless, on_existing=body.on_existing)
    return {"ok": True, "job_id": job_id}


@router.get("/api/knowledge/requirement-memory")
def memories(rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.knowledge.memories()}


@router.get("/api/knowledge/requirement-memory/{mem_id}")
def memory(mem_id: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "memory": rt.knowledge.memory(mem_id)}


@router.get("/api/knowledge")
def knowledge_list(application: str | None = None, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "items": rt.knowledge.knowledge_list(application)}


@router.get("/api/knowledge/{kid}")
def knowledge(kid: str, rt: Runtime = Depends(runtime)) -> dict[str, Any]:
    return {"ok": True, "knowledge": rt.knowledge.knowledge(kid)}
