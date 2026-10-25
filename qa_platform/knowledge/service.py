"""Intelligent discovery orchestration and the Unified QA Knowledge base.

Knowledge sources:

* ``application``: Application Discovery Agent only -> Application Memory.
* ``tfs``: Requirements Intelligence Agent only -> Requirement Memory (the application is not opened).
* ``tfs_app`` (recommended): Requirements Intelligence Agent -> Requirement Memory -> Application Discovery
  Agent guided by it (but not limited to it) -> QA Orchestration Agent maps requirements to the
  application -> coverage and gaps -> Unified QA Knowledge.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..agents.base import BaseAgent
from ..automation.discovery import DiscoveryJob, default_application
from ..clock import now_iso
from ..errors import QAError
from ..storage.db import Database, dumps, loads
from ..tfs.hierarchy import HierarchyService
from .mapping import map_requirements
from .requirements import build_requirement_memory, guidance_keywords

SOURCES = ("application", "tfs", "tfs_app")


class RequirementsIntelligenceAgent(BaseAgent):
    agent_id = "requirements"


class QAOrchestrationAgent(BaseAgent):
    agent_id = "orchestration"


class KnowledgeService:
    def __init__(self, db: Database, bus: Any, hierarchy: HierarchyService, automation: Any, design: Any) -> None:
        self.db = db
        self.hierarchy = hierarchy
        self.automation = automation
        self.design = design
        self.requirements_agent = RequirementsIntelligenceAgent(bus)
        self.orchestration_agent = QAOrchestrationAgent(bus)

    # ---- start ----------------------------------------------------------------------------

    def start(self, source: str, *, base_url: str | None, scope: dict[str, Any] | None, max_pages: int = 15,
              max_depth: int = 3, headless: bool = True, **discovery_options: Any) -> str:
        if source not in SOURCES:
            raise QAError("KNOWLEDGE_SOURCE_INVALID", f"Unknown discovery knowledge source '{source}'.")
        if source in ("application", "tfs_app") and not base_url:
            raise QAError("URL_INVALID", "The application URL must start with http:// or https://.")
        if source in ("tfs", "tfs_app") and not scope:
            raise QAError("HIERARCHY_SELECTION_EMPTY", "Choose the TFS requirement scope.")
        if source == "application":
            return self.automation.start_discovery(base_url, max_pages, max_depth, headless,
                                                   knowledge_source="application", **discovery_options)
        if scope:  # validate the scope now so the tester sees mistakes immediately
            self.hierarchy.resolve(**_scope_args(scope))
        application = (discovery_options.get("application") or "").strip() or \
            (default_application(base_url) if base_url else None)

        def work(job: DiscoveryJob) -> dict[str, Any]:
            run_id = job.run_id
            memory, mem_id = self._requirements(job, scope or {})
            job.requirement_memory_id = mem_id
            if source == "tfs":
                return {"id": None, "summary": {"requirements": memory["counts"]}, "requirement_memory_id": mem_id}
            discovery = self.automation.discovery.crawl(
                run_id, base_url, max_pages=max_pages, max_depth=max_depth, headless=headless, job=job,
                knowledge_source="tfs_app", guidance=guidance_keywords(memory), **discovery_options)
            job.discovery_id = discovery["id"]
            knowledge = self._map(job, memory, mem_id, discovery, application or discovery["application"])
            job.knowledge_id = knowledge["id"]
            return {**discovery, "knowledge_id": knowledge["id"]}

        return self.automation.start_discovery(base_url or "", max_pages, max_depth, headless, work=work,
                                               knowledge_source=source, scope=scope, **discovery_options)

    def _requirements(self, job: DiscoveryJob, scope: dict[str, Any]) -> tuple[dict[str, Any], str]:
        with self.requirements_agent.activity("Reading the selected TFS requirement hierarchy", run_id=job.run_id) as act:
            job.set_state("READING_REQUIREMENTS", "Requirements Intelligence Agent connecting to TFS")
            act.progress(10, f"Connecting to {self.hierarchy.tfs.profile()['label']}")
            job.set_state("RESOLVING_HIERARCHY", "Resolving parent / child relationships")
            resolved = self.hierarchy.resolve(**_scope_args(scope))
            c = resolved["counts"]
            for lvl, name in (("epic", "Epics"), ("feature", "Features"), ("story", "User Stories"),
                              ("ac", "Acceptance Criteria"), ("testcase", "Test Cases")):
                if c[lvl]:
                    act.progress(30, f"Loaded {c[lvl]} {name}")
            act.log(f"Scope normalised: {resolved['total']} item(s) in scope, {len(resolved['context'])} parent item(s) "
                    "kept for traceability; duplicates removed")
            job.set_state("BUILDING_REQUIREMENT_MEMORY", "Building Requirement Memory")
            memory = build_requirement_memory(resolved, self.hierarchy, self.design, note=lambda t: act.progress(70, t))
            mem_id = self._save_memory(memory)
            act.complete(f"Requirement Memory {mem_id}: {c['feature']} Features, {c['story']} User Stories, "
                         f"{c['ac']} Acceptance Criteria, {c['testcase']} Test Cases",
                         requirement_memory_id=mem_id)
        return memory, mem_id

    def _save_memory(self, memory: dict[str, Any]) -> str:
        """One Requirement Memory per project and scope; running the same scope again updates it."""
        project = memory["project"]["name"] or ""
        row = self.db.query_one("SELECT id, memory FROM requirement_memory WHERE project = ? AND scope_key = ?",
                                (project, memory["scope_key"]))
        if row:
            memory["version"] = int(loads(row["memory"]).get("version") or 1) + 1
            self.db.execute("UPDATE requirement_memory SET memory = ?, created_at = ? WHERE id = ?",
                            (dumps(memory), memory["created_at"], row["id"]))
            return row["id"]
        memory["version"] = 1
        mem_id = f"REQ-{uuid.uuid4().hex[:10].upper()}"
        self.db.execute("INSERT INTO requirement_memory (id, project, scope_key, created_at, scope, memory)"
                        " VALUES (?,?,?,?,?,?)",
                        (mem_id, project, memory["scope_key"], memory["created_at"],
                         dumps({"option": memory["option"], "active": memory["active"]}), dumps(memory)))
        return mem_id

    def _map(self, job: DiscoveryJob, memory: dict[str, Any], mem_id: str, discovery: dict[str, Any],
             application: str) -> dict[str, Any]:
        with self.orchestration_agent.activity("Mapping requirements to the discovered application",
                                               run_id=job.run_id) as act:
            job.set_state("MAPPING_REQUIREMENTS", "QA Orchestration Agent mapping requirements")
            mapping = map_requirements(memory, discovery)
            for ac in list(mapping["acceptance_criteria"].values())[:50]:
                if ac["mapped"]:
                    act.log(f"{ac['label']} mapped to " + ", ".join(
                        f"{m['page']}{' / ' + m['element'] if m.get('element') else ''}" for m in ac["mapped_to"][:3]))
                else:
                    act.log(f"{ac['label']} NOT MAPPED")
            job.set_state("ANALYZING_GAPS", "Analyzing requirement and application gaps")
            kid = f"QAK-{uuid.uuid4().hex[:10].upper()}"
            knowledge = {"id": kid, "application": application, "run_id": job.run_id, "created_at": now_iso(),
                         "requirement_memory_id": mem_id, "discovery_id": discovery["id"],
                         "project": memory["project"], "iteration": memory["iteration"], "option": memory["option"],
                         "counts": memory["counts"], "flows": discovery.get("flows", []),
                         "page_objects": sorted({p["name"] for p in discovery["pages"] if not p.get("error")}),
                         **mapping}
            self.db.execute("INSERT INTO qa_knowledge (id, application, run_id, discovery_id, requirement_memory_id,"
                            " created_at, knowledge) VALUES (?,?,?,?,?,?,?)",
                            (kid, application, job.run_id, discovery["id"], mem_id, knowledge["created_at"],
                             dumps(knowledge)))
            s = mapping["summary"]
            act.complete(f"Coverage {mapping['coverage']['overall']}%: {s['requirement_mappings']} mapped, "
                         f"{s['unmapped_requirements']} unmapped requirement(s), {s['unmapped_application_features']} "
                         "application area(s) without requirements", knowledge_id=kid)
        return knowledge

    # ---- reading ----------------------------------------------------------------------------

    def memory(self, mem_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM requirement_memory WHERE id = ?", (mem_id,))
        if not row:
            raise QAError("REQUIREMENT_MEMORY_NOT_FOUND", f"Requirement Memory {mem_id} does not exist.", status=404)
        return {**loads(row["memory"]), "id": row["id"]}

    def memories(self, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query("SELECT id, project, created_at, memory FROM requirement_memory ORDER BY created_at DESC LIMIT ?",
                               (limit,)):
            m = loads(r["memory"])
            out.append({"id": r["id"], "project": r["project"], "created_at": r["created_at"], "option": m.get("option"),
                        "iteration": (m.get("iteration") or {}).get("name"), "counts": m.get("counts"),
                        "version": m.get("version")})
        return out

    def knowledge(self, kid: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT knowledge FROM qa_knowledge WHERE id = ?", (kid,))
        if not row:
            raise QAError("QA_KNOWLEDGE_NOT_FOUND", f"Unified QA Knowledge {kid} does not exist.", status=404)
        return loads(row["knowledge"])

    def knowledge_list(self, application: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT knowledge FROM qa_knowledge", []
        if application:
            sql += " WHERE lower(application) = lower(?)"
            params.append(application)
        out = []
        for r in self.db.query(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit)):
            k = loads(r["knowledge"])
            out.append({"id": k["id"], "application": k["application"], "created_at": k["created_at"],
                        "project": (k.get("project") or {}).get("name"), "option": k.get("option"),
                        "coverage": k["coverage"]["overall"], "summary": k["summary"],
                        "discovery_id": k["discovery_id"], "requirement_memory_id": k["requirement_memory_id"]})
        return out

    def for_test_case(self, test_case_id: str | int, application: str | None = None) -> dict[str, Any] | None:
        """Latest Unified QA Knowledge about one Test Case (used by the Automation Engineering Agent)."""
        sql, params = "SELECT knowledge FROM qa_knowledge", []
        if application:
            sql += " WHERE lower(application) = lower(?)"
            params.append(application)
        for r in self.db.query(sql + " ORDER BY created_at DESC LIMIT 20", params):
            k = loads(r["knowledge"])
            tc = k.get("test_cases", {}).get(str(test_case_id))
            if tc:
                return {"knowledge_id": k["id"], "application": k["application"], "test_case": tc,
                        "flows": k.get("flows", []), "page_objects": k.get("page_objects", [])}
        return None


def _scope_args(scope: dict[str, Any]) -> dict[str, Any]:
    return {"option": scope.get("option") or "custom", "iteration": scope.get("iteration") or None,
            "ids": scope.get("ids") or [], "excluded": scope.get("excluded") or [],
            "stories_in": scope.get("stories_in") or "sprint"}
