"""23.3.25: saved test data sets for performance scripts.

Rows are kept in the database as text. Cells of secret columns are not: they are kept in the Windows Credential
Manager (in pieces small enough for it) and only read back to write the CSV file of a run, which is deleted when
JMeter ends. Views, exports, logs and run results never contain them."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Callable

from pydantic import BaseModel

from ..errors import QAError
from ..storage.db import dumps, loads
from ..clock import now_iso
from . import datasets as D

SECRET_NS = "perfdata"
CHUNK = 1000  # characters per Credential Manager entry (its limit is 2560 bytes of UTF-16)
EDIT_LIMIT = 1000  # rows edited in the table; larger data sets are changed by CSV import


def new_id() -> str:
    return "DS-" + uuid.uuid4().hex[:8].upper()


class AIRows(BaseModel):
    rows: list[list[str]]


class DatasetStore:
    def __init__(self, db: Any, secrets: Callable[[], Any], ai: Any = None) -> None:
        self.db = db
        self._secrets = secrets  # the SecretStore is set on the service after it is built
        self.ai = ai

    # ---- storage ---------------------------------------------------------------------------------------------

    def _row(self, ds_id: str) -> dict[str, Any]:
        r = self.db.query_one("SELECT * FROM perf_datasets WHERE id = ?", (ds_id,))
        if not r:
            raise QAError("DATASET_NOT_FOUND", f"Test data set {ds_id} does not exist.", status=404)
        return r

    def _store(self) -> Any:
        st = self._secrets()
        if st is None or not st.available:
            raise QAError("SECRET_STORE_UNAVAILABLE", "This data set has secret columns, but the Windows Credential "
                          "Manager is not available to keep them.", status=503)
        return st

    def _read_secrets(self, ds_id: str, chunks: int) -> dict[str, dict[str, str]]:
        if not chunks:
            return {}
        st = self._secrets()
        if st is None:
            return {}
        parts = []
        for i in range(chunks):
            v = st.get(SECRET_NS, f"{ds_id}.{i}")
            if v is None:
                return {}  # removed outside the application: every secret cell reads as "not saved"
            parts.append(v)
        try:
            return json.loads("".join(parts))
        except ValueError:
            return {}

    def _write_secrets(self, ds_id: str, data: dict[str, dict[str, str]], old_chunks: int) -> int:
        data = {c: {r: v for r, v in cells.items() if v} for c, cells in data.items()}
        data = {c: cells for c, cells in data.items() if cells}
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if data else ""
        pieces = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
        if pieces:
            st = self._store()
            for i, p in enumerate(pieces):
                st.set(SECRET_NS, f"{ds_id}.{i}", p)
        st = self._secrets()
        for i in range(len(pieces), old_chunks):
            try:
                st.delete(SECRET_NS, f"{ds_id}.{i}")
            except Exception:
                pass
        return len(pieces)

    def _save(self, ds_id: str | None, *, name: str, description: str, columns: list[dict[str, Any]],
              rows: list[dict[str, Any]], secrets: dict[str, dict[str, str]], source: str,
              base_version: int | None = None) -> dict[str, Any]:
        ts = now_iso()
        if ds_id:
            cur = self._row(ds_id)
            if base_version is not None and base_version != cur["version"]:
                raise QAError("DATASET_CONFLICT", f"The data set was changed elsewhere (now version {cur['version']}). "
                              "Reload it before saving, so no change is lost.", status=409)
            chunks = self._write_secrets(ds_id, secrets, cur["secret_chunks"] or 0)
            self.db.execute("UPDATE perf_datasets SET name = ?, description = ?, columns = ?, rows = ?, "
                            "secret_chunks = ?, version = version + 1, source = ?, updated_at = ? WHERE id = ?",
                            (name, description, dumps(columns), dumps(rows), chunks, source or cur["source"], ts,
                             ds_id))
        else:
            ds_id = new_id()
            chunks = self._write_secrets(ds_id, secrets, 0)
            self.db.execute("INSERT INTO perf_datasets (id, name, description, columns, rows, secret_chunks, version,"
                            " source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                            (ds_id, name, description, dumps(columns), dumps(rows), chunks, source, ts, ts))
        return self.view(ds_id)

    def _load(self, ds_id: str, *, reveal: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]],
                                                                   list[dict[str, Any]], dict[str, dict[str, str]]]:
        r = self._row(ds_id)
        columns, rows = loads(r["columns"]), loads(r["rows"])
        secrets = self._read_secrets(ds_id, r["secret_chunks"] or 0) if reveal or r["secret_chunks"] else {}
        return r, columns, rows, secrets

    @staticmethod
    def _matrix(columns: list[dict[str, Any]], rows: list[dict[str, Any]], secrets: dict[str, dict[str, str]],
                *, reveal: bool) -> list[list[str | None]]:
        """Rows as lists of values. A secret cell is its value when revealed, else None."""
        out = []
        for row in rows:
            vals: list[str | None] = []
            for i, c in enumerate(columns):
                if c["type"] == "secret":
                    v = secrets.get(c["name"], {}).get(str(row["id"]))
                    vals.append(v if reveal else None)
                    if reveal and v is None:
                        vals[-1] = None
                else:
                    vals.append(row["v"][i] if i < len(row["v"]) else "")
            out.append(vals)
        return out

    # ---- views ---------------------------------------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        used = self._usage()
        out = []
        for r in self.db.query("SELECT id, name, description, columns, rows, version, source, updated_at "
                               "FROM perf_datasets ORDER BY updated_at DESC"):
            cols = loads(r["columns"])
            out.append({"id": r["id"], "name": r["name"], "description": r["description"], "columns": cols,
                        "rows": len(loads(r["rows"])), "version": r["version"], "source": r["source"],
                        "updated_at": r["updated_at"], "used_by": used.get(r["id"], [])})
        return out

    def view(self, ds_id: str) -> dict[str, Any]:
        r, columns, rows, secrets = self._load(ds_id)
        problems = D.validate(columns, self._checkable(columns, rows, secrets))
        shown = rows[:EDIT_LIMIT]
        matrix = self._matrix(columns, shown, secrets, reveal=False)
        secret_set = {str(row["id"]): [c["name"] for c in columns if c["type"] == "secret"
                                       and secrets.get(c["name"], {}).get(str(row["id"]))] for row in shown}
        return {"id": r["id"], "name": r["name"], "description": r["description"], "columns": columns,
                "rows": [{"id": row["id"], "values": vals} for row, vals in zip(shown, matrix)],
                "rows_total": len(rows), "editable": len(rows) <= EDIT_LIMIT, "secret_set": secret_set,
                "version": r["version"], "source": r["source"], "created_at": r["created_at"],
                "updated_at": r["updated_at"], "problems": problems, "used_by": self._usage().get(ds_id, []),
                "types": D.TYPES, "rules": D.RULES}

    def _checkable(self, columns: list[dict[str, Any]], rows: list[dict[str, Any]],
                   secrets: dict[str, dict[str, str]]) -> list[list[str]]:
        """Rows for validation: secret cells are "x" when saved and None when not (their values are not checked)."""
        out = []
        for row in rows:
            vals = []
            for i, c in enumerate(columns):
                if c["type"] == "secret":
                    vals.append("x" if secrets.get(c["name"], {}).get(str(row["id"])) else None)
                else:
                    vals.append(row["v"][i] if i < len(row["v"]) else "")
            out.append(vals)
        return out

    def _usage(self) -> dict[str, list[dict[str, Any]]]:
        from . import script as jscript
        used: dict[str, list[dict[str, Any]]] = {}
        for p in self.db.query("SELECT id, name, plan FROM perf_plans"):
            text = loads(p["plan"]).get("script") or ""
            if "datasets/DS-" not in text:
                continue
            try:
                ids = {b["dataset_id"] for b in jscript.data_sets(jscript.parse(text)) if b["dataset_id"]}
            except QAError:
                continue
            for i in ids:
                used.setdefault(i, []).append({"id": p["id"], "name": p["name"]})
        return used

    # ---- changes -------------------------------------------------------------------------------------------------

    @staticmethod
    def _split(columns: list[dict[str, Any]], values: list[list[str | None]], ids: list[int | None],
               old_secrets: dict[str, dict[str, str]], next_id: int) -> tuple[list[dict[str, Any]],
                                                                             dict[str, dict[str, str]]]:
        """Rows for the database (secret cells blank) and the secret cells. A secret cell given as None keeps the
        value saved for that row."""
        rows, secrets = [], {c["name"]: {} for c in columns if c["type"] == "secret"}
        for vals, rid in zip(values, ids):
            if rid is None:
                rid, next_id = next_id, next_id + 1
            vals = list(vals) + [""] * (len(columns) - len(vals))
            plain = []
            for i, c in enumerate(columns):
                v = vals[i]
                if c["type"] == "secret":
                    if v is None:
                        v = old_secrets.get(c["name"], {}).get(str(rid), "")
                    if v:
                        secrets[c["name"]][str(rid)] = str(v)
                    plain.append("")
                else:
                    plain.append("" if v is None else str(v))
            rows.append({"id": rid, "v": plain})
        return rows, secrets

    def create(self, name: str, columns: list[dict[str, Any]], values: list[list[str | None]] | None = None, *,
               description: str = "", source: str = "manual") -> dict[str, Any]:
        cols = D.check_columns(columns)
        name = (name or "").strip()[:200]
        if not name:
            raise QAError("VALIDATION_FAILED", "Give the data set a name.", status=422)
        values = values or []
        self._limit(len(values))
        rows, secrets = self._split(cols, values, [None] * len(values), {}, 1)
        return self._save(None, name=name, description=description[:1000], columns=cols, rows=rows,
                          secrets=secrets, source=source)

    @staticmethod
    def _limit(n: int) -> None:
        if n > D.MAX_ROWS:
            raise QAError("DATASET_TOO_LARGE", f"At most {D.MAX_ROWS} rows are supported.", status=422)

    def update(self, ds_id: str, *, name: str, description: str, columns: list[dict[str, Any]],
               rows: list[dict[str, Any]], base_version: int | None) -> dict[str, Any]:
        """Save the edited table. rows: [{id (None for a new row), values}]. Columns are matched by their old name
        (`from`), so renaming a column keeps its values, also secret ones."""
        r, old_cols, old_rows, old_secrets = self._load(ds_id, reveal=True)
        if len(old_rows) > EDIT_LIMIT:
            raise QAError("DATASET_TOO_LARGE_TO_EDIT", f"This data set has {len(old_rows)} rows. Change it by "
                          "importing a CSV file.", status=409)
        cols = D.check_columns(columns)
        renamed = {str(c.get("from") or c.get("name")): c2["name"] for c, c2 in zip(columns, cols)}
        old_types = {c["name"]: c["type"] for c in old_cols}
        secrets_by_new: dict[str, dict[str, str]] = {}
        for old, new in renamed.items():
            if old_types.get(old) == "secret":
                secrets_by_new[new] = old_secrets.get(old, {})
        # a column that becomes secret keeps its old plain values as secrets until they are replaced
        old_plain = {c["name"]: {str(row["id"]): (row["v"][i] if i < len(row["v"]) else "") for row in old_rows}
                     for i, c in enumerate(old_cols) if c["type"] != "secret"}
        for old, new in renamed.items():
            if new in [c["name"] for c in cols if c["type"] == "secret"] and old in old_plain:
                secrets_by_new.setdefault(new, old_plain[old])
        self._limit(len(rows))
        next_id = max([row["id"] for row in old_rows] + [0]) + 1
        known = {row["id"] for row in old_rows}
        ids = [x.get("id") if x.get("id") in known else None for x in rows]
        rows_db, secrets = self._split(cols, [x.get("values") or [] for x in rows], ids, secrets_by_new, next_id)
        name = (name or "").strip()[:200] or r["name"]
        return self._save(ds_id, name=name, description=(description or "")[:1000], columns=cols, rows=rows_db,
                          secrets=secrets, source=r["source"], base_version=base_version)

    def import_csv(self, data: bytes | str, *, name: str = "", ds_id: str | None = None, mode: str = "create",
                   filename: str = "", sheet: str | None = None) -> dict[str, Any]:
        """A CSV file with a header line: a new data set, or replacing or adding to the rows of one."""
        if isinstance(data, bytes) and data[:4] == b"PK\x03\x04":  # final patch (defect 10): an Excel workbook
            data = self.xlsx_to_csv(data, sheet)
            filename = filename or "workbook.xlsx"
        parsed = D.parse_csv(data)
        self._limit(len(parsed["rows"]))
        source = f"CSV {filename}".strip()
        if mode == "create" or not ds_id:
            out = self.create(name or filename.rsplit(".", 1)[0] or "Imported data", parsed["columns"],
                              parsed["rows"], source=source)
        else:
            r, cols, rows, secrets = self._load(ds_id, reveal=True)
            if mode == "append":
                names = [c["name"] for c in parsed["columns"]]
                missing = [c["name"] for c in cols if c["name"] not in names]
                extra = [n for n in names if n not in [c["name"] for c in cols]]
                if missing or extra:
                    raise QAError("DATASET_COLUMNS_DIFFER", "The file's columns differ from the data set's"
                                  + (f"; missing: {', '.join(missing)}" if missing else "")
                                  + (f"; not in the data set: {', '.join(extra)}" if extra else "") + ".",
                                  status=422)
                order = [names.index(c["name"]) for c in cols]
                new_vals = [[row[i] for i in order] for row in parsed["rows"]]
                self._limit(len(rows) + len(new_vals))
                keep = self._matrix(cols, rows, secrets, reveal=True)
                all_vals = keep + new_vals
                ids = [row["id"] for row in rows] + [None] * len(new_vals)
                next_id = max([row["id"] for row in rows] + [0]) + 1
                rows_db, sec = self._split(cols, all_vals, ids, {}, next_id)
                out = self._save(ds_id, name=r["name"], description=r["description"] or "", columns=cols,
                                 rows=rows_db, secrets=sec, source=source)
            else:  # replace: the file's columns; a column known before keeps its type
                types = {c["name"]: c["type"] for c in cols}
                new_cols = [{"name": c["name"], "type": types.get(c["name"], c["type"])} for c in parsed["columns"]]
                rows_db, sec = self._split(new_cols, parsed["rows"], [None] * len(parsed["rows"]), {}, 1)
                out = self._save(ds_id, name=r["name"], description=r["description"] or "", columns=new_cols,
                                 rows=rows_db, secrets=sec, source=source)
        out["import"] = {k: parsed[k] for k in ("encoding", "delimiter", "notes")} | {"rows": len(parsed["rows"])}
        return out

    @staticmethod
    def xlsx_to_csv(data: bytes, sheet: str | None = None) -> str:
        """One sheet of an .xlsx (the first, or the one named) as CSV text with its first row as the header.
        Cell text is kept as shown, so leading zeros stay."""
        from ..automation.files import read_xlsx

        sheets = read_xlsx(data)
        if not sheets:
            raise QAError("DATASET_INVALID", "The workbook has no sheets.", status=422)
        if sheet and sheet not in sheets:
            raise QAError("DATASET_INVALID", f"The workbook has no sheet named {sheet}. Sheets: {', '.join(sheets)}.",
                          status=422)
        rows = sheets[sheet] if sheet else next(iter(sheets.values()))
        rows = [r for r in rows if any((c or "").strip() for c in r)]
        if not rows:
            raise QAError("DATASET_INVALID", "The sheet is empty.", status=422)
        width = max(len(r) for r in rows)
        return D.to_csv([str(c) for c in rows[0]] + [""] * (width - len(rows[0])),
                        [[str(c) for c in r] + [""] * (width - len(r)) for r in rows[1:]])

    def export_xlsx(self, ds_id: str) -> tuple[bytes, str]:
        """The data set as an .xlsx workbook (text cells only, no formulas). Secret columns are exported empty."""
        from ..comparison.reports import xlsx

        r, cols, rows, _ = self._load(ds_id)
        matrix = [["" if v is None else v for v in vals] for vals in self._matrix(cols, rows, {}, reveal=False)]
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in r["name"])[:60] or ds_id
        return xlsx([("Data", [c["name"] for c in cols], matrix)]), f"{safe}.xlsx"

    def export_csv(self, ds_id: str) -> tuple[str, str]:
        """CSV text and a file name. Secret columns are exported empty."""
        r, cols, rows, _ = self._load(ds_id)
        matrix = [["" if v is None else v for v in vals] for vals in self._matrix(cols, rows, {}, reveal=False)]
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in r["name"])[:60] or ds_id
        return D.to_csv([c["name"] for c in cols], matrix), f"{safe}.csv"

    def delete(self, ds_id: str) -> None:
        r = self._row(ds_id)
        used = self._usage().get(ds_id)
        if used:
            raise QAError("DATASET_IN_USE", "This data set is used by " + ", ".join(p["name"] for p in used)
                          + ". Remove it from those scripts first.", status=409)
        self._write_secrets(ds_id, {}, r["secret_chunks"] or 0)
        self.db.execute("DELETE FROM perf_datasets WHERE id = ?", (ds_id,))

    def add_rows(self, ds_id: str, values: list[list[str]], *, replace: bool = False, source: str = "") -> dict[str, Any]:
        r, cols, rows, secrets = self._load(ds_id, reveal=True)
        keep = [] if replace else self._matrix(cols, rows, secrets, reveal=True)
        self._limit(len(keep) + len(values))
        ids = ([] if replace else [row["id"] for row in rows]) + [None] * len(values)
        next_id = 1 if replace else max([row["id"] for row in rows] + [0]) + 1
        rows_db, sec = self._split(cols, keep + values, ids, {}, next_id)
        return self._save(ds_id, name=r["name"], description=r["description"] or "", columns=cols, rows=rows_db,
                          secrets=sec, source=source or r["source"])

    def generate(self, ds_id: str, rules: dict[str, dict[str, Any]], count: int, *, replace: bool = False,
                 seed: int | None = None) -> dict[str, Any]:
        r, cols, rows, _ = self._load(ds_id)
        start = 1 if replace else len(rows) + 1
        values = D.generate(cols, rules, count, start=start, seed=seed)
        return self.add_rows(ds_id, values, replace=replace, source=r["source"] or "generated")

    def generate_ai(self, ds_id: str, description: str, count: int) -> dict[str, Any]:
        """Rows written by the configured AI model from the column names, types and a description. Secret columns
        are left empty; every row is checked against the column types before it is kept."""
        if self.ai is None:
            raise QAError("AI_NOT_CONFIGURED", "No AI model is set up.", status=503)
        if count < 1 or count > 50:
            raise QAError("VALIDATION_FAILED", "Ask the AI for 1 to 50 rows at a time.", status=422)
        r, cols, rows, _ = self._load(ds_id)
        plain = [c for c in cols if c["type"] != "secret"]
        if not plain:
            raise QAError("VALIDATION_FAILED", "All columns are secret; enter their values yourself.", status=422)
        spec = [{"name": c["name"], "type": c["type"]} for c in plain]
        sample = [[row["v"][cols.index(c)] for c in plain] for row in rows[:5]]
        result = self.ai.complete_json(
            "You write synthetic test data for a performance test. Write realistic but invented values only; never "
            "real people's data, real email domains (use example.test) or real secrets. Keep each value on one "
            "line. Integers are digits only; dates are YYYY-MM-DD; booleans are true or false.",
            json.dumps({"columns": spec, "rows_wanted": count, "description": description[:1000],
                        "existing_examples": sample}, ensure_ascii=False), AIRows, max_tokens=4000)
        good, rejected = [], 0
        for row in result.rows[:count]:
            if len(row) != len(plain) or not all(D.type_ok(c["type"], str(v)) and "\n" not in str(v)
                                                 for c, v in zip(plain, row)):
                rejected += 1
                continue
            full = []
            it = iter(row)
            for c in cols:
                full.append("" if c["type"] == "secret" else str(next(it)))
            good.append(full)
        if not good:
            raise QAError("AI_ROWS_INVALID", "The AI's rows did not match the column types, so none were added.",
                          status=502)
        out = self.add_rows(ds_id, good, source=r["source"] or "AI generated")
        out["ai"] = {"added": len(good), "rejected": rejected, "model": self.ai.config().get("model")}
        return out

    # ---- runs ----------------------------------------------------------------------------------------------------

    def materialize(self, ds_id: str, variables: list[str]) -> dict[str, Any]:
        """The CSV text a run reads, with the columns in the order the script names them. Secret cells are filled
        in, so the caller deletes the file after the run when `has_secrets` is true."""
        r, cols, rows, secrets = self._load(ds_id, reveal=True)
        names = [c["name"] for c in cols]
        missing = [v for v in variables if v not in names]
        if missing:
            raise QAError("DATASET_COLUMN_MISSING", f"Data set {r['name']} has no column {', '.join(missing)}.",
                          status=409)
        matrix = self._matrix(cols, rows, secrets, reveal=True)
        idx = [names.index(v) for v in variables]
        out_rows = [["" if vals[i] is None else vals[i] for i in idx] for vals in matrix]
        text = D.to_csv(variables, out_rows)
        used_secret = [v for v in variables if cols[names.index(v)]["type"] == "secret"]
        return {"text": text, "has_secrets": bool(used_secret), "secret_columns": used_secret,
                "info": {"id": ds_id, "name": r["name"], "version": r["version"], "rows": len(out_rows),
                         "columns": variables,
                         "sha256": None if used_secret else hashlib.sha256(text.encode("utf-8")).hexdigest()}}

    def check(self, ds_id: str, variables: list[str]) -> dict[str, Any]:
        """For the pre-run checks: missing columns, value problems, rows."""
        try:
            r, cols, rows, secrets = self._load(ds_id)
        except QAError:
            return {"exists": False}
        names = [c["name"] for c in cols]
        missing = [v for v in variables if v not in names]
        used = [c for c in cols if c["name"] in variables]
        problems = D.validate(used, [[vals[names.index(c["name"])] for c in used]
                                     for vals in self._checkable(cols, rows, secrets)])
        return {"exists": True, "name": r["name"], "rows": len(rows), "missing": missing, "problems": problems,
                "secret_columns": [c["name"] for c in used if c["type"] == "secret"]}
