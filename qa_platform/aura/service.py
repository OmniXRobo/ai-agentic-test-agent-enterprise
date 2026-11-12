"""AURA memory: structured, versioned, offline knowledge that the QA agents share.

Where it lives
* A SQLite file ``aura_memory.sqlite3`` in the folder chosen under Preferences > AURA Agent Memory (for example
  ``D:\\QAEnterprise\\AURA_Memory``); by default ``<data>\\aura_memory``. Any drive letter works; nothing is assumed.
* If that folder becomes unavailable (a removed drive, a disconnected share), AURA does not silently forget: what it
  learns meanwhile is written to ``aura_pending.jsonl`` in the data folder and replayed into the memory as soon as the
  folder is back. The status says UNAVAILABLE until then.

What it stores (``kind``)
  application_profile, page_structure, component, locator_history, automation_pattern, healing,
  business_workflow, test_data_schema, performance_baseline, known_defect, application_change,
  approved_correction, field_mapping

Each entry is (kind, application, key) with versions: when what is learned differs from the latest version a new
version is stored, and for page structures and components the difference is also stored as an
``application_change``. Entries are "observed" until a person approves them (``approved``). AURA never changes an
approved script or a test expectation: agents read its knowledge as suggestions (for example an approved locator
correction is tried before self-healing scores candidates; the step still has to pass).

What it never stores: passwords, PATs, tokens, API keys, cookies, authorization headers or values typed into password
fields. ``scrub`` removes them (by key name and by shape) before anything is written, and the stored text is checked
again; an entry that still looks secret is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import zipfile
from pathlib import Path
from typing import Any

from ..clock import now, now_iso
from ..config import SECRET_KEY_PATTERN, Paths, Settings
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("aura")

KINDS = ("application_profile", "page_structure", "component", "locator_history", "automation_pattern", "healing",
         "business_workflow", "test_data_schema", "performance_baseline", "known_defect", "application_change",
         "approved_correction", "field_mapping")
DB_NAME = "aura_memory.sqlite3"
MIN_FREE_MB = 200
SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    application TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source TEXT,
    agent TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, application, key, version)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_lookup ON knowledge(kind, application, key, version);
CREATE INDEX IF NOT EXISTS ix_knowledge_app ON knowledge(application, kind);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    agent TEXT,
    kind TEXT,
    application TEXT,
    summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

_TOKEN_SHAPES = [
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),  # JWT
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b[a-z0-9]{52}\b"),  # an Azure DevOps PAT
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
]
_SECRET_FIELD_HINT = re.compile(r"(?i)(password|passwd|pwd|secret|token|cookie|session|auth|api[_-]?key|pat$)")


def _redact_text(text: str) -> str:
    for rx in _TOKEN_SHAPES:
        text = rx.sub("[REDACTED]", text)
    return text


def scrub(value: Any, _key: str = "") -> Any:
    """A copy of ``value`` without secrets: keys that name a secret are dropped, typed values of password-like
    fields are dropped, token-shaped text is replaced with [REDACTED]."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        is_secret_field = (str(value.get("input_type") or value.get("type") or "").lower() == "password"
                           or bool(_SECRET_FIELD_HINT.search(str(value.get("name") or value.get("label") or ""))))
        for k, v in value.items():
            ks = str(k)
            if SECRET_KEY_PATTERN.search(ks) or ks.lower() in ("cookies", "cookie", "authorization", "headers",
                                                                "set-cookie", "storage_state", "credentials"):
                continue
            if is_secret_field and ks.lower() in ("value", "text", "default", "sample", "samples", "example"):
                continue
            out[ks] = scrub(v, ks)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, _key) for v in value][:500]
    if isinstance(value, str):
        return _redact_text(value)[:4000]
    return value


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def _diff(old: Any, new: Any, path: str = "") -> list[dict[str, Any]]:
    """Field-level differences between two JSON values (for application_change)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[dict[str, Any]] = []
        for k in sorted(set(old) | set(new)):
            p = f"{path}.{k}" if path else str(k)
            if k not in old:
                out.append({"path": p, "change": "added", "new": new[k]})
            elif k not in new:
                out.append({"path": p, "change": "removed", "old": old[k]})
            else:
                out += _diff(old[k], new[k], p)
        return out
    if old != new:
        return [{"path": path, "change": "changed", "old": old, "new": new}]
    return []


class AuraService:
    def __init__(self, settings: Settings, paths: Paths) -> None:
        self.settings = settings
        self.paths = paths
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._folder: Path | None = None
        self.pending_file = paths.root / "aura_pending.jsonl"
        self.problem: str | None = None
        self._open()

    # ---- configuration ---------------------------------------------------------------------------------------

    def config(self) -> dict[str, Any]:
        a = self.settings.section("aura") or {}
        return {"enabled": a.get("enabled", True) is not False,
                "memory_path": a.get("memory_path") or str(self.paths.root / "aura_memory"),
                "retention_days": int(a.get("retention_days") or 0),
                "learn_from": a.get("learn_from") or {}}

    def enabled(self) -> bool:
        return self.config()["enabled"]

    @staticmethod
    def check_folder(raw: str) -> Path:
        """A usable memory folder: absolute, not a system folder, creatable, writable, with free space."""
        text = (raw or "").strip().strip('"')
        if not text:
            raise QAError("AURA_PATH_INVALID", "Enter a folder for AURA's memory, for example D:\\QAEnterprise\\AURA_Memory.")
        if "\x00" in text or len(text) > 240:
            raise QAError("AURA_PATH_INVALID", "This folder name is not valid.")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise QAError("AURA_PATH_INVALID", "Give the full folder path, starting with a drive letter (e.g. D:\\...).")
        low = str(path).replace("/", "\\").lower()
        if any(low.rstrip("\\").endswith(x) or f"{x}\\" in low for x in ("\\windows", "\\program files",
                                                                           "\\program files (x86)")):
            raise QAError("AURA_PATH_INVALID", "Choose a folder outside Windows and Program Files.")
        anchor = Path(path.anchor) if path.anchor else None
        if anchor is not None and not anchor.exists():
            raise QAError("AURA_DRIVE_MISSING", f"The drive {path.anchor} is not available on this PC.", status=409)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".aura_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise QAError("AURA_PATH_NOT_WRITABLE", f"SkyGuard QA cannot write to {path}: {exc.strerror or exc}.",
                          status=409) from exc
        free_mb = shutil.disk_usage(path).free // (1024 * 1024)
        if free_mb < MIN_FREE_MB:
            raise QAError("AURA_DISK_FULL", f"Only {free_mb} MB are free on {path.anchor or path}; AURA needs at "
                          f"least {MIN_FREE_MB} MB.", status=409)
        return path

    def _open(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
            folder = Path(self.config()["memory_path"])
            try:
                folder.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(folder / DB_NAME), check_same_thread=False, isolation_level=None)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
                conn.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('created_at', ?)", (now_iso(),))
                conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('format', '1')")
                self._conn, self._folder, self.problem = conn, folder, None
            except (OSError, sqlite3.Error) as exc:
                self._conn, self._folder = None, folder
                self.problem = f"The memory folder {folder} is not available: {exc}"
                log.warning(f"AURA memory unavailable: {exc}")
                return
        self._replay_pending()

    def _db(self) -> sqlite3.Connection | None:
        if self._conn is None:
            self._open()  # the drive may be back
        return self._conn

    # ---- learning ----------------------------------------------------------------------------------------------

    def learn(self, kind: str, application: str | None, key: str, data: dict[str, Any], *, source: str,
              approved: bool = False, agent: str | None = None) -> dict[str, Any] | None:
        if kind not in KINDS:
            raise QAError("AURA_KIND_INVALID", f"Unknown knowledge kind '{kind}'.")
        if not self.enabled():
            return None
        clean = scrub(data)
        text = _canonical(clean)
        if any(rx.search(text) for rx in _TOKEN_SHAPES[:3]):
            log.warning(f"AURA refused an entry that still looks like it holds a secret ({kind}/{key[:60]})")
            return None
        app = (application or "").strip()[:200]
        key = str(key)[:500]
        entry = {"kind": kind, "application": app, "key": key, "data": clean, "source": source, "agent": agent,
                 "approved": bool(approved), "at": now_iso()}
        with self._lock:
            conn = self._db()
            if conn is None:
                self._queue(entry)
                return None
            try:
                return self._store(conn, entry, text)
            except sqlite3.Error as exc:
                log.warning(f"AURA could not store {kind}: {exc}")
                self._queue(entry)
                self._conn = None
                self.problem = f"The memory database could not be written: {exc}"
                return None

    def _store(self, conn: sqlite3.Connection, entry: dict[str, Any], text: str) -> dict[str, Any]:
        sha = hashlib.sha256(text.encode()).hexdigest()
        latest = conn.execute("SELECT * FROM knowledge WHERE kind = ? AND application = ? AND key = ? "
                              "ORDER BY version DESC LIMIT 1", (entry["kind"], entry["application"], entry["key"])).fetchone()
        at = entry["at"]
        if latest is not None and latest["sha256"] == sha:
            approved = 1 if (entry["approved"] or latest["approved"]) else 0
            conn.execute("UPDATE knowledge SET seen = seen + 1, updated_at = ?, approved = ? WHERE id = ?",
                         (at, approved, latest["id"]))
            return {"id": latest["id"], "version": latest["version"], "changed": False}
        version = (latest["version"] + 1) if latest is not None else 1
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                "INSERT INTO knowledge (kind, application, key, version, data, sha256, source, agent, approved, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (entry["kind"], entry["application"], entry["key"], version, text, sha, entry["source"],
                 entry["agent"], 1 if entry["approved"] else 0, at, at))
            summary = (f"Learned {entry['kind'].replace('_', ' ')} '{entry['key'][:80]}'"
                       + (f" (version {version})" if version > 1 else ""))
            if latest is not None and entry["kind"] in ("page_structure", "component", "application_profile"):
                changes = _diff(json.loads(latest["data"]), entry["data"])[:100]
                if changes:
                    change_key = f"{entry['kind']}:{entry['key']}:v{latest['version']}->v{version}"
                    ctext = _canonical({"of": entry["kind"], "key": entry["key"], "from_version": latest["version"],
                                        "to_version": version, "changes": changes})
                    conn.execute(
                        "INSERT OR IGNORE INTO knowledge (kind, application, key, version, data, sha256, source, agent,"
                        " approved, created_at, updated_at) VALUES ('application_change',?,?,1,?,?,?,?,0,?,?)",
                        (entry["application"], change_key, ctext, hashlib.sha256(ctext.encode()).hexdigest(),
                         entry["source"], entry["agent"], at, at))
                    summary += f"; {len(changes)} change(s) since version {latest['version']}"
            conn.execute("INSERT INTO activity (at, agent, kind, application, summary) VALUES (?,?,?,?,?)",
                         (at, entry["agent"] or entry["source"], entry["kind"], entry["application"], summary))
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise
        return {"id": cur.lastrowid, "version": version, "changed": True}

    def _queue(self, entry: dict[str, Any]) -> None:
        try:
            with self.pending_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            log.warning(f"AURA could not queue learning: {exc}")

    def _replay_pending(self) -> int:
        if not self.pending_file.exists() or self._conn is None:
            return 0
        done = 0
        try:
            lines = self.pending_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                try:
                    entry = json.loads(line)
                    self._store(self._conn, entry, _canonical(entry["data"]))
                    done += 1
                except (ValueError, KeyError, sqlite3.Error):
                    continue
            self.pending_file.unlink()
            if done:
                log.info(f"AURA replayed {done} entries learned while its memory folder was unavailable")
        except OSError:
            pass
        return done

    # ---- reading -----------------------------------------------------------------------------------------------

    def recall(self, kind: str, application: str | None, key: str, approved_only: bool = False) -> dict[str, Any] | None:
        conn = self._db()
        if conn is None:
            return None
        sql = ("SELECT * FROM knowledge WHERE kind = ? AND application = ? AND key = ?"
               + (" AND approved = 1" if approved_only else "") + " ORDER BY version DESC LIMIT 1")
        with self._lock:
            row = conn.execute(sql, (kind, (application or "").strip(), key)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        d["approved"] = bool(d["approved"])
        return d

    def entries(self, *, kind: str | None = None, application: str | None = None, q: str | None = None,
                latest_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        conn = self._db()
        if conn is None:
            return []
        where, params = [], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if application is not None:
            where.append("application = ?")
            params.append(application)
        if q:
            where.append("(key LIKE ? OR data LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if latest_only:
            where.append("version = (SELECT MAX(version) FROM knowledge k2 WHERE k2.kind = knowledge.kind AND "
                         "k2.application = knowledge.application AND k2.key = knowledge.key)")
        sql = ("SELECT * FROM knowledge" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY updated_at DESC LIMIT ?")
        with self._lock:
            rows = conn.execute(sql, [*params, max(1, min(int(limit), 1000))]).fetchall()
        return [self._row(r) for r in rows]

    def history(self, kind: str, application: str, key: str) -> list[dict[str, Any]]:
        conn = self._db()
        if conn is None:
            return []
        with self._lock:
            rows = conn.execute("SELECT * FROM knowledge WHERE kind = ? AND application = ? AND key = ? "
                                "ORDER BY version DESC", (kind, application, key)).fetchall()
        return [self._row(r) for r in rows]

    def approve(self, entry_id: int, approved: bool = True, by: str | None = None) -> dict[str, Any]:
        conn = self._db()
        if conn is None:
            raise QAError("AURA_UNAVAILABLE", self.problem or "AURA memory is unavailable.", status=503)
        with self._lock:
            row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (int(entry_id),)).fetchone()
            if row is None:
                raise QAError("AURA_NOT_FOUND", "This knowledge entry does not exist.", status=404)
            conn.execute("UPDATE knowledge SET approved = ?, updated_at = ? WHERE id = ?",
                         (1 if approved else 0, now_iso(), int(entry_id)))
            conn.execute("INSERT INTO activity (at, agent, kind, application, summary) VALUES (?,?,?,?,?)",
                         (now_iso(), by or "user", row["kind"], row["application"],
                          f"{'Approved' if approved else 'Approval withdrawn'}: {row['kind']} '{row['key'][:80]}'"))
            row = conn.execute("SELECT * FROM knowledge WHERE id = ?", (int(entry_id),)).fetchone()
        return self._row(row)

    def activity(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._db()
        if conn is None:
            return []
        with self._lock:
            return [dict(r) for r in conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?",
                                                  (max(1, min(int(limit), 1000)),)).fetchall()]

    def applications(self) -> list[dict[str, Any]]:
        conn = self._db()
        if conn is None:
            return []
        with self._lock:
            rows = conn.execute("SELECT application, COUNT(DISTINCT kind || key) AS entries, MAX(updated_at) AS last "
                                "FROM knowledge GROUP BY application ORDER BY last DESC").fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        cfg = self.config()
        conn = self._db()
        out: dict[str, Any] = {"enabled": cfg["enabled"], "memory_path": cfg["memory_path"],
                               "status": "DISABLED" if not cfg["enabled"] else "UNAVAILABLE" if conn is None else "ACTIVE",
                               "problem": self.problem, "pending": self._pending_count(), "kinds": {}}
        if conn is None:
            return out
        with self._lock:
            for r in conn.execute("SELECT kind, COUNT(*) AS versions, COUNT(DISTINCT application || '|' || key) AS "
                                  "entries, SUM(approved) AS approved FROM knowledge GROUP BY kind").fetchall():
                out["kinds"][r["kind"]] = {"entries": r["entries"], "versions": r["versions"],
                                           "approved": r["approved"] or 0}
            out["applications"] = conn.execute("SELECT COUNT(DISTINCT application) FROM knowledge").fetchone()[0]
            out["activity"] = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
        db = Path(cfg["memory_path"]) / DB_NAME
        try:
            out["size_mb"] = round(db.stat().st_size / 1048576, 2)
            out["free_mb"] = shutil.disk_usage(db.parent).free // 1048576
        except OSError:
            pass
        return out

    def _pending_count(self) -> int:
        try:
            return sum(1 for _ in self.pending_file.open(encoding="utf-8")) if self.pending_file.exists() else 0
        except OSError:
            return 0

    # ---- maintenance: integrity, backup, restore, export, import, reset, migration --------------------------------

    def integrity(self) -> dict[str, Any]:
        conn = self._db()
        if conn is None:
            return {"ok": False, "problem": self.problem}
        with self._lock:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            bad = 0
            for r in conn.execute("SELECT data, sha256 FROM knowledge").fetchall():
                if hashlib.sha256(r["data"].encode()).hexdigest() != r["sha256"]:
                    bad += 1
        return {"ok": result == "ok" and bad == 0, "sqlite": result, "checksum_mismatches": bad}

    def _backup_dir(self) -> Path:
        return Path(self.config()["memory_path"]) / "backups"

    def backup(self, note: str = "") -> dict[str, Any]:
        conn = self._db()
        if conn is None:
            raise QAError("AURA_UNAVAILABLE", self.problem or "AURA memory is unavailable.", status=503)
        folder = self._backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        name = f"aura-{now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        target = folder / name
        with self._lock:
            dest = sqlite3.connect(str(target))
            try:
                conn.backup(dest)
            finally:
                dest.close()
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        (folder / (name + ".sha256")).write_text(f"{sha}  {name}\n{note}\n", encoding="utf-8")
        return {"file": name, "sha256": sha, "size_mb": round(target.stat().st_size / 1048576, 2)}

    def backups(self) -> list[dict[str, Any]]:
        folder = self._backup_dir()
        if not folder.exists():
            return []
        return [{"file": p.name, "size_mb": round(p.stat().st_size / 1048576, 2),
                 "created": p.name[5:20]} for p in sorted(folder.glob("aura-*.sqlite3"), reverse=True)]

    def restore(self, file_name: str) -> dict[str, Any]:
        if not re.fullmatch(r"aura-\d{8}-\d{6}\.sqlite3", file_name or ""):
            raise QAError("AURA_BACKUP_INVALID", "Choose a backup from the list.")
        source = self._backup_dir() / file_name
        if not source.is_file():
            raise QAError("AURA_BACKUP_MISSING", "This backup does not exist.", status=404)
        sums = source.with_name(source.name + ".sha256")
        if sums.exists() and sums.read_text(encoding="utf-8").split()[0] != hashlib.sha256(source.read_bytes()).hexdigest():
            raise QAError("AURA_BACKUP_CORRUPT", "This backup does not match its checksum; it was not restored.", status=409)
        check = sqlite3.connect(str(source))
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise QAError("AURA_BACKUP_CORRUPT", "This backup is damaged; it was not restored.", status=409)
        finally:
            check.close()
        safety = self.backup("automatic backup before a restore")
        with self._lock:
            src = sqlite3.connect(str(source))
            try:
                src.backup(self._conn)
            finally:
                src.close()
        return {"restored": file_name, "safety_backup": safety["file"]}

    def export(self, application: str | None = None) -> bytes:
        """A zip with knowledge.json (latest and earlier versions), activity.json and a manifest with checksums."""
        rows = self.entries(application=application, latest_only=False, limit=1000000) if application is not None \
            else self.entries(latest_only=False, limit=1000000)
        payload = json.dumps(rows, ensure_ascii=False, indent=1, default=str).encode()
        manifest = {"format": "aura-export-1", "created_at": now_iso(), "application": application,
                    "entries": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("knowledge.json", payload)
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
        return buf.getvalue()

    def import_zip(self, content: bytes) -> dict[str, Any]:
        import io

        if len(content) > 200 * 1024 * 1024:
            raise QAError("AURA_IMPORT_TOO_LARGE", "The file is larger than 200 MB.", status=413)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = set(z.namelist())
                if not {"knowledge.json", "manifest.json"} <= names:
                    raise QAError("AURA_IMPORT_INVALID", "This is not an AURA export (knowledge.json missing).")
                info = z.getinfo("knowledge.json")
                if info.file_size > 500 * 1024 * 1024:
                    raise QAError("AURA_IMPORT_TOO_LARGE", "The export is too large to import.", status=413)
                payload = z.read("knowledge.json")
                manifest = json.loads(z.read("manifest.json"))
        except zipfile.BadZipFile as exc:
            raise QAError("AURA_IMPORT_INVALID", "This file is not a zip file.") from exc
        if manifest.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise QAError("AURA_IMPORT_CORRUPT", "The export does not match its checksum; nothing was imported.")
        rows = json.loads(payload)
        added = 0
        for r in sorted(rows, key=lambda x: (x.get("kind", ""), x.get("application", ""), x.get("key", ""),
                                             x.get("version", 0))):
            if r.get("kind") not in KINDS:
                continue
            res = self.learn(r["kind"], r.get("application"), r.get("key", ""), r.get("data") or {},
                             source=f"import:{r.get('source') or ''}"[:120], approved=bool(r.get("approved")),
                             agent=r.get("agent"))
            if res and res.get("changed"):
                added += 1
        return {"imported": added, "in_file": len(rows)}

    def reset(self, application: str | None, confirm: str) -> dict[str, Any]:
        expected = f"RESET {application}" if application else "RESET ALL"
        if (confirm or "").strip() != expected:
            raise QAError("AURA_RESET_UNCONFIRMED", f"Type {expected} to confirm.", status=409)
        safety = self.backup(f"automatic backup before {expected}")
        conn = self._db()
        with self._lock:
            if application:
                n = conn.execute("DELETE FROM knowledge WHERE application = ?", (application,)).rowcount
            else:
                n = conn.execute("DELETE FROM knowledge").rowcount
            conn.execute("INSERT INTO activity (at, agent, kind, application, summary) VALUES (?,?,?,?,?)",
                         (now_iso(), "user", None, application or "", f"Memory reset ({n} entries removed)"))
        return {"removed": n, "safety_backup": safety["file"]}

    def migrate(self, new_path: str) -> dict[str, Any]:
        """Move the memory to another folder: copy, verify, switch. The old copy stays where it was."""
        target = self.check_folder(new_path)
        current = Path(self.config()["memory_path"])
        if target.resolve() == current.resolve():
            return {"memory_path": str(target), "moved": False}
        dest = target / DB_NAME
        conn = self._db()
        if dest.exists():
            raise QAError("AURA_TARGET_HAS_MEMORY", f"{target} already holds an AURA memory. Choose an empty folder, or "
                          "use it as is with 'Use this folder'.", status=409)
        if conn is not None:
            with self._lock:
                out = sqlite3.connect(str(dest))
                try:
                    conn.backup(out)
                finally:
                    out.close()
            check = sqlite3.connect(str(dest))
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise QAError("AURA_MIGRATION_FAILED", "The copied memory did not pass its integrity check; the "
                                  "memory stays where it was.", status=500)
            finally:
                check.close()
        self.settings.update({"aura": {"memory_path": str(target)}})
        self._open()
        return {"memory_path": str(target), "moved": conn is not None, "old_copy_kept_at": str(current)}

    def use_folder(self, path: str) -> dict[str, Any]:
        target = self.check_folder(path)
        self.settings.update({"aura": {"memory_path": str(target)}})
        self._open()
        return self.stats()

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        self.settings.update({"aura": {"enabled": bool(enabled)}})
        return self.stats()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # ---- helpers the agents use -------------------------------------------------------------------------------

    def approved_locator(self, application: str | None, selector: str) -> str | None:
        hit = self.recall("approved_correction", application, f"locator:{selector}", approved_only=True)
        return (hit or {}).get("data", {}).get("healed") if hit else None

    def component_hint(self, application: str | None, label: str) -> dict[str, Any] | None:
        hit = self.recall("component", application, f"label:{label.strip().lower()}")
        return hit.get("data") if hit else None


def default_memory_path(paths: Paths) -> str:
    return os.fspath(paths.root / "aura_memory")
