"""Suite-aware test data: TFS test case (+ its user story, criteria and suite) -> versioned, traceable data rows.

Traceability: User Story -> Acceptance Criterion -> Test Suite -> Test Case -> Dataset -> Data Row. Each
generated row is a ``test_data`` record (so automation keeps binding rows to steps as before) with a stable
``row_key``, the dataset it belongs to (``dataset_key`` + ``version``), its scenario, intended validity,
deliberately violated rule, expected outcome and criterion ids.

Generation never provisions anything: rows are stored here and only written to TFS when asked. Secrets are
stored as ``{{SECRET:name}}`` references, dynamic dates as rules such as ``{{TODAY+3d:%d/%m/%Y}}`` that the
runner resolves from the Windows system clock at execution time (the preview resolves them now).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
from datetime import datetime
from typing import Any, Callable

from ..automation.framework.template.utilities import runtime_values
from ..automation.generator import parse_tfs_steps
from ..clock import now_iso
from ..errors import QAError
from ..events import names
from ..storage.db import dumps, loads
from . import models, testing_types
from .service import DesignService, html_to_text
from .suite_cases import SuiteCaseService

AGENT_VERSION = "suite-aware-test-data/1.0"
RULE_VERSION = "data-rules/1.0"

DATA_PROMPT = (
    "You are a suite-aware test data designer for {type}. Data focus: {focus}\n"
    "Produce reusable data rows for the test case below. Cover, where they apply: valid rows, invalid rows, "
    "boundary and just-outside-boundary values, missing/empty/null/whitespace inputs, duplicates, date/time "
    "dependencies, roles and permissions, parent/child records, Unicode and special characters, and pairwise "
    "combinations. Each row names its scenario, whether it is intended to be valid, the rule an invalid row breaks "
    "on purpose, the observable expected outcome and the criterion ids it supports. Describe each field (type, "
    "required, rule) and mark a rule 'specified' only when the story or test case states it. Never invent business "
    "rules, valid credentials, database structures or performance thresholds: list them as gaps. Use synthetic "
    "values only; credentials as {{{{SECRET:name}}}}; e-mail addresses on example.com; for relative dates use "
    "{{{{TODAY}}}}, {{{{TODAY+3d:%d/%m/%Y}}}}, {{{{TODAY-2d}}}}, {{{{NOW+2h+30min:%H:%M}}}}; use {{{{UNIQUE}}}} where "
    "values must be unique per run. List data scenarios that do not apply to this testing type in not_applicable "
    "with the reason. At most {max_rows} rows.")

PROMPT_VERSION = hashlib.sha256((AGENT_VERSION + RULE_VERSION + DATA_PROMPT).encode()).hexdigest()[:12]

SECRET_FIELD = re.compile(r"pass(word|wd|code)?$|^pwd$|secret|token|api[_ -]?key|^pin$|otp|cvv|cvc|private[_ ]?key", re.I)
SECRET_REF = re.compile(r"^\s*\{\{\s*(SECRET|CRED):[^}]+\}\}\s*$")
EMAIL = re.compile(r"^([^@\s]+)@([^@\s]+)$")
SAFE_DOMAINS = ("example.com", "example.org", "example.net", "test", "invalid", "localhost", "example")
FORMULA = ("=", "+", "-", "@", "\t", "\r")
NULL_TOKEN = "<null>"


def is_secret_field(name: str) -> bool:
    return bool(SECRET_FIELD.search(re.sub(r"[^a-z]+", "_", name.lower()).strip("_")) or
                SECRET_FIELD.search(name.strip()))


def preview(values: dict[str, Any], seed: int, clock: Callable[[], datetime] = datetime.now) -> dict[str, Any]:
    """Resolve dynamic rules for display only; stored values keep the rule. Secrets are never resolved."""
    state = random.getstate()
    random.seed(seed)
    try:
        out = {}
        for k, v in values.items():
            try:
                out[k] = runtime_values.resolve(v, values, clock)
            except Exception:  # an unresolved {{DATA:x}} is reported by the binding check, not here
                out[k] = v
        return out
    finally:
        random.setstate(state)


def check_rows(rows: list[models.DataRow], ac_ids: set[str]) -> tuple[list[models.DataRow], list[dict[str, str]]]:
    """Explicit rules applied to generated rows; returns cleaned rows and what was changed or flagged."""
    issues: list[dict[str, str]] = []
    out: list[models.DataRow] = []
    seen: set[str] = set()
    for row in rows:
        if not row.values and row.scenario not in ("missing", "empty", "null"):
            issues.append({"rule": "ROW_EMPTY", "row": row.name, "message": "Row has no values; dropped"})
            continue
        key = json.dumps([row.scenario, row.values], sort_keys=True, default=str)
        if key in seen:
            issues.append({"rule": "DUPLICATE_ROW", "row": row.name, "message": "Identical to another row; dropped"})
            continue
        seen.add(key)
        if not row.intended_valid and not (row.violated_rule or "").strip():
            issues.append({"rule": "VIOLATED_RULE", "row": row.name,
                           "message": "Invalid row does not state which rule it breaks; review before use"})
        if ac_ids:
            bad = [a for a in row.ac_ids if a not in ac_ids]
            row.ac_ids = [a for a in row.ac_ids if a in ac_ids]
            if bad:
                issues.append({"rule": "SOURCE_LINK", "row": row.name,
                               "message": f"Unknown criterion ids removed: {', '.join(bad)}"})
        for k, v in list(row.values.items()):
            if is_secret_field(k) and isinstance(v, str) and v.strip() and not SECRET_REF.match(v) \
                    and row.intended_valid:
                row.values[k] = "{{SECRET:" + re.sub(r"\W+", "_", k).strip("_").lower() + "}}"
                issues.append({"rule": "SECRET", "row": row.name,
                               "message": f"Plain-text value of '{k}' replaced with a secret reference"})
            elif isinstance(v, str):
                m = EMAIL.match(v.strip())
                if m and not m.group(2).lower().endswith(SAFE_DOMAINS):
                    row.values[k] = f"{m.group(1)}@example.com"
                    issues.append({"rule": "SAFE_DESTINATION", "row": row.name,
                                   "message": f"'{v}' changed to an example.com address so no real e-mail is sent"})
        if not row.expected_outcome.strip():
            issues.append({"rule": "EXPECTED_OUTCOME", "row": row.name, "message": "No expected outcome stated"})
        out.append(row)
    return out, issues


def _csv_cell(value: Any) -> str:
    if value is None:
        return NULL_TOKEN
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return "'" + text if text.startswith(FORMULA) else text


def _from_csv_cell(text: str) -> Any:
    if text == NULL_TOKEN:
        return None
    if len(text) > 1 and text[0] == "'" and text[1:].startswith(FORMULA):
        return text[1:]
    return text


class TestDataService:
    def __init__(self, design: DesignService, cases: SuiteCaseService) -> None:
        self.design = design
        self.cases = cases
        self.db = design.db
        self.tfs = design.tfs
        self.ai = design.ai

    # ---- context ---------------------------------------------------------------------------------------

    def _case_context(self, tc_id: int) -> dict[str, Any]:
        item = self.tfs.detail(tc_id)
        tc_types = (self.tfs.meta()["categories"].get("testcase") or {}).get("types", [])
        if tc_types and item.get("type") not in tc_types:
            raise QAError("NOT_A_TEST_CASE", f"#{tc_id} is a {item.get('type')}, not a test case.")
        story_link = next((l for l in item["links"] if l.get("relation") == "Tests"), None)
        trace = self.cases.trace_for_case(tc_id)
        story_id = (story_link or {}).get("id") or (int(trace[0]["user_story_id"]) if trace else None)
        story, criteria = None, []
        if story_id:
            data = self.cases.read_story(int(story_id))
            story, criteria = data["story"], data["criteria"]
        key = trace[0]["testing_type"] if trace else None
        t = testing_types.BY_KEY.get(key) if key else testing_types.from_suite_name(item.get("title"))
        t = t or testing_types.BY_KEY["functional"]
        linked_ac = sorted({r["ac_id"] for r in trace}) or [c["id"] for c in criteria]
        return {"item": item, "steps": parse_tfs_steps(item.get("steps")), "story": story, "criteria": criteria,
                "testing_type": t, "suite_id": trace[0]["suite_id"] if trace else None, "ac_ids": linked_ac,
                "iteration": item.get("iteration_path") or (story or {}).get("iteration")}

    def _prompt_context(self, ctx: dict[str, Any]) -> str:
        item, story = ctx["item"], ctx["story"]
        steps = "\n".join(f"{n}. {s['action']} => {s['expected']}" for n, s in enumerate(ctx["steps"], start=1)) or "(none)"
        crit = "\n".join(f"{c['id']}: {c['text']}" for c in ctx["criteria"] if c["id"] in ctx["ac_ids"]) or "(none)"
        return (f"Test case #{item['id']}: {item['title']}\nTesting type: {ctx['testing_type']['name']}\n"
                f"Summary:\n{html_to_text(item.get('description')) or '(none)'}\n\nSteps (action => expected):\n{steps}\n\n"
                + (f"User story US{story['id']}: {story['title']}\n{story['description']}\n\n" if story else "")
                + f"Acceptance criteria:\n{crit}")

    # ---- generate ----------------------------------------------------------------------------------------

    def generate(self, test_case_ids: list[int], *, seed: int | None = None, max_rows: int = 12,
                 run_id: str | None = None) -> dict[str, Any]:
        if not test_case_ids:
            raise QAError("TEST_CASE_REQUIRED", "Select at least one test case.")
        out: list[dict[str, Any]] = []
        with self.design.data.activity(f"Building suite-aware test data for {len(test_case_ids)} test cases",
                                       run_id=run_id) as act:
            act.event(names.TESTDATA_STARTED, "Test data generation started")
            for n, tc in enumerate(dict.fromkeys(int(x) for x in test_case_ids)):
                act.progress(int(n * 100 / len(test_case_ids)), f"Test case #{tc}: reading steps and criteria")
                out.append(self._generate_one(tc, seed, max_rows, act))
            rows = sum(len(x["rows"]) for x in out)
            act.event(names.TESTDATA_COMPLETED, f"Data rows built: {rows}")
            act.complete(f"Data rows built: {rows} for {len(out)} test cases")
        return {"results": out}

    def _generate_one(self, tc_id: int, seed: int | None, max_rows: int, act: Any) -> dict[str, Any]:
        ctx = self._case_context(tc_id)
        t = ctx["testing_type"]
        story_id = str(ctx["story"]["id"]) if ctx["story"] else None
        ac_hash = hashlib.sha256("|".join(c["hash"] for c in ctx["criteria"]).encode()).hexdigest()[:16]
        seed = seed if seed is not None else int(hashlib.sha256(f"{tc_id}|{ac_hash}".encode()).hexdigest()[:8], 16) % 1_000_000
        act.progress(None, f"Test case #{tc_id}: generating {t['name']} data")
        design = self.ai.complete_json(DATA_PROMPT.format(type=t["name"], focus=t["data"], max_rows=max_rows),
                                       self._prompt_context(ctx), models.TestDataDesign, max_tokens=6000)
        rows, issues = check_rows(design.rows[:max_rows], {c["id"] for c in ctx["criteria"]})
        cfg = self.ai.config()
        dataset_key = f"DS-{tc_id}-{t['key']}"
        prev = self.db.query_one(
            "SELECT MAX(v) AS v FROM (SELECT json_extract(meta, '$.dataset.version') AS v FROM test_data WHERE dataset_key = ?"
            " UNION ALL SELECT json_extract(json_extract(snapshot, '$.meta'), '$.dataset.version') FROM test_data_history"
            " WHERE json_extract(snapshot, '$.dataset_key') = ?)", (dataset_key, dataset_key))
        version = int((prev or {}).get("v") or 0) + 1
        meta = {
            "dataset": {"key": dataset_key, "version": version, "name": f"{t['name']} data for #{tc_id}",
                        "purpose": f"{t['name']} of test case #{tc_id} {ctx['item']['title']}"},
            "fields": [f.model_dump() for f in design.fields], "gaps": design.gaps,
            "not_applicable": design.not_applicable, "preconditions": design.preconditions, "cleanup": design.cleanup,
            "seed": seed, "rule_version": RULE_VERSION, "agent": self.design.data.name, "agent_version": AGENT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": {"mode": cfg["mode"], "model": cfg["model"], "temperature": cfg["temperature"]},
            "requirement_revision": {"user_story_id": story_id, "rev": (ctx["story"] or {}).get("rev"),
                                     "criteria_hash": ac_hash},
            "generated_at": now_iso(),
        }
        kept = self._retire(dataset_key, tc_id)
        ts = now_iso()
        ids = []
        for n, row in enumerate(rows, start=1):
            row_meta = {**meta, "preview": preview(row.values, seed + n)}
            cur = self.db.execute(
                "INSERT INTO test_data (user_story_id, test_case_ref, tfs_test_case_id, name, kind, dataset, source_run_id,"
                " created_at, updated_at, dataset_key, row_key, version, scenario, intended_valid, violated_rule,"
                " expected_outcome, ac_ids, suite_id, testing_type, iteration, meta, user_edited)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (story_id, f"TC{tc_id}", tc_id, row.name, "valid" if row.intended_valid else "invalid", dumps(row.values),
                 None, ts, ts, dataset_key, f"{dataset_key}-R{n:02d}", version, row.scenario, int(row.intended_valid),
                 row.violated_rule, row.expected_outcome, dumps(row.ac_ids or ctx["ac_ids"]), ctx["suite_id"], t["key"],
                 ctx["iteration"], dumps(row_meta)))
            ids.append(cur.lastrowid)
        scenarios = sorted({r.scenario for r in rows})
        return {"test_case_id": tc_id, "title": ctx["item"]["title"], "user_story_id": story_id,
                "testing_type": t["name"], "dataset_key": dataset_key, "version": version, "rows": ids,
                "scenarios": scenarios, "issues": issues, "gaps": design.gaps, "not_applicable": design.not_applicable,
                "kept_user_edited": kept, "seed": seed}

    def _retire(self, dataset_key: str, tc_id: int) -> int:
        """Archive unedited rows of the previous version; keep user-edited rows and flag them for review."""
        kept = 0
        for r in self.db.query("SELECT * FROM test_data WHERE tfs_test_case_id = ? AND (dataset_key = ? OR "
                               "dataset_key IS NULL)", (tc_id, dataset_key)):
            if r.get("dataset_key") is None:
                continue  # rows from AI Test Design runs stay as they are
            if r["user_edited"]:
                kept += 1
                self.db.execute("UPDATE test_data SET review_reason = ? WHERE id = ?",
                                (f"Dataset regenerated on {now_iso()[:10]}; this row was edited by a user and was kept.",
                                 r["id"]))
                continue
            self._history(r, "regenerated")
            self.db.execute("DELETE FROM test_data WHERE id = ?", (r["id"],))
        return kept

    def _history(self, row: dict[str, Any], change: str) -> None:
        self.db.execute("INSERT INTO test_data_history (test_data_id, row_key, version, change, snapshot, at)"
                        " VALUES (?,?,?,?,?,?)", (row["id"], row.get("row_key"), row.get("version") or 1, change,
                                                  dumps(row), now_iso()))

    # ---- list / edit / delete ----------------------------------------------------------------------------

    @staticmethod
    def _shape(r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        r["dataset"] = loads(r["dataset"]) if isinstance(r["dataset"], str) else r["dataset"]
        r["ac_ids"] = loads(r["ac_ids"]) if r.get("ac_ids") else []
        r["meta"] = loads(r["meta"]) if r.get("meta") else {}
        r["intended_valid"] = None if r.get("intended_valid") is None else bool(r["intended_valid"])
        r["user_edited"] = bool(r.get("user_edited"))
        return r

    def list(self, *, iteration: str | None = None, user_story_id: str | None = None,
             test_case_ids: list[int] | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        clauses, params = [], []
        if iteration:
            clauses.append("(iteration = ? OR iteration LIKE ?)")
            params += [iteration, iteration + "\\%"]
        if user_story_id:
            clauses.append("user_story_id = ?")
            params.append(str(user_story_id))
        if test_case_ids:
            clauses.append(f"tfs_test_case_id IN ({','.join('?' * len(test_case_ids))})")
            params += [int(x) for x in test_case_ids]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(f"SELECT * FROM test_data {where} ORDER BY tfs_test_case_id DESC, row_key, id LIMIT ?",
                             [*params, limit])
        return [self._shape(r) for r in rows]

    def update(self, data_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM test_data WHERE id = ?", (data_id,))
        if not row:
            raise QAError("TEST_DATA_NOT_FOUND", f"Test data {data_id} does not exist.", status=404)
        values = changes.get("values")
        intended_valid = changes.get("intended_valid", None if row.get("intended_valid") is None else bool(row["intended_valid"]))
        if values is not None:
            if not isinstance(values, dict):
                raise QAError("TEST_DATA_INVALID", "Values must be an object of field: value.")
            for k, v in values.items():
                if is_secret_field(k) and isinstance(v, str) and v.strip() and not SECRET_REF.match(v) \
                        and intended_valid is not False:
                    raise QAError("SECRET_IN_TEST_DATA", f"'{k}' looks like a secret. Store a reference such as "
                                  f"{{{{SECRET:{k.lower()}}}}} or {{{{CRED:role.password}}}} instead of the value.")
        self._history(row, "edited")
        sets = {"updated_at": now_iso(), "user_edited": 1, "version": (row.get("version") or 1) + 1, "review_reason": None}
        if values is not None:
            sets["dataset"] = dumps(values)
        for k in ("name", "scenario", "violated_rule", "expected_outcome"):
            if changes.get(k) is not None:
                sets[k] = changes[k]
        if changes.get("intended_valid") is not None:
            sets["intended_valid"] = int(bool(changes["intended_valid"]))
            sets["kind"] = "valid" if changes["intended_valid"] else "invalid"
        if changes.get("ac_ids") is not None:
            sets["ac_ids"] = dumps(changes["ac_ids"])
        self.db.execute(f"UPDATE test_data SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        [*sets.values(), data_id])
        return self._shape(self.db.query_one("SELECT * FROM test_data WHERE id = ?", (data_id,)) or row)

    def delete(self, ids: list[int]) -> int:
        n = 0
        for i in ids:
            row = self.db.query_one("SELECT * FROM test_data WHERE id = ?", (int(i),))
            if row:
                self._history(row, "deleted")
                self.db.execute("DELETE FROM test_data WHERE id = ?", (int(i),))
                n += 1
        return n

    def history(self, data_id: int) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM test_data_history WHERE test_data_id = ? ORDER BY id DESC", (data_id,))
        for r in rows:
            r["snapshot"] = loads(r["snapshot"])
        return rows

    # ---- binding check -----------------------------------------------------------------------------------

    def binding(self, tc_id: int) -> dict[str, Any]:
        """Check that the test case's @parameters and {{DATA:x}} references have a value in every row."""
        item = self.tfs.detail(tc_id)
        steps = parse_tfs_steps(item.get("steps"))
        text = " ".join(f"{s['action']} {s['expected']}" for s in steps)
        params = sorted(set(re.findall(r"@(\w+)", text)) | set(re.findall(r"\{\{\s*DATA:(\w+)\s*\}\}", text)))
        problems = []
        rows = self.list(test_case_ids=[tc_id])
        for r in rows:
            vals = r["dataset"] if isinstance(r["dataset"], dict) else {}
            missing = [p for p in params if p not in vals]
            unresolved = [k for k, v in vals.items() if isinstance(v, str) and
                          any(ref not in vals for ref in re.findall(r"\{\{\s*DATA:(\w+)\s*\}\}", v))]
            if missing or unresolved:
                problems.append({"row": r.get("row_key") or r["id"], "missing_fields": missing,
                                 "unresolved_references": unresolved})
        return {"test_case_id": tc_id, "parameters": params, "rows": len(rows), "problems": problems,
                "ok": not problems}

    # ---- write to TFS ------------------------------------------------------------------------------------

    def write_to_tfs(self, tc_id: int) -> dict[str, Any]:
        """Write the rows into the test case's test data tab when the type has one (a custom 'Test Data' field, or
        the Parameter values grid), otherwise into its Summary."""
        rows = self.list(test_case_ids=[tc_id])
        if not rows:
            raise QAError("TEST_DATA_EMPTY", f"Test Case #{tc_id} has no saved test data to write.", status=409)
        datasets = [{"name": r.get("row_key") or r["name"], "kind": r.get("scenario") or r.get("kind"),
                     "values": r["dataset"] if isinstance(r["dataset"], dict) else {}} for r in reversed(rows)]
        item = self.tfs.detail(tc_id)
        fields = self.tfs._with_client(lambda c: c.work_item_type_fields(item["type"]))
        custom = next((f["referenceName"] for f in fields
                       if re.search(r"test\s*_?data", f"{f.get('name', '')} {f['referenceName']}", re.I)), None)
        refs = {f["referenceName"] for f in fields}
        from .service import _TEST_DATA_HEADING, parameters_xml, test_data_html
        if custom:
            values = {custom: test_data_html(datasets)}
            target = f"Test Data field ({custom})"
        elif {"Microsoft.VSTS.TCM.Parameters", "Microsoft.VSTS.TCM.LocalDataSource"} <= refs and parameters_xml(datasets):
            params = parameters_xml(datasets)
            values = {"Microsoft.VSTS.TCM.Parameters": params[0], "Microsoft.VSTS.TCM.LocalDataSource": params[1]}
            target = "Parameter values (test data tab)"
        else:
            current = _TEST_DATA_HEADING.sub("", item.get("description") or "")
            values = {"description": current + test_data_html(datasets)}
            target = "Summary"
        with self.design.data.activity(f"Writing test data to Test Case #{tc_id} in TFS", test_case_id=str(tc_id)) as act:
            updated = self.tfs.update(tc_id, values)
            act.complete(f"Test data written to Test Case #{tc_id} ({target}): {len(datasets)} rows")
        return {"test_case_id": tc_id, "rows": len(datasets), "target": target, "skipped_fields": updated["skipped_fields"]}

    # ---- import / export -----------------------------------------------------------------------------------

    def export(self, rows: list[dict[str, Any]], fmt: str) -> tuple[str, str]:
        if fmt == "json":
            items = [{k: r.get(k) for k in ("row_key", "dataset_key", "version", "user_story_id", "tfs_test_case_id",
                                            "testing_type", "name", "scenario", "intended_valid", "violated_rule",
                                            "expected_outcome", "ac_ids")} | {"values": r["dataset"]} for r in rows]
            return json.dumps({"format": "qa-test-data/1", "rows": items}, ensure_ascii=False, indent=2), "application/json"
        if fmt != "csv":
            raise QAError("FORMAT_UNSUPPORTED", "Export supports csv and json.")
        keys = list(dict.fromkeys(k for r in rows for k in (r["dataset"] if isinstance(r["dataset"], dict) else {})))
        head = ["row_key", "user_story_id", "tfs_test_case_id", "testing_type", "name", "scenario", "intended_valid",
                "violated_rule", "expected_outcome", "ac_ids"]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(head + [f"value.{k}" for k in keys])
        for r in rows:
            vals = r["dataset"] if isinstance(r["dataset"], dict) else {}
            w.writerow([_csv_cell(r.get(h) if h != "ac_ids" else ";".join(r.get("ac_ids") or [])) for h in head]
                       + [_csv_cell(vals.get(k)) if k in vals else "" for k in keys])
        return buf.getvalue(), "text/csv"

    def import_rows(self, text: str, fmt: str, test_case_id: int | None = None) -> dict[str, Any]:
        if fmt == "json":
            data = json.loads(text)
            items = data.get("rows", data) if isinstance(data, dict) else data
        elif fmt == "csv":
            items = []
            for rec in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
                values = {k[6:]: _from_csv_cell(v) for k, v in rec.items() if k and k.startswith("value.") and v != ""}
                item = {k: _from_csv_cell(v) for k, v in rec.items() if k and not k.startswith("value.")}
                item["ac_ids"] = [a for a in (item.get("ac_ids") or "").split(";") if a]
                iv = (item.get("intended_valid") or "").lower()
                item["intended_valid"] = None if iv in ("", NULL_TOKEN) else iv == "true"
                item["values"] = values
                items.append(item)
        else:
            raise QAError("FORMAT_UNSUPPORTED", "Import supports csv and json.")
        ts = now_iso()
        added = 0
        for it in items:
            tc = int(test_case_id or it.get("tfs_test_case_id") or 0)
            if not tc:
                raise QAError("TEST_CASE_REQUIRED", "Every imported row needs tfs_test_case_id, or choose a test case.")
            values = it.get("values") or {}
            for k, v in values.items():
                if is_secret_field(k) and isinstance(v, str) and v.strip() and not SECRET_REF.match(v) \
                        and it.get("intended_valid") is not False:
                    raise QAError("SECRET_IN_TEST_DATA", f"Imported '{k}' holds a plain-text secret; use a reference.")
            self.db.execute(
                "INSERT INTO test_data (user_story_id, test_case_ref, tfs_test_case_id, name, kind, dataset, created_at,"
                " updated_at, dataset_key, row_key, version, scenario, intended_valid, violated_rule, expected_outcome,"
                " ac_ids, testing_type, meta, user_edited) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (it.get("user_story_id"), f"TC{tc}", tc, it.get("name") or it.get("row_key") or "Imported row",
                 None if it.get("intended_valid") is None else ("valid" if it["intended_valid"] else "invalid"),
                 dumps(values), ts, ts, it.get("dataset_key") or f"DS-{tc}-imported", it.get("row_key"),
                 int(it.get("version") or 1), it.get("scenario"),
                 None if it.get("intended_valid") is None else int(bool(it["intended_valid"])), it.get("violated_rule"),
                 it.get("expected_outcome"), dumps(it.get("ac_ids") or []), it.get("testing_type"),
                 dumps({"imported_at": ts})))
            added += 1
        return {"imported": added}

    # ---- coverage ------------------------------------------------------------------------------------------

    def coverage(self, user_story_id: int) -> dict[str, Any]:
        """Criterion -> linked test cases -> data rows; data gaps are reported separately from test case gaps."""
        story = self.cases.read_story(user_story_id)
        trace = self.cases._trace(user_story_id)
        rows = self.list(user_story_id=str(user_story_id))
        out = []
        for c in story["criteria"]:
            cases = sorted({r["tfs_test_case_id"] for r in trace if r["ac_id"] == c["id"]})
            per_case = []
            for tc in cases:
                tc_rows = [r for r in rows if r["tfs_test_case_id"] == tc and (not r["ac_ids"] or c["id"] in r["ac_ids"])]
                per_case.append({"test_case_id": tc, "rows": [r.get("row_key") or r["id"] for r in tc_rows],
                                 "scenarios": sorted({r.get("scenario") or r.get("kind") or "" for r in tc_rows}),
                                 "valid": sum(1 for r in tc_rows if r.get("intended_valid") is not False),
                                 "invalid": sum(1 for r in tc_rows if r.get("intended_valid") is False)})
            if not cases:
                status = "Missing test case coverage"
            elif any(not p["rows"] for p in per_case):
                status = "Missing data coverage"
            else:
                status = "Data covered"
            out.append({"ac_id": c["id"], "text": c["text"], "test_cases": per_case, "status": status})
        return {"story": story["story"], "matrix": out}


def secret_free(rows: list[dict[str, Any]]) -> bool:
    """True when no valid row holds a plain-text secret (used by tests and the export check)."""
    for r in rows:
        for k, v in (r["dataset"] if isinstance(r["dataset"], dict) else {}).items():
            if is_secret_field(k) and isinstance(v, str) and v.strip() and not SECRET_REF.match(v) \
                    and r.get("intended_valid") is not False:
                return False
    return True

