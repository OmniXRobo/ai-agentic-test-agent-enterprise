"""Requirements Intelligence: a resolved TFS scope -> structured Requirement Memory.

Keeps the original TFS identifiers and hierarchy, and extracts what the requirements expect
the application to have (pages, fields, buttons, validations, workflows) from Acceptance
Criteria, Test Steps and Expected Results. Deterministic, so it works without the AI server.
"""

from __future__ import annotations

import re
from typing import Any

from ..automation.generator import parse_tfs_steps
from ..clock import now_iso
from ..tfs.hierarchy import HierarchyService

FIELD_VERBS = r"enter|type|input|fill(?:\s+in)?|provide|key\s+in|set"
CLICK_VERBS = r"click(?:\s+on)?|press|tap|select|choose|submit|hit|open\s+the\s+menu"
NAV_VERBS = r"open|navigate\s+to|go\s+to|launch|visit|browse\s+to|access"
VERIFY_VERBS = r"verify|check|confirm|ensure|validate|observe|see"
_STOP_TAIL = re.compile(r"\b(field|textbox|text box|box|button|link|page|screen|tab|menu|icon|option|dropdown|"
                        r"checkbox|on|in|into|with|from|and|then|as|for)\b.*$", re.I)
_QUOTED = re.compile(r"[\"“'‘]([^\"”'’]{2,60})[\"”'’]")
_PAGE = re.compile(r"\b([A-Z]?[\w-]+(?:\s+[A-Z]?[\w-]+){0,2})\s+(page|screen)\b", re.I)
_VALIDATION = re.compile(r"\b(error|invalid|required|mandatory|must|message|warning|not allowed|cannot)\b", re.I)


def _clean_target(text: str) -> str:
    text = re.sub(r"^(the|a|an|valid|invalid|correct|wrong|user'?s?|customer'?s?)\s+", "", text.strip(), flags=re.I)
    text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.I)
    text = _STOP_TAIL.sub("", text).strip(" .,:;")
    return text[:60]


def classify_step(action: str) -> dict[str, Any]:
    """'Enter Username' -> {kind: field, target: Username}; 'Click Login' -> {kind: click, target: Login}."""
    a = (action or "").strip()
    quoted = _QUOTED.search(a)
    for kind, verbs in (("navigate", NAV_VERBS), ("field", FIELD_VERBS), ("click", CLICK_VERBS), ("verify", VERIFY_VERBS)):
        m = re.match(rf"^\s*(?:\d+[.)]\s*)?(?:{verbs})\b\s*(.*)$", a, re.I)
        if m:
            target = quoted.group(1) if quoted else _clean_target(m.group(1))
            if kind == "navigate" and re.search(r"\b(application|app|site|url|browser|home)\b", m.group(1), re.I) \
                    and not quoted:
                target = ""
            return {"kind": kind, "target": target}
    return {"kind": "other", "target": quoted.group(1) if quoted else _clean_target(a)}


def expected_pages(*texts: str) -> list[str]:
    out = []
    for t in texts:
        for m in _PAGE.finditer(t or ""):
            name = re.sub(r"^((the|a|an|is|are|be|on|to|back|in|at)\s+)+", "", m.group(1), flags=re.I).strip()
            if name and name.lower() not in ("same", "next", "previous", "this", "that", "the", "new") and name not in out:
                out.append(name)
    return out


def build_requirement_memory(scope: dict[str, Any], hierarchy: HierarchyService, design: Any,
                             note: Any = None) -> dict[str, Any]:
    """Scope from ``HierarchyService.resolve`` -> Requirement Memory (one record per TFS item, no duplicates)."""
    note = note or (lambda *_: None)
    items = scope["items"]
    numeric = [int(k) for k in items if k.isdigit()]
    nodes = hierarchy.nodes(numeric)
    active = set(scope["active"])
    mem: dict[str, Any] = {
        "project": {"name": scope["project"]}, "iteration": {"name": scope.get("iteration")},
        "option": scope["option"], "scope_key": scope["scope_key"], "created_at": now_iso(),
        "epics": [], "features": [], "user_stories": [], "acceptance_criteria": [], "test_cases": [], "other": [],
        "expected": {"pages": [], "fields": [], "buttons": [], "validations": [], "workflows": []},
        "active": scope["active"], "context": scope["context"], "counts": scope["counts"],
    }
    bucket = {"epic": "epics", "feature": "features", "story": "user_stories", "ac": "acceptance_criteria",
              "testcase": "test_cases"}

    def add_unique(key: str, value: str) -> None:
        if value and value.lower() not in [v.lower() for v in mem["expected"][key]]:
            mem["expected"][key].append(value)

    for key, it in items.items():
        n = nodes.get(int(key)) if key.isdigit() else None
        rec: dict[str, Any] = {"id": key if not key.isdigit() else int(key), "label": it["label"], "type": it["type"],
                               "title": it["title"], "parent": it.get("parent"), "children": it.get("children", []),
                               "active": key in active}
        if n:
            rec.update({"state": n["state"], "iteration": n["iteration"], "area": n["area"]})
        level = it["level"]
        if level == "story" and n:
            rec["description"] = n["description"]
            rec["acceptance_criteria"] = [c for c in it.get("children", []) if items.get(c, {}).get("level") == "ac"]
            rec["test_cases"] = [c for c in it.get("children", []) if items.get(c, {}).get("level") == "testcase"]
            for page in expected_pages(n["description"], n["acceptance_criteria_text"]):
                add_unique("pages", page)
        elif level == "ac":
            rec["text"] = it["title"]
            rec["work_item"] = key.isdigit()
            rec["test_cases"] = [c for c in it.get("children", []) if items.get(c, {}).get("level") == "testcase"]
            for page in expected_pages(it["title"] or ""):
                add_unique("pages", page)
            if _VALIDATION.search(it["title"] or ""):
                add_unique("validations", it["title"])
        elif level == "testcase" and n:
            steps = parse_tfs_steps(n["steps_xml"])
            rec["steps"] = []
            for i, s in enumerate(steps, start=1):
                c = classify_step(s["action"])
                rec["steps"].append({"index": i, "action": s["action"], "expected": s["expected"], **c})
                if c["kind"] == "field" and c["target"]:
                    add_unique("fields", c["target"])
                elif c["kind"] == "click" and c["target"]:
                    add_unique("buttons", c["target"])
                for page in expected_pages(s["action"], s["expected"]):
                    add_unique("pages", page)
                if _VALIDATION.search(s["expected"] or ""):
                    add_unique("validations", s["expected"])
            data = design.list_test_data(tfs_test_case_id=int(key)) if design is not None else []
            rec["test_data"] = [{"name": d["name"], "values": d["dataset"]} for d in reversed(data)
                                if isinstance(d.get("dataset"), dict)]
            # The requirement it tests, through the real Tests link (for traceability comments).
            rec["tests_requirement"] = n["tests"][0] if n["tests"] else None
        mem[bucket.get(level, "other")].append(rec)
    for story in mem["user_stories"]:
        if story["active"]:
            tcs = [t for t in mem["test_cases"] if str(t["id"]) in story.get("test_cases", [])]
            if tcs:
                pages = [p for t in tcs for s in t.get("steps", []) for p in expected_pages(s["action"], s["expected"])]
                if len(pages) > 1:
                    mem["expected"]["workflows"].append({"story": story["label"], "pages": list(dict.fromkeys(pages))})
    note(f"Loaded {len(mem['user_stories'])} User Stories, {len(mem['acceptance_criteria'])} Acceptance Criteria, "
         f"{len(mem['test_cases'])} Test Cases ({sum(len(t.get('steps', [])) for t in mem['test_cases'])} steps)")
    return mem


def guidance_keywords(memory: dict[str, Any]) -> list[str]:
    """Words the Application Discovery Agent uses to explore requirement-related controls first."""
    words = set()
    exp = memory.get("expected", {})
    for v in exp.get("pages", []) + exp.get("fields", []) + exp.get("buttons", []):
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{3,}", v))
    for ac in memory.get("acceptance_criteria", []):
        words.update(w.lower() for w in re.findall(r"[A-Za-z]{4,}", ac.get("text") or ""))
    return sorted(words - {"customer", "user", "should", "able", "page", "with", "that", "when", "then", "given"})
