"""Hash-chained audit trail. Rows cannot be changed or deleted (database triggers), and every row carries the hash of
the one before it, so a gap or an edit made outside the application is detected by ``verify``. Details are passed
through ``secrets_guard.redact`` first: an audit record never holds a password, OTP or session token."""

from __future__ import annotations

import hashlib
from typing import Any

from .secrets_guard import redact
from .store import Store
from .util import canonical, dumps, loads, now_iso


class Audit:
    def __init__(self, store: Store, known_secrets: Any = None) -> None:
        self.store = store
        self.known_secrets = known_secrets or (lambda: [])

    def record(self, action: str, *, actor: str | None = None, interface: str | None = None,
               target: str | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
        safe = redact(details or {}, self.known_secrets())
        with self.store.transaction() as tx:
            prev = tx.query_one("SELECT hash FROM audit ORDER BY id DESC LIMIT 1")
            prev_hash = prev["hash"] if prev else ""
            at = now_iso()
            body = {"at": at, "actor": actor, "interface": interface, "action": action, "target": target,
                    "details": safe, "prev": prev_hash}
            h = hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
            tx.execute("INSERT INTO audit (at, actor, interface, action, target, details, prev_hash, hash) "
                       "VALUES (?,?,?,?,?,?,?,?)", (at, actor, interface, action, target, dumps(safe), prev_hash, h))
        return {"at": at, "action": action, "hash": h}

    def list(self, limit: int = 200, target: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM audit", []
        if target:
            sql += " WHERE target = ?"
            params.append(target)
        rows = self.store.query(sql + " ORDER BY id DESC LIMIT ?", [*params, max(1, min(limit, 2000))])
        for r in rows:
            r["details"] = loads(r["details"])
        return rows

    def verify(self) -> dict[str, Any]:
        prev = ""
        count = 0
        for r in self.store.query("SELECT * FROM audit ORDER BY id"):
            body = {"at": r["at"], "actor": r["actor"], "interface": r["interface"], "action": r["action"],
                    "target": r["target"], "details": loads(r["details"]), "prev": prev}
            if r["prev_hash"] != prev or hashlib.sha256(canonical(body).encode("utf-8")).hexdigest() != r["hash"]:
                return {"valid": False, "checked": count, "broken_at": r["id"]}
            prev = r["hash"]
            count += 1
        return {"valid": True, "checked": count}
