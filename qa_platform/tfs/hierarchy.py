"""TFS / Azure DevOps requirement hierarchy: lazy tree, scope selection, normalisation and deduplication.

Levels come from the connected project's process (work item categories), never from titles:

* epic / feature: ``Microsoft.EpicCategory`` / ``Microsoft.FeatureCategory`` (when the process has them)
* story: ``Microsoft.RequirementCategory`` (User Story, Product Backlog Item, Requirement)
* testcase: ``Microsoft.TestCaseCategory``
* ac: Acceptance Criteria work item types when the process has them; otherwise the story's
  Acceptance Criteria field split into ``AC<story>-<n>`` items (the ids Script Studio uses)

Every type list can be overridden in settings (``tfs_hierarchy``) for customised processes.
Edges are the real work item links: Hierarchy-Forward/Reverse (parent/child) and
TestedBy-Forward/Reverse (requirement <-> test case). Nothing is inferred from text.
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any

from ..design.service import html_to_text, split_criteria
from ..errors import QAError
from .client import wiql_quote
from .service import TfsService, _identity

CHILD = "System.LinkTypes.Hierarchy-Forward"
PARENT = "System.LinkTypes.Hierarchy-Reverse"
TESTED_BY = "Microsoft.VSTS.Common.TestedBy-Forward"
TESTS = "Microsoft.VSTS.Common.TestedBy-Reverse"
LEVELS = ("epic", "feature", "story", "ac", "testcase")
SCOPES = ("entire_project", "current_sprint", "epics", "features", "all_stories", "stories", "acceptance_criteria",
          "test_cases", "custom")
LARGE_SCOPE = 300
_FIELD_AC = re.compile(r"^AC(\d+)-(\d+)$")
_AC_WI = re.compile(r"^AC(\d+)$")


def _wid(url: str) -> int | None:
    if "/workItems/" not in url and "/workitems/" not in url:
        return None
    try:
        return int(url.rsplit("/", 1)[1])
    except ValueError:
        return None


class HierarchyService:
    def __init__(self, tfs: TfsService) -> None:
        self.tfs = tfs
        self._nodes: dict[tuple[str, str, int], dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ---- configuration -------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        meta = self.tfs.meta()
        cats = meta.get("categories", {})
        override = self.tfs.settings.section("tfs_hierarchy") or {}

        def pick(key: str, category: str | None, default: list[str]) -> list[str]:
            if override.get(key):
                return [t for t in override[key] if t in meta["work_item_types"]]
            if category and (cats.get(category) or {}).get("types"):
                return list(cats[category]["types"])
            return [t for t in default if t in meta["work_item_types"]]

        ac_default = [t for t in meta["work_item_types"] if "acceptance" in t.lower()]
        return {
            "project": meta.get("project"),
            "iterations": meta.get("iterations", []),
            "epic_types": pick("epic_types", "epic", ["Epic"]),
            "feature_types": pick("feature_types", "feature", ["Feature"]),
            "requirement_types": pick("requirement_types", "requirement", ["User Story", "Product Backlog Item",
                                                                           "Requirement"]),
            "test_types": pick("test_types", "testcase", ["Test Case"]),
            "acceptance_criteria_types": pick("acceptance_criteria_types", None, ac_default),
        }

    def level_of(self, type_name: str | None, cfg: dict[str, Any]) -> str:
        if type_name in cfg["epic_types"]:
            return "epic"
        if type_name in cfg["feature_types"]:
            return "feature"
        if type_name in cfg["requirement_types"]:
            return "story"
        if type_name in cfg["test_types"]:
            return "testcase"
        if type_name in cfg["acceptance_criteria_types"]:
            return "ac"
        return "other"

    def refresh(self) -> None:
        """Refresh: drop every cached node and TFS answer, so the next read sees TFS as it is now."""
        with self._lock:
            self._nodes.clear()
        self.tfs.invalidate()

    # ---- reading ----------------------------------------------------------------------------

    def _key(self, item_id: int) -> tuple[str, str, int]:
        return (self.tfs.active_mode(), self.tfs.profile()["project"], int(item_id))

    def nodes(self, ids: list[int], cfg: dict[str, Any] | None = None) -> dict[int, dict[str, Any]]:
        """Work items with fields and links, read once per session (batched) and cached."""
        cfg = cfg or self.config()
        want = list(dict.fromkeys(int(i) for i in ids))
        with self._lock:
            missing = [i for i in want if self._key(i) not in self._nodes]
        if missing:
            raw = self.tfs._with_client(lambda c: c.work_items(missing, expand_relations=True))
            with self._lock:
                for item in raw:
                    self._nodes[self._key(item["id"])] = self._node(item, cfg)
        with self._lock:
            return {i: self._nodes[self._key(i)] for i in want if self._key(i) in self._nodes}

    def _node(self, item: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
        f = item.get("fields", {})
        rel: dict[str, list[int]] = {"children": [], "parents": [], "tested_by": [], "tests": [], "related": []}
        names = {CHILD: "children", PARENT: "parents", TESTED_BY: "tested_by", TESTS: "tests",
                 "System.LinkTypes.Related": "related"}
        for r in item.get("relations", []) or []:
            target = _wid(r.get("url", ""))
            if target is not None and r.get("rel") in names:
                rel[names[r["rel"]]].append(target)
        node = {
            "id": item["id"], "type": f.get("System.WorkItemType"), "title": f.get("System.Title"),
            "state": f.get("System.State"), "iteration": f.get("System.IterationPath"),
            "area": f.get("System.AreaPath"), "assigned_to": _identity(f.get("System.AssignedTo")),
            "description": html_to_text(f.get("System.Description")),
            "acceptance_criteria_text": html_to_text(f.get("Microsoft.VSTS.Common.AcceptanceCriteria")),
            "steps_xml": f.get("Microsoft.VSTS.TCM.Steps"), **rel,
        }
        node["level"] = self.level_of(node["type"], cfg)
        return node

    def field_criteria(self, story: dict[str, Any]) -> list[dict[str, Any]]:
        items = split_criteria(story.get("acceptance_criteria_text") or "")
        return [{"id": f"AC{story['id']}-{n}", "type": "Acceptance Criterion", "level": "ac", "title": text,
                 "parent": story["id"], "work_item": False, "tested_by": []} for n, text in enumerate(items, start=1)]

    def _wiql_ids(self, types: list[str], iteration: str | None = None, text: str | None = None,
                  top: int = 1000) -> list[int]:
        if not types:
            return []
        clauses = ["[System.TeamProject] = @project",
                   f"[System.WorkItemType] IN ({', '.join(wiql_quote(t) for t in types)})"]
        if iteration:
            clauses.append(f"[System.IterationPath] UNDER {wiql_quote(iteration)}")
        if text:
            t = text.strip().lstrip("#")
            t = re.sub(r"^(US|F|E|TC|AC)(?=\d+$)", "", t, flags=re.I)
            clauses.append(f"[System.Id] = {int(t)}" if t.isdigit() else f"[System.Title] CONTAINS {wiql_quote(t)}")
        wiql = "SELECT [System.Id] FROM WorkItems WHERE " + " AND ".join(clauses) + " ORDER BY [System.Id]"
        return self.tfs.cache.get_or_set(("hier-wiql", self.tfs.active_mode(), wiql, top),
                                         lambda: self.tfs._with_client(lambda c: c.wiql(wiql, top=top)))

    # ---- tree browsing (lazy) ------------------------------------------------------------------

    def level_items(self, level: str, iteration: str | None = None, text: str | None = None) -> dict[str, Any]:
        cfg = self.config()
        types = {"epic": cfg["epic_types"], "feature": cfg["feature_types"], "story": cfg["requirement_types"],
                 "testcase": cfg["test_types"], "ac": cfg["acceptance_criteria_types"]}.get(level)
        if types is None:
            raise QAError("HIERARCHY_LEVEL_INVALID", f"Unknown hierarchy level '{level}'.")
        ids = self._wiql_ids(types, iteration, text, top=500)
        nodes = self.nodes(ids, cfg)
        return {"level": level, "available": bool(types), "types": types,
                "items": [self._brief(nodes[i]) for i in ids if i in nodes], "truncated": len(ids) >= 500}

    def _brief(self, n: dict[str, Any]) -> dict[str, Any]:
        has = bool(n["children"] or n["tested_by"] or (n["level"] == "story" and n["acceptance_criteria_text"]))
        return {"id": n["id"], "type": n["type"], "level": n["level"], "title": n["title"], "state": n["state"],
                "iteration": n["iteration"], "has_children": has, "label": label_of(n)}

    def children(self, item_id: str | int) -> dict[str, Any]:
        """One level below a node: features/stories (Child links), Acceptance Criteria and Test Cases of a story."""
        cfg = self.config()
        m = _FIELD_AC.match(str(item_id))
        if m:
            return {"id": item_id, "items": []}
        node = self.nodes([int(item_id)], cfg).get(int(item_id))
        if node is None:
            raise QAError("TFS_NOT_FOUND", f"Work item {item_id} was not found.", status=404)
        kids = self.nodes(node["children"] + node["tested_by"], cfg)
        items = [self._brief(kids[i]) for i in node["children"] if i in kids and kids[i]["level"] != "testcase"]
        if node["level"] == "story":
            acs = [self._brief(kids[i]) for i in node["children"] if i in kids and kids[i]["level"] == "ac"]
            items = [x for x in items if x["level"] != "ac"]
            if not acs:
                acs = [{"id": a["id"], "type": a["type"], "level": "ac", "title": a["title"], "has_children": False,
                        "label": a["id"]} for a in self.field_criteria(node)]
            items = acs + items
        tests = [self._brief(kids[i]) for i in dict.fromkeys(node["tested_by"] + node["children"])
                 if i in kids and kids[i]["level"] == "testcase"]
        return {"id": node["id"], "items": items + tests}

    # ---- scope resolution ---------------------------------------------------------------------

    def resolve(self, option: str, *, iteration: str | None = None, ids: list[str | int] | None = None,
                excluded: list[str | int] | None = None, stories_in: str = "sprint") -> dict[str, Any]:
        """Normalise a selection into one logical hierarchy: active items, parent context, counts, no duplicates."""
        if option not in SCOPES:
            raise QAError("HIERARCHY_SCOPE_INVALID", f"Unknown requirement scope '{option}'.")
        cfg = self.config()
        ids = [str(i).strip() for i in ids or [] if str(i).strip()]
        excluded_set = {str(i) for i in excluded or []}
        if option == "entire_project":
            seeds = self._wiql_ids(cfg["epic_types"] + cfg["feature_types"] + cfg["requirement_types"], top=20000)
        elif option == "current_sprint":
            if not iteration:
                raise QAError("HIERARCHY_SPRINT_REQUIRED", "Choose the Sprint / Iteration.")
            seeds = self._wiql_ids(cfg["requirement_types"], iteration, top=20000)
        elif option == "all_stories":
            if stories_in == "sprint" and not iteration:
                raise QAError("HIERARCHY_SPRINT_REQUIRED", "Choose the Sprint / Iteration, or All User Stories in the Entire Project.")
            seeds = self._wiql_ids(cfg["requirement_types"], iteration if stories_in == "sprint" else None, top=20000)
        else:
            if not ids:
                raise QAError("HIERARCHY_SELECTION_EMPTY", "Select at least one work item.")
            seeds = ids
        return self._expand([str(s) for s in seeds], excluded_set, cfg, option, iteration,
                            sprint_filter=iteration if option == "current_sprint" else None)

    def _expand(self, seeds: list[str], excluded: set[str], cfg: dict[str, Any], option: str,
                iteration: str | None, sprint_filter: str | None) -> dict[str, Any]:
        items: dict[str, dict[str, Any]] = {}
        active: set[str] = set()
        context: set[str] = set()
        reads = 0

        def wi(ids: list[int]) -> dict[int, dict[str, Any]]:
            nonlocal reads
            reads += 1
            return self.nodes(ids, cfg)

        def put(n: dict[str, Any], parent: str | None, is_active: bool) -> str:
            key = str(n["id"])
            if key not in items:
                items[key] = {k: n.get(k) for k in ("id", "type", "level", "title", "state", "iteration")}
                items[key]["label"] = label_of(n)
                items[key]["parent"] = parent
                items[key]["children"] = []
            elif parent and items[key]["parent"] is None:
                items[key]["parent"] = parent
            if parent and parent in items and key not in items[parent]["children"]:
                items[parent]["children"].append(key)
            if is_active:
                active.add(key)
                context.discard(key)
            elif key not in active:
                context.add(key)
            return key

        # Field-based Acceptance Criteria need their story; work item ids are read in one batch.
        wi_ids = sorted({int(_FIELD_AC.match(s).group(1)) if _FIELD_AC.match(s) else
                         int(_AC_WI.match(s).group(1)) if _AC_WI.match(s) else int(s) for s in seeds
                         if s.isdigit() or _FIELD_AC.match(s) or _AC_WI.match(s)})
        seed_nodes = wi(wi_ids)
        frontier: list[tuple[int, str | None]] = []
        for s in seeds:
            fm = _FIELD_AC.match(s)
            if fm:
                story = seed_nodes.get(int(fm.group(1)))
                if story is None:
                    continue
                sk = put(story, self._add_context(story, put, wi), False)
                crit = next((a for a in self.field_criteria(story) if a["id"] == s), None)
                if crit:
                    key = s
                    if key not in items:
                        items[key] = {**{k: crit[k] for k in ("id", "type", "level", "title")}, "label": crit["id"],
                                      "parent": sk, "children": [], "work_item": False}
                        items[sk]["children"].append(key)
                    active.add(key)
                continue
            num = int(_AC_WI.match(s).group(1)) if _AC_WI.match(s) else int(s) if s.isdigit() else None
            node = seed_nodes.get(num) if num is not None else None
            if node is None:
                continue
            put(node, self._add_context(node, put, wi), True)
            frontier.append((node["id"], None))
        # Walk down: Child links, then Acceptance Criteria and Test Cases of each requirement.
        seen: set[int] = set()
        while frontier:
            batch_ids = [i for i, _ in frontier if i not in seen]
            frontier = []
            if not batch_ids:
                break
            seen.update(batch_ids)
            parents = wi(batch_ids)
            child_ids = sorted({c for p in parents.values() for c in p["children"] + p["tested_by"]})
            kids = wi(child_ids) if child_ids else {}
            for pid in batch_ids:
                p = parents.get(pid)
                if p is None:
                    continue
                pkey = str(pid)
                for cid in dict.fromkeys(p["children"] + p["tested_by"]):
                    c = kids.get(cid)
                    if c is None or str(cid) in excluded or c["level"] == "other":
                        continue
                    if sprint_filter and c["level"] == "story" and not _under(c["iteration"], sprint_filter):
                        continue  # other sprints' stories are not part of a sprint scope
                    if c["level"] in ("epic", "feature", "story") and p["level"] in ("story", "testcase"):
                        continue  # only walk down the hierarchy
                    put(c, pkey, True)
                    if c["level"] != "testcase":
                        frontier.append((cid, pkey))
                if p["level"] == "story" and not any(kids.get(c, {}).get("level") == "ac" for c in p["children"]):
                    for crit in self.field_criteria(p):
                        if crit["id"] in excluded:
                            continue
                        if crit["id"] not in items:
                            items[crit["id"]] = {**{k: crit[k] for k in ("id", "type", "level", "title")},
                                                 "label": crit["id"], "parent": pkey, "children": [], "work_item": False}
                            items[pkey]["children"].append(crit["id"])
                        active.add(crit["id"])
        counts = {lvl: len([k for k in active if items[k]["level"] == lvl]) for lvl in LEVELS}
        roots = [k for k, v in items.items() if not v["parent"] or v["parent"] not in items]
        total = sum(counts.values())
        scope_key = hashlib.sha1(",".join(sorted(active)).encode()).hexdigest()[:16]
        return {
            "project": cfg["project"], "iteration": iteration, "option": option, "counts": counts,
            "total": total, "large": total > LARGE_SCOPE or option == "entire_project", "items": items,
            "roots": roots, "active": sorted(active, key=_sort_key), "context": sorted(context, key=_sort_key),
            "scope_key": scope_key, "tfs_reads": reads, "config": {k: cfg[k] for k in cfg if k.endswith("_types")},
        }

    def _add_context(self, node: dict[str, Any], put: Any, wi: Any) -> str | None:
        """Ancestors (Parent links; a Test Case's tested requirement) kept for traceability, not in the active scope."""
        chain: list[dict[str, Any]] = []
        current = node
        for _ in range(6):
            up = current["parents"][:1] or (current["tests"][:1] if current["level"] == "testcase" else [])
            if not up:
                break
            parent = wi(up).get(up[0])
            if parent is None or any(p["id"] == parent["id"] for p in chain):
                break
            chain.append(parent)
            current = parent
        prev: str | None = None
        for anc in reversed(chain):
            prev = put(anc, prev, False)
        return prev


def _under(path: str | None, iteration: str) -> bool:
    if not path:
        return False
    p, it = path.lower().rstrip("\\"), iteration.lower().rstrip("\\")
    return p == it or p.startswith(it + "\\")


def _sort_key(k: str) -> tuple[int, str]:
    m = re.match(r"^(?:AC)?(\d+)", k)
    return (int(m.group(1)) if m else 0, k)


def label_of(n: dict[str, Any]) -> str:
    prefix = {"epic": "E", "feature": "F", "story": "US", "testcase": "TC", "ac": "AC"}.get(n.get("level"), "#")
    return f"{prefix}{n['id']}"
