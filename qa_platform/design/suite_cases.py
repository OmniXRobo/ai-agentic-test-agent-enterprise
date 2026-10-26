"""Suite-aware, acceptance-criterion traceable test case generation.

Flow for each selected user story::

    read story + acceptance criteria (stable AC ids)  ->  change impact on earlier generations
    -> decompose criteria into conditions (AI, deterministic fallback)
    -> generate cases for the suite's testing type (AI)
    -> validate with explicit rules -> refine once with the issues found (AI) -> validate again
    -> coverage matrix per criterion and condition

Publishing creates the TFS test cases (Title, Steps with Action / Expected Result, Tested By link to the
story), adds them to the selected suite and stores the criterion-level links in ``ac_trace``. TFS links a
test case to a user story, not to an individual acceptance criterion, so criterion links are kept by this
application and written into the test case's Summary as text; they are never presented as native TFS links.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import re
from typing import Any

from ..agents.base import new_run_id
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..logging_setup import get_logger
from ..storage.db import dumps, loads
from . import models, testing_types
from .service import DesignService, html_to_text, steps_xml

log = get_logger("design.suite_cases")

AGENT_VERSION = "suite-aware-test-case-design/1.0"

_EXPLICIT_ID = re.compile(r"^\s*\(?((?:AC|A\.C\.|Criteri(?:on|a))[\s#_-]*\d+(?:\.\d+)?[a-z]?)\)?\s*[:.)\-–]\s*(.+)$", re.I)
_GHERKIN_CONT = re.compile(r"^\s*(and|but|when|then)\b", re.I)
_GHERKIN_START = re.compile(r"^\s*(scenario(?: outline)?\s*:|given\b)", re.I)
_VAGUE = re.compile(r"^\s*(it\s+|the system\s+|system\s+)?(works?|working|functions?|behaves?)"
                    r"(\s+(as expected|correctly|properly|fine|ok))?\.?\s*$|^\s*(as expected|ok|okay|success(ful)?|"
                    r"pass(es|ed)?|correct|done|no issues?)\.?\s*$|\b(works|working) (as expected|correctly|properly)\b",
                    re.I)
_CHANGES_DATA = re.compile(r"\b(create|add|insert|delete|remove|update|edit|save|submit|register|sign up|place|order|"
                           r"purchase|pay|book|upload|import|approve|reject|transfer)\b", re.I)
_CLEANUP = re.compile(r"\b(clean ?up|restore|revert|roll ?back|delete the|remove the|reset|tear ?down|undo)\b", re.I)
_NUMBER = re.compile(r"\d")
_POLICY = re.compile(r"\b(role|permission|authori[sz]|authenticat|password|session|token|encrypt|mask|lock|policy|"
                     r"owasp|access)\w*", re.I)

ANALYSIS_PROMPT = (
    "You are an acceptance-criteria decomposition analyst. For each acceptance criterion (keep its id exactly as "
    "given) list the individually testable conditions with ids <criterion id>.1, <criterion id>.2 and so on, and the "
    "actors involved. Use only the story text: never invent business rules, values or expected results. When a "
    "criterion is too ambiguous or incomplete to write a valid test, put the question in clarification. When the "
    "testing type '{type}' cannot meaningfully test a criterion, set applicable to false with the reason. List the "
    "business rules the story states and any gaps (missing actors, permissions, inputs, limits, error behaviour).")

CASES_PROMPT = (
    "You are a suite-aware test case designer for {type}. Focus of this suite: {focus}\n"
    "Write test cases ONLY of this testing type; do not write generic functional cases with a new title. "
    "Rules: every case lists the criterion ids (ac_ids) and the condition ids it verifies; numbered steps each have "
    "one concrete action and one specific, observable expected result (never 'works correctly' or 'as expected'); "
    "use realistic synthetic input values in the actions; put credentials as {{{{SECRET:name}}}} references, never "
    "real passwords; when a case changes data add final cleanup or restoration steps; give priority 1-4, risk with a "
    "short rationale, scenario (positive, negative, boundary, ...), automation suitability and the reason when manual "
    "review is needed. Never invent business rules, thresholds or security policies: put missing information in the "
    "case's gaps. Do not repeat existing test cases listed below. List criteria this testing type cannot test in "
    "not_applicable with a reason, and questions that block a valid test in clarifications. "
    "Generate at most {max_cases} cases; only applicable cases.")

REVIEW_PROMPT = (
    "You are a test case reviewer for {type}. Fix the test cases so every issue listed is resolved: link each case "
    "to valid criterion ids and condition ids, replace vague expected results with specific observable results, add "
    "cleanup steps where data is changed, and add cases for uncovered conditions when this testing type applies "
    "(otherwise list the criterion in not_applicable with the reason). Keep refs and everything that is already "
    "correct. Never invent business rules or thresholds. Return the complete corrected set.")

PROMPT_VERSION = hashlib.sha256((AGENT_VERSION + ANALYSIS_PROMPT + CASES_PROMPT + REVIEW_PROMPT).encode()).hexdigest()[:12]


def _hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip().lower()).encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"^[\w /&-]*testing\s*-\s*", "", (text or "").lower())).strip()


def criteria_lines(acceptance_html: str | None) -> list[str]:
    """Acceptance Criteria field -> one entry per criterion; a Given/When/Then scenario stays one criterion."""
    out: list[str] = []
    for raw in html_to_text(acceptance_html).splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw).strip()
        if not line:
            continue
        if out and _GHERKIN_CONT.match(line) and not _GHERKIN_START.match(line):
            out[-1] = f"{out[-1]} {line}"
        else:
            out.append(line)
    return out


def assign_ids(lines: list[str], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable criterion ids: explicit ids (AC1, AC-02) are kept; otherwise ids from earlier generations are reused
    for unchanged text, changed criteria take the id of the criterion they replaced, new ones get the next AC-NN."""
    crit: list[dict[str, Any]] = []
    for line in lines:
        m = _EXPLICIT_ID.match(line)
        if m:
            crit.append({"id": re.sub(r"\s+", "", m.group(1)).upper(), "text": m.group(2).strip(), "explicit": True})
        else:
            crit.append({"id": None, "text": line, "explicit": False})
    by_hash = {p["ac_hash"]: p["ac_id"] for p in previous}
    taken = {c["id"] for c in crit if c["id"]}
    for c in crit:
        if not c["id"] and by_hash.get(_hash(c["text"])) and by_hash[_hash(c["text"])] not in taken:
            c["id"] = by_hash[_hash(c["text"])]
            taken.add(c["id"])
    # An edited criterion keeps the id of the previous criterion whose text it most resembles.
    freed = {p["ac_id"]: p.get("ac_text") or "" for p in previous if p["ac_id"] not in taken}
    for c in crit:
        if c["id"] or not freed:
            continue
        best = max(freed, key=lambda k: difflib.SequenceMatcher(None, freed[k].lower(), c["text"].lower()).ratio())
        if difflib.SequenceMatcher(None, freed[best].lower(), c["text"].lower()).ratio() >= 0.6:
            c["id"] = best
            taken.add(best)
            freed.pop(best)
    n = 1
    for c in crit:
        while not c["id"]:
            cand = f"AC-{n:02d}"
            n += 1
            if cand not in taken:
                c["id"] = cand
                taken.add(cand)
    for c in crit:
        c["hash"] = _hash(c["text"])
    return crit


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def titled(case_title: str, type_name: str) -> str:
    t = re.sub(rf"^\s*{re.escape(type_name)}\s*[-:–]\s*", "", case_title.strip(), flags=re.I)
    return f"{type_name} - {t}"


def validate(case_set: models.SuiteCaseSet, criteria: list[dict[str, Any]], analysis: models.SuiteAnalysis,
             ttype: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any]:
    """Explicit validation rules; returns issues per case and for the whole set. Fixes only what is safe to fix
    (title prefix, unknown ids dropped); everything else is reported."""
    known = {c["id"] for c in criteria}
    conditions = {cond.id: a.ac_id for a in analysis.criteria for cond in a.conditions}
    per_case: dict[str, list[dict[str, str]]] = {}
    for case in case_set.test_cases:
        issues: list[dict[str, str]] = []
        case.title = titled(case.title, ttype["name"])
        bad = [a for a in case.ac_ids if a not in known]
        case.ac_ids = [a for a in dict.fromkeys(case.ac_ids) if a in known]
        if bad:
            issues.append({"rule": "SOURCE_LINK", "level": "warning",
                           "message": f"Unknown criterion ids removed: {', '.join(bad)}"})
        for cid in list(case.condition_ids):
            if cid in conditions and conditions[cid] not in case.ac_ids:
                case.ac_ids.append(conditions[cid])
        case.condition_ids = [c for c in dict.fromkeys(case.condition_ids) if c in conditions]
        if not case.ac_ids:
            issues.append({"rule": "SOURCE_LINK", "level": "error",
                           "message": "Not linked to any acceptance criterion of this user story"})
        for n, step in enumerate(case.steps, start=1):
            if not step.action.strip():
                issues.append({"rule": "STEP_ACTION", "level": "error", "message": f"Step {n} has no action"})
            if not step.expected.strip():
                issues.append({"rule": "EXPECTED_RESULT", "level": "error", "message": f"Step {n} has no expected result"})
            elif _VAGUE.search(step.expected):
                issues.append({"rule": "EXPECTED_RESULT", "level": "warning",
                               "message": f"Step {n} expected result is vague: '{step.expected}'"})
        text = " ".join(s.action for s in case.steps)
        if case.scenario != "negative" and _CHANGES_DATA.search(text) and not _CLEANUP.search(text) \
                and ttype["key"] not in ("smoke", "performance"):
            issues.append({"rule": "CLEANUP", "level": "warning",
                           "message": "Changes data but has no cleanup or restoration step"})
        if ttype.get("needs_thresholds"):
            source = " ".join(c["text"] for c in criteria if c["id"] in case.ac_ids)
            missing = (ttype["key"] == "performance" and not _NUMBER.search(source)) or \
                      (ttype["key"] == "security" and not _POLICY.search(source))
            if missing:
                gap = ("No performance target is stated in the linked criteria; pass/fail cannot be decided until a "
                       "target is agreed." if ttype["key"] == "performance" else
                       "No security policy is stated in the linked criteria; pass/fail depends on the policy to be "
                       "confirmed.")
                if gap not in case.gaps:
                    case.gaps.append(gap)
                issues.append({"rule": "THRESHOLD", "level": "info", "message": gap})
        per_case[case.ref] = issues
    duplicates = []
    cases = case_set.test_cases
    for i, a in enumerate(cases):
        for b in cases[i + 1:]:
            if similar(a.title, b.title) >= 0.88 or (
                    set(a.ac_ids) == set(b.ac_ids) and [s.action.lower() for s in a.steps] == [s.action.lower() for s in b.steps]):
                duplicates.append({"ref": b.ref, "duplicate_of": a.ref, "kind": "generated"})
                per_case[b.ref].append({"rule": "DUPLICATE", "level": "warning",
                                        "message": f"Substantially overlaps {a.ref}"})
        for e in existing:
            score = similar(a.title, e.get("title") or "")
            if score >= 0.8:
                duplicates.append({"ref": a.ref, "duplicate_of": e["id"], "kind": "existing", "similarity": round(score, 2)})
                per_case[a.ref].append({"rule": "DUPLICATE", "level": "warning",
                                        "message": f"Overlaps existing test case #{e['id']} ({e.get('title')}); "
                                                   "reuse it instead of creating a duplicate"})
    return {"per_case": per_case, "duplicates": duplicates,
            "errors": sum(1 for v in per_case.values() for i in v if i["level"] == "error"),
            "warnings": sum(1 for v in per_case.values() for i in v if i["level"] == "warning")}


def coverage(criteria: list[dict[str, Any]], analysis: models.SuiteAnalysis, case_set: models.SuiteCaseSet,
             ttype: dict[str, Any]) -> list[dict[str, Any]]:
    """Requirement coverage per criterion, judged on its individual conditions (not on execution results)."""
    by_ac = {a.ac_id: a for a in analysis.criteria}
    na = {n.ac_id: n.reason for n in case_set.not_applicable}
    questions: dict[str, list[str]] = {}
    for q in case_set.clarifications:
        if q.ac_id:
            questions.setdefault(q.ac_id, []).append(q.question)
    rows = []
    for c in criteria:
        a = by_ac.get(c["id"])
        conds = [{"id": x.id, "text": x.text} for x in (a.conditions if a else [])] or \
                [{"id": f"{c['id']}.1", "text": c["text"]}]
        linked = [t for t in case_set.test_cases if c["id"] in t.ac_ids]
        covered: set[str] = set()
        for t in linked:
            ids = [x for x in t.condition_ids if x.startswith(c["id"] + ".")]
            covered.update(ids)
            if not ids and len(conds) == 1:
                covered.add(conds[0]["id"])
        missing = [x for x in conds if x["id"] not in covered]
        clar = ([a.clarification] if a and a.clarification else []) + questions.get(c["id"], [])
        reason = None
        if not linked and (c["id"] in na or (a and not a.applicable)):
            status, reason = "Not Applicable", na.get(c["id"]) or (a.not_applicable_reason if a else None)
        elif clar:  # linked cases cannot be judged complete while the criterion itself is unclear
            status = "Needs Clarification"
        elif not linked:
            status = "Not Covered"
        elif missing:
            status = "Partially Covered"
        else:
            status = "Covered"
        rows.append({
            "ac_id": c["id"], "text": c["text"], "testing_type": ttype["name"],
            "conditions": conds, "covered_conditions": sorted(covered),
            "test_cases": [t.ref for t in linked], "status": status,
            "missing": [f"{x['id']}: {x['text']}" for x in missing] if status != "Not Applicable" else [],
            "reason": reason, "clarifications": clar,
        })
    return rows


def case_summary_html(case: dict[str, Any], story: dict[str, Any], criteria: list[dict[str, Any]],
                      suite: dict[str, Any], meta: dict[str, Any]) -> str:
    texts = {c["id"]: c["text"] for c in criteria}
    e = html.escape
    li = lambda items: "<ul>" + "".join(f"<li>{e(str(x))}</li>" for x in items) + "</ul>"  # noqa: E731
    parts = [
        f"<p><strong>User Story:</strong> US{e(str(story['id']))} {e(story['title'])}</p>",
        f"<p><strong>Testing type / suite:</strong> {e(suite['testing_type_name'])} / {e(suite.get('name') or '')}</p>",
        "<p><strong>Acceptance criteria:</strong></p>"
        + li(f"{a}: {texts.get(a, '')}" for a in case["ac_ids"]),
    ]
    if case.get("objective"):
        parts.append(f"<p><strong>Objective:</strong> {e(case['objective'])}</p>")
    if case.get("preconditions"):
        parts.append(f"<p><strong>Precondition:</strong> {e(case['preconditions'])}</p>")
    if case.get("dependencies"):
        parts.append("<p><strong>Dependencies:</strong></p>" + li(case["dependencies"]))
    if case.get("test_data"):
        parts.append("<p><strong>Test data:</strong></p>" + li(case["test_data"]))
    parts.append(f"<p><strong>Priority / risk:</strong> P{case['priority']} / {e(case['risk'])}"
                 f"{(' - ' + e(case['risk_rationale'])) if case.get('risk_rationale') else ''}</p>")
    parts.append(f"<p><strong>Scenario:</strong> {e(case['scenario'])} · <strong>Automation:</strong> "
                 f"{e(case['automation'])}{(' - ' + e(case['automation_reason'])) if case.get('automation_reason') else ''}</p>")
    if case.get("gaps"):
        parts.append("<p><strong>Gaps / assumptions to review:</strong></p>" + li(case["gaps"]))
    parts.append(f"<p><em>Generated by AI Agentic Test Agent Enterprise ({e(meta['agent_version'])}, prompt "
                 f"{e(meta['prompt_version'])}, model {e(meta['model'].get('model') or 'unknown')}) from User Story "
                 f"#{e(str(story['id']))} rev {e(str(meta['requirement_revision'].get(str(story['id']), {}).get('rev')))}."
                 " Acceptance-criterion links are kept by the application; TFS links this test case to the user story."
                 "</em></p>")
    return "".join(parts)


class SuiteCaseService:
    def __init__(self, design: DesignService) -> None:
        self.design = design
        self.db = design.db
        self.tfs = design.tfs
        self.ai = design.ai
        self.runs = design.runs

    def project(self) -> str:
        return self.tfs.profile()["project"]

    # ---- suites and their testing type ---------------------------------------------------------------

    def set_suite_type(self, plan_id: int, suite_id: int, key: str) -> None:
        testing_types.get(key)
        self.db.execute("INSERT OR REPLACE INTO suite_types (project, plan_id, suite_id, testing_type, updated_at) "
                        "VALUES (?,?,?,?,?)", (self.project(), int(plan_id), int(suite_id), key, now_iso()))

    def forget_suite(self, suite_id: int) -> None:
        self.db.execute("DELETE FROM suite_types WHERE project = ? AND suite_id = ?", (self.project(), int(suite_id)))

    def forget_plan(self, plan_id: int) -> None:
        self.db.execute("DELETE FROM suite_types WHERE project = ? AND plan_id = ?", (self.project(), int(plan_id)))

    def suites(self, plan_id: int) -> list[dict[str, Any]]:
        stored = {r["suite_id"]: r["testing_type"] for r in self.db.query(
            "SELECT suite_id, testing_type FROM suite_types WHERE project = ? AND plan_id = ?",
            (self.project(), int(plan_id)))}
        out = []
        for s in self.tfs.test_suites(plan_id):
            key = stored.get(s["id"])
            t = testing_types.BY_KEY.get(key) if key else testing_types.from_suite_name(s.get("name"))
            out.append({**s, "testing_type": t["key"] if t else None, "testing_type_name": t["name"] if t else None,
                        "testing_type_source": "saved" if key else ("name" if t else None)})
        return out

    def create_suites(self, plan_id: int, keys: list[str], parent_suite_id: int | None = None,
                      prefix: str | None = None) -> list[dict[str, Any]]:
        if not keys:
            raise QAError("TESTING_TYPE_REQUIRED", "Check at least one testing type.")
        types = [testing_types.get(k) for k in dict.fromkeys(keys)]
        names_ = [f"{prefix.strip()} - {t['name']}" if prefix and prefix.strip() else t["name"] for t in types]
        if parent_suite_id is None:
            made = self.tfs.ensure_suites(plan_id, names_)
        else:
            existing = {(s.get("name") or "").strip().lower(): s for s in self.tfs.test_suites(plan_id)
                        if s.get("parent_id") == parent_suite_id}
            made = []
            for n in names_:
                if n.lower() in existing:
                    made.append({"id": existing[n.lower()]["id"], "name": n, "created": False})
                else:
                    c = self.tfs._with_client(lambda cl, n=n: cl.create_test_suite(plan_id, parent_suite_id, n))
                    made.append({"id": c["id"], "name": c.get("name") or n, "created": True})
            self.tfs.invalidate()
        for t, s in zip(types, made):
            self.set_suite_type(plan_id, s["id"], t["key"])
            s["testing_type"], s["testing_type_name"] = t["key"], t["name"]
        return made

    def suite_info(self, plan_id: int, suite_id: int, override: str | None = None) -> dict[str, Any]:
        suite = next((s for s in self.suites(plan_id) if int(s["id"]) == int(suite_id)), None)
        if suite is None:
            raise QAError("SUITE_NOT_FOUND", f"Test suite {suite_id} is not in test plan {plan_id}.", status=404)
        key = override or suite["testing_type"]
        if not key:
            raise QAError("TESTING_TYPE_REQUIRED", f"Suite '{suite['name']}' has no testing type. Set it on the Test "
                                                   "Suites page or choose one here.")
        t = testing_types.get(key)
        return {**suite, "plan_id": plan_id, "testing_type": t["key"], "testing_type_name": t["name"]}

    # ---- sprint -> user stories ------------------------------------------------------------------------

    def sprint_stories(self, iteration: str) -> list[dict[str, Any]]:
        if not (iteration or "").strip():
            raise QAError("SPRINT_REQUIRED", "Choose a sprint.")
        items = self.tfs.query(category="requirement", iteration=iteration, top=500)["items"]
        counts: dict[str, int] = {}
        for r in self.db.query("SELECT user_story_id, COUNT(DISTINCT tfs_test_case_id) AS n FROM ac_trace "
                               "WHERE project = ? GROUP BY user_story_id", (self.project(),)):
            counts[r["user_story_id"]] = r["n"]
        return [{**i, "traced_test_cases": counts.get(str(i["id"]), 0)} for i in items]

    # ---- traceability and change impact --------------------------------------------------------------

    def _trace(self, story_id: Any) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM ac_trace WHERE project = ? AND user_story_id = ? ORDER BY id",
                             (self.project(), str(story_id)))

    def _previous_criteria(self, story_id: Any) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for r in self._trace(story_id):
            seen.setdefault(r["ac_id"], {"ac_id": r["ac_id"], "ac_hash": r["ac_hash"], "ac_text": r["ac_text"]})
        for g in self.db.query("SELECT result FROM case_generations WHERE project = ? ORDER BY created_at",
                               (self.project(),)):
            for s in loads(g["result"]).get("stories", []):
                if str(s.get("story", {}).get("id")) == str(story_id):
                    for c in s.get("criteria", []):
                        seen.setdefault(c["id"], {"ac_id": c["id"], "ac_hash": c["hash"], "ac_text": c["text"]})
        return list(seen.values())

    def read_story(self, story_id: int) -> dict[str, Any]:
        item = self.tfs.detail(story_id)
        lines = criteria_lines(item.get("acceptance_criteria"))
        linked = [l["title"] for l in item["traceability"]["acceptance_criteria"] if l.get("title")]
        lines += [t for t in linked if t not in lines]
        criteria = assign_ids(lines, self._previous_criteria(story_id))
        return {
            "story": {"id": item["id"], "title": item["title"], "state": item["state"], "rev": item.get("rev"),
                      "iteration": item.get("iteration_path"), "area_path": item.get("area_path"),
                      "description": html_to_text(item.get("description")) or ""},
            "criteria": criteria,
            "existing": [{"id": t["id"], "title": t["title"], "state": t.get("state")}
                         for t in item["traceability"]["test_cases"]],
        }

    def impact(self, story_id: Any, criteria: list[dict[str, Any]], *, mark: bool = True) -> list[dict[str, Any]]:
        """Test cases whose source criterion changed or was removed since they were linked."""
        current = {c["id"]: c for c in criteria}
        out = []
        for r in self._trace(story_id):
            now_c = current.get(r["ac_id"])
            if now_c and now_c["hash"] == r["ac_hash"]:
                continue
            reason = (f"{r['ac_id']} changed: was '{r['ac_text']}', now '{now_c['text']}'" if now_c
                      else f"{r['ac_id']} was removed from the user story (was '{r['ac_text']}')")
            out.append({"tfs_test_case_id": r["tfs_test_case_id"], "title": r["test_case_title"], "ac_id": r["ac_id"],
                        "testing_type": r["testing_type"], "reason": reason})
            if mark and (r["status"] != "review" or r["review_reason"] != reason):
                self.db.execute("UPDATE ac_trace SET status = 'review', review_reason = ?, updated_at = ? WHERE id = ?",
                                (reason, now_iso(), r["id"]))
        return out

    def traceability(self, story_id: int) -> dict[str, Any]:
        data = self.read_story(story_id)
        impact = self.impact(story_id, data["criteria"])
        rows = self._trace(story_id)
        matrix = []
        for c in data["criteria"]:
            links = [r for r in rows if r["ac_id"] == c["id"]]
            by_type: dict[str, list[dict[str, Any]]] = {}
            for r in links:
                by_type.setdefault(testing_types.BY_KEY.get(r["testing_type"], {}).get("name", r["testing_type"]), []).append(
                    {"id": r["tfs_test_case_id"], "title": r["test_case_title"], "status": r["status"],
                     "conditions": loads(r["condition_ids"]) if r["condition_ids"] else [],
                     "review_reason": r["review_reason"]})
            matrix.append({"ac_id": c["id"], "text": c["text"], "by_testing_type": by_type,
                           "status": "Linked" if links else "No linked test cases"})
        return {"story": data["story"], "criteria": data["criteria"], "matrix": matrix, "impact": impact,
                "note": "Coverage shows which test cases are linked to each criterion; it is not an execution result. "
                        "Criterion-level links are kept by this application (TFS links test cases to the user story)."}

    # ---- generation ----------------------------------------------------------------------------------

    def _meta(self, stories: list[dict[str, Any]]) -> dict[str, Any]:
        cfg = self.ai.config()
        return {
            "agent": self.design.case_design.name, "agent_version": AGENT_VERSION, "prompt_version": PROMPT_VERSION,
            "model": {"mode": cfg["mode"], "label": cfg["label"], "model": cfg["model"], "base_url": cfg["base_url"],
                      "temperature": cfg["temperature"], "max_tokens": cfg["max_tokens"]},
            "requirement_revision": {str(s["story"]["id"]): {"rev": s["story"].get("rev"),
                                                              "criteria_hash": _hash("|".join(c["hash"] for c in s["criteria"]))}
                                     for s in stories},
            "generated_at": now_iso(),
        }

    def _context(self, data: dict[str, Any], ttype: dict[str, Any]) -> str:
        s = data["story"]
        crit = "\n".join(f"{c['id']}: {c['text']}" for c in data["criteria"]) or "(none)"
        existing = "\n".join(f"#{e['id']} {e['title']}" for e in data["existing"]) or "(none)"
        return (f"User story US{s['id']}: {s['title']}\nState: {s['state']}; Iteration: {s['iteration']}\n\n"
                f"Description:\n{s['description'] or '(none)'}\n\nAcceptance criteria:\n{crit}\n\n"
                f"Selected suite testing type: {ttype['name']}\n\nExisting test cases linked to this story:\n{existing}")

    def _analyse(self, context: str, data: dict[str, Any], ttype: dict[str, Any]) -> tuple[models.SuiteAnalysis, list[str]]:
        notes: list[str] = []
        try:
            analysis = self.ai.complete_json(ANALYSIS_PROMPT.format(type=ttype["name"]), context, models.SuiteAnalysis)
        except QAError:
            raise
        except Exception as exc:  # pragma: no cover - defensive: keep generating with the deterministic fallback
            analysis = models.SuiteAnalysis()
            notes.append(f"Condition breakdown was not available ({exc}); each criterion is treated as one condition.")
        known = {c["id"] for c in data["criteria"]}
        analysis.criteria = [a for a in analysis.criteria if a.ac_id in known]
        have = {a.ac_id for a in analysis.criteria}
        for c in data["criteria"]:
            if c["id"] not in have:
                analysis.criteria.append(models.CriterionAnalysis(ac_id=c["id"]))
        for a in analysis.criteria:
            conds = [x for x in a.conditions if x.text.strip()]
            for n, x in enumerate(conds, start=1):
                if not x.id.startswith(a.ac_id + "."):
                    x.id = f"{a.ac_id}.{n}"
            if not conds:
                text = next(c["text"] for c in data["criteria"] if c["id"] == a.ac_id)
                conds = [models.Condition(id=f"{a.ac_id}.1", text=text)]
            a.conditions = conds
        return analysis, notes

    def generate_story(self, story_id: int, suite: dict[str, Any], *, max_cases: int, run_id: str,
                       act: Any) -> dict[str, Any]:
        ttype = testing_types.get(suite["testing_type"])
        data = self.read_story(story_id)
        impact = self.impact(story_id, data["criteria"])
        if not data["criteria"]:
            return {**data, "testing_type": ttype["key"], "analysis": models.SuiteAnalysis().model_dump(),
                    "test_cases": [], "not_applicable": [], "clarifications": [
                        {"ac_id": None, "question": "This user story has no acceptance criteria; add them before "
                                                     "generating test cases."}],
                    "coverage": [], "validation": {"per_case": {}, "duplicates": [], "errors": 0, "warnings": 0},
                    "impact": impact, "limitations": [], "refined": False, "notes": []}
        context = self._context(data, ttype)
        act.progress(None, f"US{story_id}: breaking {len(data['criteria'])} acceptance criteria into conditions")
        analysis, notes = self._analyse(context, data, ttype)
        conds = "\n".join(f"{x.id} ({a.ac_id}): {x.text}" for a in analysis.criteria for x in a.conditions)
        prompt_ctx = context + "\n\nConditions:\n" + conds + (
            "\n\nBusiness rules stated:\n" + "\n".join(analysis.business_rules) if analysis.business_rules else "")
        act.progress(None, f"US{story_id}: generating {ttype['name']} test cases")
        case_set = self.ai.complete_json(CASES_PROMPT.format(type=ttype["name"], focus=ttype["focus"], max_cases=max_cases),
                                         prompt_ctx, models.SuiteCaseSet, max_tokens=6000)
        case_set.test_cases = case_set.test_cases[:max_cases]
        check = validate(case_set, data["criteria"], analysis, ttype, data["existing"])
        matrix = coverage(data["criteria"], analysis, case_set, ttype)
        needs_fix = check["errors"] or check["warnings"] or any(r["status"] in ("Not Covered",) for r in matrix)
        refined = False
        if needs_fix:
            issues = [f"{ref}: {i['message']}" for ref, lst in check["per_case"].items() for i in lst
                      if i["level"] in ("error", "warning")]
            issues += [f"{r['ac_id']} is {r['status']}: missing {', '.join(r['missing']) or 'all conditions'}"
                       for r in matrix if r["status"] in ("Not Covered", "Partially Covered")]
            act.progress(None, f"US{story_id}: reviewing and refining ({len(issues)} issues found)")
            try:
                refined_set = self.ai.complete_json(
                    REVIEW_PROMPT.format(type=ttype["name"]),
                    prompt_ctx + "\n\nTest cases:\n" + case_set.model_dump_json() + "\n\nIssues:\n" + "\n".join(issues),
                    models.SuiteCaseSet, max_tokens=6000)
                if refined_set.test_cases or not case_set.test_cases:
                    refined_set.test_cases = refined_set.test_cases[:max_cases]
                    check2 = validate(refined_set, data["criteria"], analysis, ttype, data["existing"])
                    if check2["errors"] <= check["errors"]:
                        case_set, check, refined = refined_set, check2, True
                        matrix = coverage(data["criteria"], analysis, case_set, ttype)
            except QAError as exc:
                notes.append(f"Refinement step failed ({exc.message}); the first draft is shown with its issues.")
        limitations = [ttype["needs_context"]] if ttype.get("needs_context") else []
        return {**data, "testing_type": ttype["key"], "analysis": analysis.model_dump(),
                "test_cases": [c.model_dump() for c in case_set.test_cases],
                "not_applicable": [n.model_dump() for n in case_set.not_applicable],
                "clarifications": [q.model_dump() for q in case_set.clarifications],
                "coverage": matrix, "validation": check, "impact": impact, "limitations": limitations,
                "refined": refined, "notes": notes}

    def run(self, plan_id: int, suite_id: int, story_ids: list[int], *, testing_type: str | None = None,
            max_cases: int = 8, run_id: str | None = None) -> dict[str, Any]:
        if not story_ids:
            raise QAError("STORY_REQUIRED", "Select at least one user story.")
        run_id = run_id or new_run_id("TCGEN")
        suite = self.suite_info(plan_id, suite_id, testing_type)
        if self.runs.get(run_id) is None:  # the API registers the run before it starts in the background
            self.runs.start(run_id, "design", f"{suite['testing_type_name']} test cases for {len(story_ids)} user "
                            "stories", user_story_id=str(story_ids[0]))
        result: dict[str, Any] = {"run_id": run_id, "plan_id": plan_id, "suite": suite, "stories": []}
        try:
            with self.design.case_design.activity(
                    f"Generating {suite['testing_type_name']} test cases for {len(story_ids)} user stories",
                    run_id=run_id) as act:
                act.event(names.TESTCASE_STARTED, "Suite-aware test case generation started")
                for n, sid in enumerate(story_ids):
                    act.progress(int(n * 100 / len(story_ids)), f"Reading User Story #{sid}")
                    result["stories"].append(self.generate_story(sid, suite, max_cases=max_cases, run_id=run_id, act=act))
                total = sum(len(s["test_cases"]) for s in result["stories"])
                act.event(names.TESTCASE_COMPLETED, f"Test cases generated: {total}")
                act.complete(f"{suite['testing_type_name']}: {total} test cases for {len(story_ids)} user stories")
            result["meta"] = self._meta(result["stories"])
        except Exception as exc:
            result["error"] = exc.message if isinstance(exc, QAError) else str(exc)
            self._save(result)
            self.runs.finish(run_id, "FAILED", {"error": result["error"]})
            raise
        self._save(result)
        self.runs.finish(run_id, "PASSED", {"test_cases": sum(len(s["test_cases"]) for s in result["stories"])})
        return result

    def _save(self, result: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO case_generations (run_id, project, plan_id, suite_id, testing_type, story_ids,"
            " created_at, result) VALUES (?,?,?,?,?,?,?,?)",
            (result["run_id"], self.project(), result.get("plan_id"), (result.get("suite") or {}).get("id"),
             (result.get("suite") or {}).get("testing_type") or "", dumps([s["story"]["id"] for s in result["stories"]]),
             now_iso(), dumps(result)))

    def get(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT result FROM case_generations WHERE run_id = ?", (run_id,))
        if not row:
            raise QAError("GENERATION_NOT_FOUND", f"No test case generation {run_id}.", status=404)
        return loads(row["result"])

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT run_id, plan_id, suite_id, testing_type, story_ids, created_at FROM case_generations "
                             "WHERE project = ? ORDER BY created_at DESC LIMIT ?", (self.project(), limit))
        for r in rows:
            r["story_ids"] = loads(r["story_ids"])
        return rows

    def edit_case(self, run_id: str, story_id: int, ref: str, case: dict[str, Any]) -> dict[str, Any]:
        """Save a reviewer's edit of one generated case before it is published (validated again)."""
        result = self.get(run_id)
        story = next((s for s in result["stories"] if int(s["story"]["id"]) == int(story_id)), None)
        if story is None:
            raise QAError("STORY_NOT_IN_RUN", f"User Story #{story_id} is not part of {run_id}.", status=404)
        idx = next((i for i, c in enumerate(story["test_cases"]) if c["ref"] == ref), None)
        if idx is None:
            raise QAError("CASE_NOT_FOUND", f"Test case {ref} is not in this generation.", status=404)
        edited = models.SuiteTestCase.model_validate({**story["test_cases"][idx], **case, "ref": ref})
        story["test_cases"][idx] = {**edited.model_dump(), "edited": True}
        ttype = testing_types.get(story["testing_type"])
        analysis = models.SuiteAnalysis.model_validate(story["analysis"])
        case_set = models.SuiteCaseSet(test_cases=[models.SuiteTestCase.model_validate(c) for c in story["test_cases"]],
                                       not_applicable=story["not_applicable"], clarifications=story["clarifications"])
        story["validation"] = validate(case_set, story["criteria"], analysis, ttype, story["existing"])
        story["coverage"] = coverage(story["criteria"], analysis, case_set, ttype)
        story["test_cases"] = [{**c.model_dump(), **({"edited": True} if story["test_cases"][i].get("edited") else {})}
                               for i, c in enumerate(case_set.test_cases)]
        self._save(result)
        return story

    # ---- publish to TFS ------------------------------------------------------------------------------

    def publish(self, run_id: str, selection: dict[str, list[str]], reuse: dict[str, int] | None = None) -> dict[str, Any]:
        """selection: {story_id: [refs]}; reuse: {"<story_id>:<ref>": existing TFS test case id} links an existing
        test case to the suite and criteria instead of creating a duplicate."""
        result = self.get(run_id)
        if result.get("error"):
            raise QAError("GENERATION_INCOMPLETE", "This generation failed; nothing to publish.", status=409)
        reuse = reuse or {}
        suite = result["suite"]
        plan_id = int(result["plan_id"])
        tc_type = self.tfs.default_type("testcase")
        meta = result.get("meta") or {}
        created: list[dict[str, Any]] = []
        with self.design.suite.activity(f"Publishing test cases to suite '{suite['name']}'", run_id=run_id) as act:
            for story in result["stories"]:
                sid = str(story["story"]["id"])
                refs = set(selection.get(sid) or [])
                if not refs:
                    continue
                story_url = self.tfs.work_item_url(int(sid))
                for case in story["test_cases"]:
                    if case["ref"] not in refs:
                        continue
                    if case.get("published_id"):
                        created.append({"story_id": sid, "ref": case["ref"], "id": case["published_id"],
                                        "title": case["title"], "action": "already published"})
                        continue
                    reuse_id = reuse.get(f"{sid}:{case['ref']}")
                    if reuse_id:
                        tc_id, action = int(reuse_id), "reused"
                    else:
                        act.progress(None, f"Creating {case['ref']}: {case['title']}")
                        values = {
                            "title": case["title"], "priority": case["priority"],
                            "iteration_path": story["story"].get("iteration"), "area_path": story["story"].get("area_path"),
                            "tags": "; ".join(["AI Generated", suite["testing_type_name"], *case["ac_ids"]]),
                            "Microsoft.VSTS.TCM.Steps": steps_xml([models.TestStep(**s) for s in case["steps"]]),
                            "description": case_summary_html(case, story["story"], story["criteria"], suite, meta),
                        }
                        patch, _skipped = self.tfs.build_patch(tc_type, values)
                        patch.append({"op": "add", "path": "/relations/-",
                                      "value": {"rel": "Microsoft.VSTS.Common.TestedBy-Reverse", "url": story_url}})
                        tc_id, action = int(self.tfs.create_raw(tc_type, patch)["id"]), "created"
                    case["published_id"] = tc_id
                    self._trace_case(sid, story["criteria"], case, suite, plan_id, tc_id, run_id)
                    created.append({"story_id": sid, "ref": case["ref"], "id": tc_id, "title": case["title"],
                                    "action": action, "ac_ids": case["ac_ids"]})
            ids = [c["id"] for c in created if c["action"] in ("created", "reused")]
            if ids:
                self.tfs.add_to_suite(plan_id, int(suite["id"]), ids)
            act.complete(f"Published {sum(1 for c in created if c['action'] == 'created')} new and "
                         f"{sum(1 for c in created if c['action'] == 'reused')} reused test cases to suite '{suite['name']}'")
        result.setdefault("published", []).append({"at": now_iso(), "items": created})
        self._save(result)
        return {"created": created, "plan_id": plan_id, "suite_id": suite["id"], "suite": suite["name"]}

    def _trace_case(self, story_id: str, criteria: list[dict[str, Any]], case: dict[str, Any], suite: dict[str, Any],
                    plan_id: int, tc_id: int, run_id: str) -> None:
        texts = {c["id"]: c for c in criteria}
        ts = now_iso()
        for ac in case["ac_ids"]:
            c = texts.get(ac)
            if not c:
                continue
            self.db.execute("DELETE FROM ac_trace WHERE project = ? AND user_story_id = ? AND ac_id = ? AND "
                            "tfs_test_case_id = ?", (self.project(), story_id, ac, tc_id))
            self.db.execute(
                "INSERT INTO ac_trace (project, user_story_id, ac_id, ac_text, ac_hash, condition_ids, plan_id, suite_id,"
                " testing_type, tfs_test_case_id, test_case_title, run_id, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.project(), story_id, ac, c["text"], c["hash"],
                 dumps([x for x in case.get("condition_ids", []) if x.startswith(ac + ".")]), plan_id, int(suite["id"]),
                 suite["testing_type"], tc_id, case["title"], run_id, "current", ts, ts))

    def trace_for_case(self, tfs_test_case_id: int) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM ac_trace WHERE tfs_test_case_id = ? ORDER BY id", (int(tfs_test_case_id),))
