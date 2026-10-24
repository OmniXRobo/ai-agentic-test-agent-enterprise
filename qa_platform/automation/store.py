"""Automation script store (versioned JSON files) and application credential profiles.

Every save keeps the previous version under ``versions/`` so a script change,
including one proposed by self-healing, can always be rolled back.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..clock import now_iso
from ..credentials import APP_CREDENTIAL_NAMESPACE, SecretStore
from ..errors import QAError
from ..storage.db import Database
from .dsl import Script, new_script_id

_SAFE_ID = re.compile(r"^SCR-[A-Z0-9]{10}$")


class ScriptStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, script_id: str) -> Path:
        if not _SAFE_ID.match(script_id):
            raise QAError("SCRIPT_ID_INVALID", f"Invalid script id '{script_id}'.", status=400)
        return self.root / script_id

    def list(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(self.root.glob("SCR-*/script.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: data.get(k) for k in ("id", "name", "mode", "test_case_id", "user_story_id", "base_url",
                                                  "version", "updated_at", "application", "credential_role")}
                       | {"steps": len(data.get("steps", []))})
        out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
        return out

    def get(self, script_id: str) -> Script:
        path = self._dir(script_id) / "script.json"
        if not path.exists():
            raise QAError("SCRIPT_NOT_FOUND", f"Script {script_id} does not exist.", status=404)
        return Script.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, script: Script, *, reason: str = "edit") -> Script:
        if not script.id:
            script.id = new_script_id()
        folder = self._dir(script.id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "script.json"
        ts = now_iso()
        if path.exists():
            previous = Script.model_validate_json(path.read_text(encoding="utf-8"))
            versions = folder / "versions"
            versions.mkdir(exist_ok=True)
            (versions / f"v{previous.version:04d}.json").write_text(previous.model_dump_json(indent=2), encoding="utf-8")
            script.version = previous.version + 1
            script.created_at = previous.created_at
        else:
            script.created_at = script.created_at or ts
        script.updated_at = ts
        tmp = path.with_suffix(".tmp")
        tmp.write_text(script.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        with (folder / "history.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": ts, "version": script.version, "reason": reason}) + "\n")
        return script

    def versions(self, script_id: str) -> list[dict[str, Any]]:
        folder = self._dir(script_id)
        history = folder / "history.log"
        if not history.exists():
            return []
        return [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]

    def restore(self, script_id: str, version: int) -> Script:
        path = self._dir(script_id) / "versions" / f"v{int(version):04d}.json"
        if not path.exists():
            raise QAError("SCRIPT_VERSION_NOT_FOUND", f"Version {version} of {script_id} does not exist.", status=404)
        old = Script.model_validate_json(path.read_text(encoding="utf-8"))
        return self.save(old, reason=f"restore v{version}")

    def delete(self, script_id: str) -> None:
        folder = self._dir(script_id)
        if not folder.exists():
            raise QAError("SCRIPT_NOT_FOUND", f"Script {script_id} does not exist.", status=404)
        # Archive instead of deleting, so evidence and history stay traceable.
        archive = self.root / "_archived"
        archive.mkdir(exist_ok=True)
        folder.replace(archive / f"{script_id}-{now_iso().replace(':', '').replace('.', '')[:17]}")


class CredentialProfiles:
    """Role-based application credentials. Usernames in SQLite, passwords in the OS credential store.

    A credential belongs to an application and a role, optionally for one environment (for example
    UAT). Kept separate from TFS credentials (different credential-store namespace). Scripts of the
    AI-Driven Automation framework can also have their own credentials, which override these.
    """

    def __init__(self, db: Database, secrets: SecretStore) -> None:
        self.db = db
        self.secrets = secrets

    @staticmethod
    def key(application: str, role: str, environment: str | None = None) -> str:
        base = f"{application.strip().lower()}|{role.strip().lower()}"
        return f"{base}|{environment.strip().lower()}" if environment and environment.strip() else base

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM app_credentials ORDER BY application, role, environment")
        for r in rows:
            r["has_password"] = self.secrets.has(APP_CREDENTIAL_NAMESPACE, r["id"])
        return rows

    def save(self, application: str, role: str, username: str, password: str | None, login_url: str | None,
             environment: str | None = None) -> dict[str, Any]:
        if not application.strip() or not role.strip() or not username.strip():
            raise QAError("CREDENTIAL_INVALID", "Application, role and username are required.")
        env = (environment or "").strip()
        cid = self.key(application, role, env)
        self.db.execute(
            "INSERT INTO app_credentials (id, application, role, environment, username, login_url, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET username = excluded.username, login_url = excluded.login_url,"
            " updated_at = excluded.updated_at",
            (cid, application.strip(), role.strip(), env, username.strip(), login_url, now_iso()),
        )
        if password:
            self.secrets.set(APP_CREDENTIAL_NAMESPACE, cid, password)
        return next(r for r in self.list() if r["id"] == cid)

    def delete(self, cid: str) -> None:
        self.db.execute("DELETE FROM app_credentials WHERE id = ?", (cid,))
        if self.secrets.available:
            self.secrets.delete(APP_CREDENTIAL_NAMESPACE, cid)

    def find(self, application: str | None, role: str, environment: str | None = None) -> dict[str, Any] | None:
        """The credential of exactly this application and role: the environment's own, else the general one."""
        if not application:
            return None
        if environment and environment.strip():
            row = self.db.query_one("SELECT * FROM app_credentials WHERE id = ?",
                                    (self.key(application, role, environment),))
            if row:
                return row
        return self.db.query_one("SELECT * FROM app_credentials WHERE id = ?", (self.key(application, role),))

    def password_of(self, cid: str) -> str | None:
        return self.secrets.get(APP_CREDENTIAL_NAMESPACE, cid)

    def lookup(self, application: str | None, role: str, field: str) -> str | None:
        """Resolve {{CRED:role.field}} for a script's application."""
        row = None
        if application:
            row = self.db.query_one("SELECT * FROM app_credentials WHERE id = ?", (self.key(application, role),))
        if row is None:
            rows = self.db.query("SELECT * FROM app_credentials WHERE lower(role) = ? AND environment = ''",
                                 (role.strip().lower(),))
            row = rows[0] if len(rows) == 1 else None
        if row is None:
            return None
        if field == "username":
            return row["username"]
        if field == "password":
            return self.secrets.get(APP_CREDENTIAL_NAMESPACE, row["id"])
        if field == "login_url":
            return row["login_url"]
        return None
