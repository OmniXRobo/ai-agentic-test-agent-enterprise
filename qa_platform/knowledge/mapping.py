"""Requirement <-> application mapping: coverage per hierarchy level and gaps in both directions.

Deterministic matching of requirement words (Acceptance Criteria, test steps, expected results)
against what discovery actually found (page names, titles, headings, element names, labels,
placeholders, test ids). Works offline without the AI server; every match names the page and
element it found, so a tester can check it.
"""

from __future__ import annotations

import re
from typing import Any

from .requirements import expected_pages

STOP = {
    "the", "a", "an", "to", "on", "in", "of", "and", "or", "with", "is", "are", "be", "been", "should", "can", "could",
    "will", "must", "able", "user", "users", "customer", "customers", "tester", "system", "application", "app", "page",
    "screen", "field", "button", "link", "click", "enter", "type", "select", "verify", "check", "valid", "displayed",
    "shown", "visible", "successfully", "successful", "correct", "correctly", "their", "his", "her", "my", "your", "its",
    "that", "this", "when", "then", "given", "into", "from", "for", "as", "at", "by", "it", "not", "has", "have", "via",
    "after", "before", "new", "all", "any", "each", "open", "opens", "navigate", "see", "sees", "value", "values", "details",
}
SYNONYMS = {"signin": "login", "logon": "login", "log": "login", "sign": "login", "basket": "cart", "bag": "cart",
            "username": "username", "pwd": "password", "pass": "password", "zip": "postal", "postcode": "postal",
            "items": "item", "products": "product", "inventory": "product", "catalog": "product", "catalogue": "product",
            "orders": "order", "carts": "cart", "remove": "remove", "delete": "remove", "logout": "logout",
            "firstname": "first", "lastname": "last", "checkout": "checkout", "confirmation": "complete",
            "confirm": "complete", "completed": "complete", "thank": "complete", "purchase": "order"}
PHRASES = [(r"\buser\s+name\b", "username"), (r"\blog\s*-?\s*in\b|\bsign\s*-?\s*in\b", "login"),
           (r"\blog\s*-?\s*out\b|\bsign\s*-?\s*out\b", "logout"), (r"\be-?\s?mail\b", "email"),
           (r"\bcheck\s*-?\s*out\b", "checkout"), (r"\bpostal\s+code\b|\bzip\s*code\b", "postal"),
           (r"\bfirst\s+name\b", "first"), (r"\blast\s+name\b", "last"), (r"\badd\s+to\s+cart\b", "add cart")]
THRESHOLD = 0.5


def tokens(text: str | None) -> list[str]:
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    t = t.lower()
    for pat, rep in PHRASES:
        t = re.sub(pat, rep, t)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        if len(w) < 2 or w in STOP or w.isdigit():
            continue
        w = SYNONYMS.get(w, w)
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w not in out:
            out.append(w)
    return out


class AppIndex:
    """What discovery found, in matchable form."""

    def __init__(self, discovery: dict[str, Any]) -> None:
        self.pages: list[dict[str, Any]] = []
        for p in discovery.get("pages", []):
            if p.get("error"):
                continue
            ptoks = set(tokens(" ".join([(p.get("name") or "").removesuffix("Page"), p.get("title") or "",
                                         " ".join(p.get("headings") or []), re.sub(r"\.\w+$", "", p["url"].rsplit("/", 1)[-1])])))
            els = []
            for e in p.get("elements", []):
                desc = " ".join(x for x in (e.get("name"), e.get("label"), e.get("placeholder"), e.get("text"),
                                            e.get("test_id"), e.get("element_id")) if x)
                els.append({"el": e, "tokens": set(tokens(desc)), "kind": _kind(e)})
            self.pages.append({"page": p, "tokens": ptoks, "elements": els})

    def match(self, text: str, kind: str = "any") -> dict[str, Any] | None:
        want = tokens(text)
        if not want:
            return None
        best: tuple[float, dict[str, Any]] | None = None
        for p in self.pages:
            if kind in ("any", "navigate", "page"):
                s = len(set(want) & p["tokens"]) / len(want)
                if s and (best is None or s > best[0]):
                    best = (s, {"page": p["page"]["name"], "url": p["page"]["url"], "element": None, "selector": None})
            if kind in ("navigate", "page"):
                continue
            for e in p["elements"]:
                s = len(set(want) & e["tokens"]) / len(want)
                if kind in ("field", "click") and e["kind"] != kind:
                    s *= 0.6  # right words on the wrong kind of control
                if s and (best is None or s > best[0] + 1e-9):
                    el = e["el"]
                    best = (s, {"page": p["page"]["name"], "url": p["page"]["url"],
                                "element": el.get("name") or el.get("text") or el.get("placeholder") or el["selector"],
                                "selector": el["selector"]})
        if best and best[0] >= THRESHOLD:
            return {**best[1], "score": round(best[0], 2)}
        return None

    def related(self, text: str, limit: int = 6) -> list[dict[str, Any]]:
        """Every page/element that shares words with an Acceptance Criterion, best first."""
        want = set(tokens(text))
        if not want:
            return []
        found = []
        for p in self.pages:
            s = len(want & p["tokens"]) / len(want)
            if s:
                found.append((s, {"page": p["page"]["name"], "element": None, "url": p["page"]["url"]}, want & p["tokens"]))
            for e in p["elements"]:
                s = len(want & e["tokens"]) / len(want)
                if s:
                    el = e["el"]
                    found.append((s, {"page": p["page"]["name"], "url": p["page"]["url"], "selector": el["selector"],
                                      "element": el.get("name") or el.get("text") or el.get("placeholder") or el["selector"]},
                                  want & e["tokens"]))
        found.sort(key=lambda x: -x[0])
        out, seen, covered = [], set(), set()
        for score, f, toks in found:
            key = (f["page"], f.get("element"))
            if key in seen:
                continue
            seen.add(key)
            if len(out) < limit:
                out.append({**f, "score": round(score, 2)})
                covered |= toks
        return out if len(covered) / len(want) >= THRESHOLD else []


def _kind(e: dict[str, Any]) -> str:
    role = e.get("role") or ""
    if role in ("textbox", "combobox", "checkbox", "radio") or e.get("tag") in ("input", "textarea", "select"):
        if e.get("input_type") not in ("submit", "button"):
            return "field"
    return "click"


def map_requirements(memory: dict[str, Any], discovery: dict[str, Any]) -> dict[str, Any]:
    """Requirement Memory + Application Memory -> mapping, coverage, requirement gaps, application gaps."""
    idx = AppIndex(discovery)
    referenced_pages: set[str] = set()
    ac_map: dict[str, dict[str, Any]] = {}
    tc_map: dict[str, dict[str, Any]] = {}
    req_gaps: list[dict[str, Any]] = []
    for ac in memory.get("acceptance_criteria", []):
        if not ac["active"]:
            continue
        found = idx.related(ac.get("text") or ac["title"] or "")
        referenced_pages.update(f["url"] for f in found)
        ac_map[str(ac["id"])] = {"id": ac["id"], "label": ac["label"], "text": ac.get("text") or ac["title"],
                                 "mapped": bool(found), "mapped_to": found}
        if not found:
            req_gaps.append({"kind": "acceptance_criterion", "id": ac["id"], "label": ac["label"],
                             "text": ac.get("text"), "reason": "Acceptance Criterion cannot be mapped to the discovered application"})
    for tc in memory.get("test_cases", []):
        if not tc["active"]:
            continue
        steps_out = []
        parts = ok = 0
        for s in tc.get("steps", []):
            kind = s.get("kind") or "other"
            target = s.get("target") or ""
            match = None
            if kind == "navigate" and not target:
                first = idx.pages[0]["page"] if idx.pages else None
                match = {"page": first["name"], "url": first["url"], "element": None, "selector": None} if first else None
            elif target:
                match = idx.match(target, kind if kind in ("field", "click", "navigate") else "any")
            parts += 1
            ok += bool(match)
            if match:
                referenced_pages.add(match["url"])
            elif target:
                req_gaps.append({"kind": "test_step", "id": tc["id"], "label": tc["label"], "step": s["index"],
                                 "text": s["action"], "reason": f"Test Step references '{target}', which was not found"})
            exp_match = None
            if s.get("expected"):
                parts += 1
                exp_match = idx.match(s["expected"], "any")
                # "Cart page lists the product": the page it names was discovered, so the result is observable there.
                for name in ([] if exp_match else expected_pages(s["expected"])):
                    exp_match = idx.match(name, "page")
                    if exp_match:
                        break
                ok += bool(exp_match)
                if exp_match:
                    referenced_pages.add(exp_match["url"])
                else:
                    req_gaps.append({"kind": "expected_result", "id": tc["id"], "label": tc["label"], "step": s["index"],
                                     "text": s["expected"], "reason": "Expected Result cannot be observed in the discovered application"})
            steps_out.append({"index": s["index"], "action": s["action"], "kind": kind, "target": target,
                              "match": match, "expected": s.get("expected"), "expected_match": exp_match})
        if not steps_out:
            title_match = idx.match(tc["title"] or "")
            parts, ok = 1, int(bool(title_match))
        coverage = round(100 * ok / parts) if parts else 0
        tc_map[str(tc["id"])] = {"id": tc["id"], "label": tc["label"], "title": tc["title"], "steps": steps_out,
                                 "coverage": coverage, "mapped": coverage >= 50}
    # Coverage up the tree: AC/TC -> story -> feature -> epic (only active items count).
    items = {str(r["id"]): r for key in ("epics", "features", "user_stories", "acceptance_criteria", "test_cases")
             for r in memory.get(key, [])}
    level_of = {str(r["id"]): lvl for lvl, key in (("epic", "epics"), ("feature", "features"), ("story", "user_stories"),
                                                    ("ac", "acceptance_criteria"), ("testcase", "test_cases"))
                for r in memory.get(key, [])}
    cov: dict[str, int | None] = {}

    def coverage_of(key: str) -> int | None:
        if key in cov:
            return cov[key]
        lvl = level_of.get(key)
        if lvl == "ac":
            cov[key] = 100 if ac_map.get(key, {}).get("mapped") else 0 if key in ac_map else None
        elif lvl == "testcase":
            cov[key] = tc_map[key]["coverage"] if key in tc_map else None
        else:
            vals = [c for c in (coverage_of(k) for k in items.get(key, {}).get("children", [])) if c is not None]
            if not vals and lvl == "story" and items[key]["active"]:
                vals = [100 if idx.match(items[key]["title"] or "") else 0]
            cov[key] = round(sum(vals) / len(vals)) if vals else None
        return cov[key]

    def node(key: str) -> dict[str, Any]:
        r = items[key]
        c = coverage_of(key)
        out = {"id": r["id"], "label": r["label"], "level": level_of[key], "title": r["title"], "active": r["active"],
               "coverage": c, "status": "MAPPED" if c is not None and c >= 80 else "PARTIAL" if c else "NOT_MAPPED",
               "children": [node(k) for k in r.get("children", []) if k in items]}
        if level_of[key] == "ac" and key in ac_map:
            out["mapped_to"] = ac_map[key]["mapped_to"]
        if level_of[key] == "testcase" and key in tc_map:
            out["mapped_to"] = [m for st in tc_map[key]["steps"] for m in (st["match"], st["expected_match"]) if m]
        if level_of[key] in ("story", "feature", "epic"):
            out["application_areas"] = _areas(out)
        return out

    roots = [k for k, r in items.items() if not r.get("parent") or str(r["parent"]) not in items]
    tree = [node(k) for k in roots]
    for key, lvl in level_of.items():
        if lvl in ("story", "feature") and items[key]["active"] and coverage_of(key) == 0:
            req_gaps.append({"kind": "user_story" if lvl == "story" else "feature", "id": items[key]["id"],
                             "label": items[key]["label"], "text": items[key]["title"],
                             "reason": ("User Story workflow missing" if lvl == "story"
                                        else "Feature not represented in the discovered application")})
    # Application side: what was discovered but no selected requirement refers to.
    unmapped = []
    page_names = {p["page"]["name"] for p in idx.pages if p["page"]["url"] in referenced_pages}
    reported: set[str] = set()
    for p in idx.pages:
        page = p["page"]
        # One page object can have several URLs (item?id=1, item?id=2): any of them mapped maps the page.
        if page["name"] in page_names or page["name"] in reported or page.get("auth") in ("otp", "captcha"):
            continue
        reported.add(page["name"])
        unmapped.append({"kind": "page", "page": page["name"], "url": page["url"],
                         "reason": "Application page found; no Feature / User Story / Acceptance Criterion mapped"})
    for f in discovery.get("flows", []):
        if not set(f["pages"]) & page_names:
            unmapped.append({"kind": "workflow", "page": f["name"], "url": None,
                             "reason": "Workflow found; no Acceptance Criterion mapped"})
    whole_project = memory.get("option") == "entire_project"
    by_level = {}
    for lvl in ("epic", "feature", "story", "ac", "testcase"):
        vals = [coverage_of(k) for k, l in level_of.items() if l == lvl and items[k]["active"]]
        vals = [v for v in vals if v is not None]
        if vals:
            by_level[lvl] = {"items": len(vals), "coverage": round(sum(vals) / len(vals)),
                             "mapped": len([v for v in vals if v >= 80])}
    overall_vals = [coverage_of(k) for k in roots]
    overall_vals = [v for v in overall_vals if v is not None]
    return {
        "coverage": {"overall": round(sum(overall_vals) / len(overall_vals)) if overall_vals else 0, "by_level": by_level},
        "tree": tree, "acceptance_criteria": ac_map, "test_cases": tc_map,
        "requirement_gaps": req_gaps,
        # Requirements guide discovery but do not limit it: with a partial scope, unmapped discoveries are
        # reported as out of scope; with the entire project they are application gaps.
        "application_gaps": unmapped if whole_project else [],
        "out_of_scope": [] if whole_project else unmapped,
        "mapped_pages": sorted(page_names),
        "summary": {"requirement_mappings": len([a for a in ac_map.values() if a["mapped"]])
                    + len([t for t in tc_map.values() if t["mapped"]]),
                    "unmapped_requirements": len([g for g in req_gaps if g["kind"] in ("acceptance_criterion", "user_story", "feature")])
                    + len([t for t in tc_map.values() if not t["mapped"]]),
                    "unmapped_application_features": len(unmapped)},
    }


def _areas(node: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(n: dict[str, Any]) -> None:
        for m in n.get("mapped_to", []) or []:
            if m["page"] not in out:
                out.append(m["page"])
        for c in n.get("children", []):
            walk(c)

    walk(node)
    return out
