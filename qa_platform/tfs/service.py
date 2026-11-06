"""TFS / Azure DevOps service: profiles, connection diagnostics, queries, traceability.

Uses only the currently active, user-configured profile. Never falls back to
sample data: if TFS is unreachable the caller gets a structured error.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

import httpx

from .. import branding
from ..config import Settings
from ..credentials import TFS_NAMESPACE, SecretStore
from ..errors import QAError
from ..events import names
from ..events.bus import EventBus, QAEvent
from ..logging_setup import get_logger
from .client import AzureDevOpsClient, wiql_quote

log = get_logger("tfs")

MODES = {
    "online": {"label": "Azure DevOps Online", "url_key": "organization_url"},
    "offline": {"label": "Local TFS / Azure DevOps Server", "url_key": "collection_url"},
}

# Work item categories defined by every standard process template (Agile, Scrum, CMMI, Basic).
CATEGORIES = {
    "requirement": "Microsoft.RequirementCategory",
    "bug": "Microsoft.BugCategory",
    "testcase": "Microsoft.TestCaseCategory",
    "testplan": "Microsoft.TestPlanCategory",
    "testsuite": "Microsoft.TestSuiteCategory",
    "task": "Microsoft.TaskCategory",
    "epic": "Microsoft.EpicCategory",
    "feature": "Microsoft.FeatureCategory",
}

LIST_FIELDS = [
    "System.Id", "System.Title", "System.WorkItemType", "System.State", "System.AssignedTo",
    "System.IterationPath", "System.AreaPath", "System.Tags", "System.ChangedDate",
    "Microsoft.VSTS.Common.Priority", "Microsoft.VSTS.Common.Severity",
]

EDITABLE_FIELDS = {
    "title": "System.Title",
    "description": "System.Description",
    "state": "System.State",
    "assigned_to": "System.AssignedTo",
    "iteration_path": "System.IterationPath",
    "area_path": "System.AreaPath",
    "tags": "System.Tags",
    "priority": "Microsoft.VSTS.Common.Priority",
    "severity": "Microsoft.VSTS.Common.Severity",
    "repro_steps": "Microsoft.VSTS.TCM.ReproSteps",
    "acceptance_criteria": "Microsoft.VSTS.Common.AcceptanceCriteria",
}

TEST_PARAMETER_FIELDS = ("Microsoft.VSTS.TCM.Parameters", "Microsoft.VSTS.TCM.LocalDataSource")

RELATION_NAMES = {
    "System.LinkTypes.Hierarchy-Forward": "Child",
    "System.LinkTypes.Hierarchy-Reverse": "Parent",
    "System.LinkTypes.Related": "Related",
    "Microsoft.VSTS.Common.TestedBy-Forward": "Tested By",
    "Microsoft.VSTS.Common.TestedBy-Reverse": "Tests",
    "System.LinkTypes.Dependency-Forward": "Successor",
    "System.LinkTypes.Dependency-Reverse": "Predecessor",
}


class TTLCache:
    def __init__(self, ttl: float) -> None:
        self.ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


def _identity(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("uniqueName")
    return value


def simplify(item: dict[str, Any]) -> dict[str, Any]:
    f = item.get("fields", {})
    return {
        "id": item["id"],
        "rev": item.get("rev"),
        "title": f.get("System.Title"),
        "type": f.get("System.WorkItemType"),
        "state": f.get("System.State"),
        "assigned_to": _identity(f.get("System.AssignedTo")),
        "iteration_path": f.get("System.IterationPath"),
        "area_path": f.get("System.AreaPath"),
        "tags": f.get("System.Tags"),
        "priority": f.get("Microsoft.VSTS.Common.Priority"),
        "severity": f.get("Microsoft.VSTS.Common.Severity"),
        "changed": f.get("System.ChangedDate"),
    }


def _flatten_nodes(node: dict[str, Any], out: list[str]) -> list[str]:
    path = node.get("path", "")
    # classification paths come back as \Project\Iteration\Sprint 1; WIQL wants Project\Sprint 1
    parts = [p for p in path.split("\\") if p]
    if len(parts) >= 2 and parts[1] in ("Iteration", "Area"):
        parts.pop(1)
    if parts:
        out.append("\\".join(parts))
    for child in node.get("children", []) or []:
        _flatten_nodes(child, out)
    return out


class TfsService:
    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        bus: EventBus,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.secrets = secrets
        self.bus = bus
        self.transport = transport  # injectable for tests
        self.cache = TTLCache(ttl=60)
        self.meta_cache = TTLCache(ttl=600)
        self._last_unavailable: tuple[str, float] | None = None

    # ---- profiles ----------------------------------------------------------

    def active_mode(self) -> str:
        return self.settings.section("tfs").get("active_mode", "online")

    def profile(self, mode: str | None = None) -> dict[str, Any]:
        mode = mode or self.active_mode()
        if mode not in MODES:
            raise QAError("TFS_MODE_INVALID", f"Unknown TFS mode '{mode}'.")
        p = self.settings.section("tfs").get(mode, {})
        return {
            "mode": mode,
            "label": MODES[mode]["label"],
            "url": (p.get(MODES[mode]["url_key"]) or "").rstrip("/"),
            "project": p.get("project", ""),
            "api_version": p.get("api_version") or ("7.1" if mode == "online" else "6.0"),
            "verify_tls": p.get("verify_tls", True),
            "has_pat": self.secrets.has(TFS_NAMESPACE, mode),
        }

    def profiles(self) -> dict[str, Any]:
        return {
            "active_mode": self.active_mode(),
            "credential_store_available": self.secrets.available,
            "profiles": {m: self.profile(m) for m in MODES},
        }

    def save_profile(self, mode: str, *, url: str | None = None, project: str | None = None,
                     api_version: str | None = None, verify_tls: bool | None = None,
                     pat: str | None = None, make_active: bool = False) -> dict[str, Any]:
        if mode not in MODES:
            raise QAError("TFS_MODE_INVALID", f"Unknown TFS mode '{mode}'.")
        patch: dict[str, Any] = {}
        if url is not None:
            url = url.strip().rstrip("/")
            if url and not url.lower().startswith(("http://", "https://")):
                raise QAError("TFS_URL_INVALID", "The server URL must start with http:// or https://.")
            patch[MODES[mode]["url_key"]] = url
        if project is not None:
            patch["project"] = project.strip()
        if api_version:
            patch["api_version"] = api_version.strip()
        if verify_tls is not None and mode == "offline":
            patch["verify_tls"] = bool(verify_tls)
        tfs_patch: dict[str, Any] = {mode: patch} if patch else {}
        if make_active:
            tfs_patch["active_mode"] = mode
        if tfs_patch:
            self.settings.update({"tfs": tfs_patch})
        if pat:
            self.secrets.set(TFS_NAMESPACE, mode, pat.strip())
        self.invalidate()
        return self.profiles()

    def clear_pat(self, mode: str) -> None:
        self.secrets.delete(TFS_NAMESPACE, mode)
        self.invalidate()

    def invalidate(self) -> None:
        self.cache.clear()
        self.meta_cache.clear()

    def client(self, mode: str | None = None) -> AzureDevOpsClient:
        p = self.profile(mode)
        if not p["url"]:
            raise QAError("TFS_NOT_CONFIGURED", f"{p['label']} has no server URL configured.", status=409)
        if not p["project"]:
            raise QAError("TFS_NOT_CONFIGURED", f"{p['label']} has no project configured.", status=409)
        pat = self.secrets.get(TFS_NAMESPACE, p["mode"])
        if not pat:
            raise QAError("TFS_NOT_CONFIGURED", f"No PAT is saved for {p['label']}.", status=409)
        return AzureDevOpsClient(p["url"], p["project"], pat, p["api_version"], verify_tls=p["verify_tls"],
                                 transport=self.transport)

    def _with_client(self, fn: Callable[[AzureDevOpsClient], Any]) -> Any:
        client = self.client()
        try:
            return fn(client)
        except QAError as exc:
            if exc.error_code in ("TFS_UNREACHABLE", "TFS_TIMEOUT", "TFS_AUTH_FAILED"):
                self._unavailable(exc.message)
            raise
        finally:
            client.close()

    def _unavailable(self, reason: str) -> None:
        # Publish once per reason every 5 minutes so a dashboard refresh cannot flood notifications.
        now = time.monotonic()
        if self._last_unavailable and self._last_unavailable[0] == reason and now - self._last_unavailable[1] < 300:
            return
        self._last_unavailable = (reason, now)
        self.bus.publish(QAEvent(name=names.TFS_UNAVAILABLE, activity=reason, source="tfs.client"))

    # ---- connection test ----------------------------------------------------

    def test_connection(self, mode: str | None = None) -> dict[str, Any]:
        p = self.profile(mode)
        checks: list[dict[str, str]] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

        def skip_rest(after: str) -> None:
            for name in ("Server Reachability", "Authentication", "Organization/Collection", "Project",
                         "Work Item API", "Test Management API"):
                if not any(c["name"] == name for c in checks):
                    checks.append({"name": name, "status": "SKIP", "detail": f"Not run because {after} failed."})

        try:
            client = self.client(p["mode"])
        except QAError as exc:
            add("Configuration", False, exc.message)
            skip_rest("configuration")
            return self._test_result(p, checks)

        try:
            # Reachability: any HTTP answer at all means the server is reachable.
            try:
                client._http.get(client.base_url, timeout=10)
                add("Server Reachability", True, f"{client.base_url} responded.")
            except httpx.HTTPError as exc:
                add("Server Reachability", False, f"No response from {client.base_url}: {type(exc).__name__}: {exc}")
                skip_rest("server reachability")
                return self._test_result(p, checks)

            try:
                data = client.connection_data()
                user = (data.get("authenticatedUser") or {}).get("providerDisplayName") or "unknown user"
                if (data.get("authenticatedUser") or {}).get("descriptor", "").lower().find("anonymous") >= 0:
                    raise QAError("TFS_AUTH_FAILED", "The server treated the request as anonymous.", status=401)
                add("Authentication", True, f"Authenticated as {user}.")
            except QAError as exc:
                add("Authentication", False, exc.message)
                skip_rest("authentication")
                return self._test_result(p, checks)

            try:
                projects = client.list_projects()
                add("Organization/Collection", True, f"{len(projects)} project(s) visible in {client.base_url}.")
            except QAError as exc:
                add("Organization/Collection", False, exc.message + (f" ({exc.details})" if exc.details else ""))
                skip_rest("organization/collection")
                return self._test_result(p, checks)

            try:
                proj = client.get_project()
                add("Project", True, f"Project '{proj.get('name')}' found (state: {proj.get('state', 'unknown')}).")
            except QAError as exc:
                add("Project", False, f"Project '{p['project']}': {exc.message}")
                skip_rest("project")
                return self._test_result(p, checks)

            try:
                types = client.work_item_types()
                client.wiql("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project", top=1)
                add("Work Item API", True, f"{len(types)} work item types; WIQL queries work.")
            except QAError as exc:
                add("Work Item API", False, exc.message)

            try:
                plans = client.test_plans()
                add("Test Management API", True, f"{len(plans)} test plan(s) readable.")
            except QAError as exc:
                add("Test Management API", False, exc.message)
        finally:
            client.close()
        return self._test_result(p, checks)

    def _test_result(self, p: dict[str, Any], checks: list[dict[str, str]]) -> dict[str, Any]:
        ok = all(c["status"] == "PASS" for c in checks)
        if not ok and p["mode"] == self.active_mode():
            failed = next(c for c in checks if c["status"] == "FAIL")
            self._unavailable(f"{p['label']}: {failed['name']} failed. {failed['detail']}")
        return {"mode": p["mode"], "label": p["label"], "ok": ok, "checks": checks}

    # ---- metadata -----------------------------------------------------------

    def projects(self) -> list[dict[str, Any]]:
        return self._with_client(lambda c: [{"id": x["id"], "name": x["name"]} for x in c.list_projects()])

    def meta(self) -> dict[str, Any]:
        def load(c: AzureDevOpsClient) -> dict[str, Any]:
            types = c.work_item_types()
            cats = c.work_item_type_categories()
            categories = {}
            for key, ref in CATEGORIES.items():
                cat = next((x for x in cats if x.get("referenceName") == ref), None)
                if cat:
                    categories[key] = {
                        "default": (cat.get("defaultWorkItemType") or {}).get("name"),
                        "types": [t["name"] for t in cat.get("workItemTypes", [])],
                    }
            states: dict[str, list[str]] = {}
            closed: set[str] = set()
            for t in types:
                states[t["name"]] = [s["name"] for s in t.get("states", []) or []]
                closed.update(s["name"] for s in t.get("states", []) or []
                              if s.get("category") in ("Completed", "Removed", "Resolved"))
            iterations = _flatten_nodes(c.iterations(), [])
            areas = _flatten_nodes(c.areas(), [])
            return {
                "project": c.project,
                "work_item_types": sorted(t["name"] for t in types if not t.get("isDisabled")),
                "categories": categories,
                "states": states,
                "closed_states": sorted(closed),
                "iterations": iterations,
                "areas": areas,
            }

        key = (self.active_mode(), self.profile()["project"])
        return self.meta_cache.get_or_set(("meta", key), lambda: self._with_client(load))

    def types_for(self, category: str) -> list[str]:
        """Real work item types for a category (e.g. Bug, Defect or Issue for 'bug')."""
        cat = self.meta()["categories"].get(category)
        if not cat or not cat["types"]:
            raise QAError("TFS_CATEGORY_MISSING", f"The project's process has no work item type for '{category}'.",
                          status=409)
        return cat["types"]

    def default_type(self, category: str) -> str:
        cat = self.meta()["categories"].get(category) or {}
        return cat.get("default") or self.types_for(category)[0]

    def type_field_refs(self, type_name: str) -> set[str]:
        key = ("fields", self.active_mode(), self.profile()["project"], type_name)
        return self.meta_cache.get_or_set(
            key, lambda: self._with_client(lambda c: {f["referenceName"] for f in c.work_item_type_fields(type_name)}))

    def allowed_values(self, type_name: str, ref: str) -> list[str]:
        """Allowed values of one field (e.g. Severity), empty when the field is free text or missing."""
        key = ("allowed", self.active_mode(), self.profile()["project"], type_name)
        fields = self.meta_cache.get_or_set(key, lambda: self._with_client(
            lambda c: {f["referenceName"]: [str(v) for v in f.get("allowedValues") or []]
                       for f in c.work_item_type_fields(type_name, allowed_values=True)}))
        return fields.get(ref, [])

    def upload_attachment(self, file_name: str, data: bytes) -> dict[str, Any]:
        return self._with_client(lambda c: c.upload_attachment(file_name, data))

    def add_links(self, item_id: int, relations: list[dict[str, Any]], comment: str | None = None) -> dict[str, Any]:
        patch = [{"op": "add", "path": "/relations/-", "value": r} for r in relations]
        if comment:
            patch.append({"op": "add", "path": "/fields/System.History", "value": comment})
        updated = self._with_client(lambda c: c.update_work_item(item_id, patch))
        self.invalidate()
        return simplify(updated)

    def work_item_url(self, item_id: int) -> str:
        return f"{self.profile()['url'].rstrip('/')}/_apis/wit/workItems/{int(item_id)}"

    # ---- queries --------------------------------------------------------------

    def query(
        self,
        *,
        category: str | None = None,
        type_name: str | None = None,
        state: str | None = None,
        iteration: str | None = None,
        assigned_to: str | None = None,
        text: str | None = None,
        top: int = 200,
    ) -> dict[str, Any]:
        clauses = ["[System.TeamProject] = @project"]
        if type_name:
            clauses.append(f"[System.WorkItemType] = {wiql_quote(type_name)}")
        elif category:
            types = self.types_for(category)
            clauses.append(f"[System.WorkItemType] IN ({', '.join(wiql_quote(t) for t in types)})")
        if state:
            clauses.append(f"[System.State] = {wiql_quote(state)}")
        if iteration:
            clauses.append(f"[System.IterationPath] UNDER {wiql_quote(iteration)}")
        if assigned_to:
            if assigned_to.lower() == "@me":
                clauses.append("[System.AssignedTo] = @Me")
            else:
                clauses.append(f"[System.AssignedTo] CONTAINS {wiql_quote(assigned_to)}")
        if text:
            text = text.strip().lstrip("#")
            if text.isdigit():
                clauses.append(f"[System.Id] = {int(text)}")
            else:
                clauses.append(f"[System.Title] CONTAINS {wiql_quote(text)}")
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE " + " AND ".join(clauses)
                + " ORDER BY [System.ChangedDate] DESC")
        top = max(1, min(int(top), 1000))

        def run(c: AzureDevOpsClient) -> dict[str, Any]:
            ids = c.wiql(wiql, top=top)
            items = c.work_items(ids, fields=LIST_FIELDS) if ids else []
            return {"items": [simplify(i) for i in items], "count": len(items), "truncated": len(ids) >= top}

        return self.cache.get_or_set(("q", self.active_mode(), wiql, top), lambda: self._with_client(run))

    def count(self, category: str, extra: str = "") -> int:
        types = self.types_for(category)
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.WorkItemType] IN ("
                + ", ".join(wiql_quote(t) for t in types) + ")" + extra)
        return self.cache.get_or_set(("count", self.active_mode(), wiql),
                                     lambda: self._with_client(lambda c: len(c.wiql(wiql, top=20000))))

    def field_values(self, category: str, field_ref: str) -> list[Any]:
        """One field of every work item in a category (for example acceptance criteria of all user stories)."""
        types = self.types_for(category)
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.WorkItemType] IN ("
                + ", ".join(wiql_quote(t) for t in types) + ")")

        def run(c: AzureDevOpsClient) -> list[Any]:
            ids = c.wiql(wiql, top=20000)
            return [(w.get("fields") or {}).get(field_ref) for w in c.work_items(ids, fields=[field_ref])] if ids else []

        return self.cache.get_or_set(("field_values", self.active_mode(), wiql, field_ref), lambda: self._with_client(run))

    def acceptance_criteria_items(self, story_types: list[str], ac_types: list[str]) -> dict[str, Any]:
        """23.3.30: acceptance criteria written in the user stories' Acceptance Criteria field and kept as their own
        work items (for example an 'Acceptance Criteria' type linked under the story). Field text is returned per
        story; each criterion work item comes with the stories it is linked to."""
        field = "Microsoft.VSTS.Common.AcceptanceCriteria"

        def ids_of(types: list[str]) -> str:
            return ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND "
                    "[System.WorkItemType] IN (" + ", ".join(wiql_quote(t) for t in types) + ")")

        def run(c: AzureDevOpsClient) -> dict[str, Any]:
            story_ids = c.wiql(ids_of(story_types), top=20000)
            stories = {w["id"]: (w.get("fields") or {}).get(field)
                       for w in (c.work_items(story_ids, fields=[field]) if story_ids else [])}
            for sid in story_ids:
                stories.setdefault(sid, None)
            items: list[dict[str, Any]] = []
            if ac_types:
                ac_ids = c.wiql(ids_of(ac_types), top=20000)
                for w in c.work_items(ac_ids, expand_relations=True) if ac_ids else []:
                    linked = set()
                    for r in w.get("relations") or []:
                        url = r.get("url", "")
                        if "/workItems/" in url or "/workitems/" in url:
                            try:
                                linked.add(int(url.rsplit("/", 1)[1]))
                            except ValueError:
                                continue
                    items.append({"id": w["id"], "stories": sorted(linked & set(stories))})
            return {"stories": stories, "items": items}

        key = ("ac_items", self.active_mode(), tuple(story_types), tuple(ac_types))
        return self.cache.get_or_set(key, lambda: self._with_client(run))

    def detail(self, item_id: int) -> dict[str, Any]:
        def run(c: AzureDevOpsClient) -> dict[str, Any]:
            raw = c.work_item(item_id)
            related_ids: dict[int, str] = {}
            for rel in raw.get("relations", []) or []:
                url = rel.get("url", "")
                if "/workItems/" in url:
                    try:
                        related_ids[int(url.rsplit("/", 1)[1])] = RELATION_NAMES.get(rel.get("rel"), rel.get("rel"))
                    except ValueError:
                        continue
            linked = c.work_items(related_ids.keys(), fields=LIST_FIELDS) if related_ids else []
            links = [{**simplify(w), "relation": related_ids.get(w["id"])} for w in linked]
            attachments = [
                {"name": (r.get("attributes") or {}).get("name"), "url": r.get("url")}
                for r in raw.get("relations", []) or [] if r.get("rel") == "AttachedFile"
            ]
            fields = raw.get("fields", {})
            return {
                **simplify(raw),
                "description": fields.get("System.Description"),
                "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria"),
                "repro_steps": fields.get("Microsoft.VSTS.TCM.ReproSteps"),
                "steps": fields.get("Microsoft.VSTS.TCM.Steps"),
                # Process-specific fields (e.g. a custom 'Test Data' tab) and the test case's Parameter values.
                "extra_fields": {k: v for k, v in fields.items() if k in TEST_PARAMETER_FIELDS
                                 or not k.startswith(("System.", "Microsoft.VSTS.", "WEF_"))},
                "created_by": _identity(fields.get("System.CreatedBy")),
                "created": fields.get("System.CreatedDate"),
                "links": links,
                "attachments": attachments,
                "traceability": self._traceability(links),
                "web_url": (raw.get("_links") or {}).get("html", {}).get("href"),
            }

        return self.cache.get_or_set(("detail", self.active_mode(), int(item_id)), lambda: self._with_client(run))

    def _traceability(self, links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        try:
            cats = self.meta()["categories"]
        except QAError:
            cats = {}
        bug_types = set((cats.get("bug") or {}).get("types", []))
        test_types = set((cats.get("testcase") or {}).get("types", []))
        trace: dict[str, list[dict[str, Any]]] = {"acceptance_criteria": [], "test_cases": [], "defects": [], "other": []}
        for link in links:
            t = link.get("type") or ""
            if t in test_types:
                trace["test_cases"].append(link)
            elif t in bug_types:
                trace["defects"].append(link)
            elif "acceptance" in t.lower():
                trace["acceptance_criteria"].append(link)
            else:
                trace["other"].append(link)
        return trace

    # ---- writes ---------------------------------------------------------------

    def build_patch(self, type_name: str, values: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        """Build a JSON patch, dropping fields the work item type does not have."""
        available = self.type_field_refs(type_name)
        patch, skipped = [], []
        for key, value in values.items():
            if value is None:
                continue
            ref = EDITABLE_FIELDS.get(key, key if "." in key else None)
            if ref is None:
                skipped.append(key)
                continue
            if ref not in available:
                skipped.append(ref)
                continue
            patch.append({"op": "add", "path": f"/fields/{ref}", "value": value})
        return patch, skipped

    def create(self, type_name: str, values: dict[str, Any]) -> dict[str, Any]:
        if not values.get("title"):
            raise QAError("TFS_TITLE_REQUIRED", "A title is required.")
        if type_name not in self.meta()["work_item_types"]:
            raise QAError("TFS_TYPE_INVALID", f"'{type_name}' is not a work item type in this project.")
        patch, skipped = self.build_patch(type_name, values)
        created = self._with_client(lambda c: c.create_work_item(type_name, patch))
        self.invalidate()
        return {"item": simplify(created), "skipped_fields": skipped}

    def create_raw(self, type_name: str, patch: list[dict[str, Any]]) -> dict[str, Any]:
        """Create from a prepared JSON patch (fields already checked with build_patch)."""
        created = self._with_client(lambda c: c.create_work_item(type_name, patch))
        self.invalidate()
        return simplify(created)

    def add_to_suite(self, plan_id: int, suite_id: int, ids: list[int]) -> None:
        self._with_client(lambda c: c.add_test_cases_to_suite(plan_id, suite_id, ids))
        self.invalidate()

    def update(self, item_id: int, values: dict[str, Any]) -> dict[str, Any]:
        current = self.detail(item_id)
        patch, skipped = self.build_patch(current["type"], values)
        if not patch:
            raise QAError("TFS_NOTHING_TO_UPDATE", "No supported fields to update.", details=skipped)
        updated = self._with_client(lambda c: c.update_work_item(item_id, patch))
        self.invalidate()
        return {"item": simplify(updated), "skipped_fields": skipped}

    def delete(self, item_id: int) -> dict[str, Any]:
        """Delete a work item. A test case is deleted permanently with the test management API; any other type goes
        to the Recycle Bin."""
        item = self.detail(item_id)
        test_types = (self.meta()["categories"].get("testcase") or {}).get("types", [])
        if item["type"] in test_types:
            self._with_client(lambda c: c.delete_test_case(item_id))
            how = "deleted permanently"
        else:
            self._with_client(lambda c: c.delete_work_item(item_id))
            how = "moved to the Recycle Bin"
        self.invalidate()
        return {"id": int(item_id), "title": item["title"], "type": item["type"], "result": how}

    # ---- test management ---------------------------------------------------------

    def test_plans(self) -> list[dict[str, Any]]:
        def run(c: AzureDevOpsClient) -> list[dict[str, Any]]:
            return [{
                "id": p["id"], "name": p.get("name"), "state": p.get("state"),
                "iteration": p.get("iteration"), "area_path": (p.get("area") or {}).get("name") or p.get("areaPath"),
                "owner": _identity(p.get("owner")), "start_date": p.get("startDate"), "end_date": p.get("endDate"),
                "root_suite_id": (p.get("rootSuite") or {}).get("id"), "description": p.get("description"),
            } for p in c.test_plans()]

        return self.cache.get_or_set(("plans", self.active_mode()), lambda: self._with_client(run))

    def create_test_plan(self, name: str, *, area_path: str | None = None, iteration: str | None = None,
                         description: str | None = None) -> dict[str, Any]:
        created = self._with_client(lambda c: c.create_test_plan(name, area_path=area_path, iteration=iteration,
                                                                 description=description))
        self.invalidate()
        return {"id": created["id"], "name": created.get("name") or name,
                "root_suite_id": (created.get("rootSuite") or {}).get("id"), "created": True}

    def update_test_plan(self, plan_id: int, *, name: str | None = None, iteration: str | None = None,
                         area_path: str | None = None, state: str | None = None,
                         description: str | None = None) -> dict[str, Any]:
        """Change a test plan in TFS; ``iteration=""`` moves it back to the project's root iteration (no sprint)."""
        changes: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise QAError("PLAN_NAME_REQUIRED", "A test plan needs a name.")
            changes["name"] = name.strip()
        if iteration is not None:
            changes["iteration"] = iteration.strip() or self.profile()["project"]
        if area_path:
            changes["areaPath"] = area_path
        if state:
            changes["state"] = state
        if description is not None:
            changes["description"] = description
        if not changes:
            raise QAError("TFS_NOTHING_TO_UPDATE", "Nothing to change on this test plan.")
        updated = self._with_client(lambda c: c.update_test_plan(plan_id, changes))
        self.invalidate()
        return {"id": int(updated.get("id") or plan_id), "name": updated.get("name") or changes.get("name"),
                "iteration": updated.get("iteration"), "state": updated.get("state")}

    def delete_test_plan(self, plan_id: int) -> None:
        self._with_client(lambda c: c.delete_test_plan(plan_id))
        self.invalidate()

    def update_test_suite(self, plan_id: int, suite_id: int, name: str) -> dict[str, Any]:
        if not (name or "").strip():
            raise QAError("SUITE_NAME_REQUIRED", "A test suite needs a name.")
        updated = self._with_client(lambda c: c.update_test_suite(plan_id, suite_id, name.strip()))
        self.invalidate()
        return {"id": int(updated.get("id") or suite_id), "name": updated.get("name") or name.strip()}

    def delete_test_suite(self, plan_id: int, suite_id: int) -> None:
        root = next((s for s in self.test_suites(plan_id) if not s.get("parent_id")), None)
        if root and int(root["id"]) == int(suite_id):
            raise QAError("TFS_ROOT_SUITE", "The root suite belongs to the test plan; delete the test plan instead.",
                          status=409)
        self._with_client(lambda c: c.delete_test_suite(plan_id, suite_id))
        self.invalidate()

    def ensure_test_plan(self, name: str, *, area_path: str | None = None, iteration: str | None = None,
                         description: str | None = None) -> dict[str, Any]:
        """Reuse the plan with this exact name, otherwise create it."""
        existing = next((p for p in self.test_plans() if (p.get("name") or "").strip().lower() == name.strip().lower()), None)
        if existing:
            return {"id": existing["id"], "name": existing["name"], "root_suite_id": existing.get("root_suite_id"),
                    "created": False}
        return self.create_test_plan(name, area_path=area_path, iteration=iteration, description=description)

    def ensure_suites(self, plan_id: int, names: list[str], root_suite_id: int | None = None) -> list[dict[str, Any]]:
        """Make sure a static child suite exists under the root suite for every name; reuse ones that exist."""
        suites = self.test_suites(plan_id)
        if root_suite_id is None:
            root = next((s for s in suites if not s.get("parent_id")), None)
            if root is None:
                raise QAError("TFS_ROOT_SUITE_MISSING", f"Test plan {plan_id} has no root suite.", status=409)
            root_suite_id = root["id"]
        by_name = {(s.get("name") or "").strip().lower(): s for s in suites if s.get("parent_id") == root_suite_id}
        out = []
        for name in names:
            found = by_name.get(name.strip().lower())
            if found:
                out.append({"id": found["id"], "name": found["name"], "created": False})
                continue
            created = self._with_client(lambda c, n=name: c.create_test_suite(plan_id, root_suite_id, n))
            out.append({"id": created["id"], "name": created.get("name") or name, "created": True})
        self.invalidate()
        return out

    def test_suites(self, plan_id: int) -> list[dict[str, Any]]:
        def run(c: AzureDevOpsClient) -> list[dict[str, Any]]:
            return [{
                "id": s["id"], "name": s.get("name"), "suite_type": s.get("suiteType"),
                "parent_id": (s.get("parentSuite") or s.get("parent") or {}).get("id"),
                "test_case_count": s.get("testCaseCount"),
            } for s in c.test_suites(plan_id)]

        return self.cache.get_or_set(("suites", self.active_mode(), int(plan_id)), lambda: self._with_client(run))

    def suite_test_cases(self, plan_id: int, suite_id: int) -> list[dict[str, Any]]:
        def run(c: AzureDevOpsClient) -> list[dict[str, Any]]:
            out = []
            for tc in c.suite_test_cases(plan_id, suite_id):
                wi = tc.get("workItem") or tc.get("testCase") or {}
                out.append({"id": int(wi.get("id")), "title": wi.get("name")})
            return out

        return self.cache.get_or_set(("suitecases", self.active_mode(), int(plan_id), int(suite_id)),
                                     lambda: self._with_client(run))

    # ---- 23.3.21: automation results as TFS test run outcomes -------------------------------

    def publish_results(self, title: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Write Passed / Failed for each automated run of a test case into TFS test runs.

        ``results``: dicts with test_case_id, status (PASSED / FAILED / ...), name, duration_ms, error, run_id,
        started_at, finished_at. Runs whose test case is in a test plan become one test run per plan on that plan's
        test points (the plan's outcome changes); test cases in no plan go into one automated run without a plan."""
        outcome_of = {"PASSED": "Passed", "FAILED": "Failed", "ERROR": "Failed", "STOPPED": "Aborted",
                      "SKIPPED": "NotExecuted"}
        rows = [r for r in results if str(r.get("test_case_id") or "").isdigit()]
        report: dict[str, Any] = {"runs": [], "skipped": len(results) - len(rows), "errors": []}
        if not rows:
            return report

        def work(c: AzureDevOpsClient) -> dict[str, Any]:
            by_plan: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
            no_plan: list[dict[str, Any]] = []
            for r in rows:
                try:
                    points = c.test_points_for_case(int(r["test_case_id"]))
                except QAError as exc:
                    report["errors"].append(f"Test case {r['test_case_id']}: {exc.message}")
                    points = []
                if not points:
                    no_plan.append(r)
                for pt in points:
                    plan = int((pt.get("testPlan") or {}).get("id") or 0)
                    if plan:
                        by_plan.setdefault(plan, []).append((r, pt))

            def result_body(r: dict[str, Any]) -> dict[str, Any]:
                body = {"outcome": outcome_of.get(str(r.get("status") or "").upper(), "Failed"), "state": "Completed",
                        "comment": f"{branding.name()} run {r.get('run_id') or ''} ({r.get('browser') or 'browser'})"}
                if r.get("duration_ms") is not None:
                    body["durationInMs"] = int(r["duration_ms"])
                if r.get("error"):
                    body["errorMessage"] = str(r["error"])[:4000]
                if r.get("started_at"):
                    body["startedDate"] = r["started_at"]
                if r.get("finished_at"):
                    body["completedDate"] = r["finished_at"]
                return body

            for plan, pairs in by_plan.items():
                run = c.create_test_run({"name": title[:250], "plan": {"id": plan}, "automated": True,
                                         "pointIds": sorted({int(pt["id"]) for _, pt in pairs}), "state": "InProgress"})
                rid = int(run["id"])
                existing = c.test_run_results(rid)
                updates = []
                for res in existing:
                    point = int((res.get("testPoint") or {}).get("id") or 0)
                    match = next((r for r, pt in pairs if int(pt["id"]) == point), None)
                    if match:
                        updates.append({"id": res["id"], **result_body(match)})
                if updates:
                    c.update_test_results(rid, updates)
                c.update_test_run(rid, {"state": "Completed"})
                report["runs"].append({"id": rid, "plan_id": plan, "results": len(updates),
                                       "url": f"{c.base_url}/{c.project}/_testManagement/runs?runId={rid}"})
            if no_plan:
                run = c.create_test_run({"name": title[:250], "automated": True, "state": "InProgress"})
                rid = int(run["id"])
                c.add_test_results(rid, [{"testCase": {"id": str(r["test_case_id"])},
                                          "testCaseTitle": r.get("name") or f"Test Case {r['test_case_id']}",
                                          "automatedTestName": r.get("name") or str(r["test_case_id"]),
                                          **result_body(r)} for r in no_plan])
                c.update_test_run(rid, {"state": "Completed"})
                report["runs"].append({"id": rid, "plan_id": None, "results": len(no_plan),
                                       "url": f"{c.base_url}/{c.project}/_testManagement/runs?runId={rid}"})
            return report

        return self._with_client(work)

    # ---- dashboard -------------------------------------------------------------------

    def kpis(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        meta = self.meta()
        for key in ("requirement", "testcase"):
            if key in meta["categories"]:
                out[key] = self.count(key)
        if "bug" in meta["categories"]:
            closed = meta.get("closed_states") or []
            extra = f" AND [System.State] NOT IN ({', '.join(wiql_quote(x) for x in closed)})" if closed else ""
            out["open_defects"] = self.count("bug", extra)
        return out
