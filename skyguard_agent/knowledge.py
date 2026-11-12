"""Application knowledge: registry, accounts, versioned learned knowledge, locator memory, backup / restore / export.

Every learned item (screen, workflow, business rule, relationship, role) is a versioned row with provenance: source,
confidence, timestamp, application version, creator and approver. States:

  discovered          found by automatic discovery (read-only)
  ai_suggested        proposed by the local model; never used for execution until approved
  awaiting_approval   ready for a person to review (guided learning, corrections, imports)
  approved            used by the planner and the executor
  rejected            kept for history, never used
  superseded          an older approved version
  needs_revalidation  the application changed (change detection); not used until approved again

Only ``approved`` knowledge drives operations. Nothing here stores a secret.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .secrets_guard import redact
from .store import Store
from .util import AgentError, canonical, dumps, loads, new_id, norm, now_iso
from .vault import Vault, expired

STATES = ("discovered", "ai_suggested", "awaiting_approval", "approved", "rejected", "superseded",
          "needs_revalidation")
KINDS = ("screen", "workflow", "rule", "relationship", "role", "profile")
MODES = ("testing", "role_testing", "production")
STATE_LABELS = {"discovered": "Discovered", "ai_suggested": "AI Suggested", "awaiting_approval": "Awaiting Approval",
                "approved": "Approved", "rejected": "Rejected", "superseded": "Superseded",
                "needs_revalidation": "Needs Revalidation"}
NAME_RE = re.compile(r"^[\w .,&()'/+-]{1,80}$")
KEY_RE = re.compile(r"^[\w .:/>()&+-]{1,160}$")


def safe_app_url(url: str) -> str:
    """An application URL a person may register: http(s), a host, no embedded credentials, no odd schemes."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise AgentError("URL_INVALID", "The application URL must start with http:// or https://.")
    if not parts.hostname:
        raise AgentError("URL_INVALID", "The application URL has no host name.")
    if parts.username or parts.password:
        raise AgentError("URL_INVALID", "Do not put a login in the URL. Use the secure credential form.")
    if any(c in url for c in ("\n", "\r", "\t", " ", "\\")):
        raise AgentError("URL_INVALID", "The application URL contains characters that are not allowed.")
    host = parts.hostname
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_multicast or ip.is_unspecified or str(ip) == "169.254.169.254":
            raise AgentError("URL_INVALID", "That address cannot be registered as an application.")
    except ValueError:
        pass
    return url


def origin_of(url: str) -> str:
    p = urlsplit(url)
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{(p.hostname or '').lower()}{port}"


def screen_signature(screen: dict[str, Any]) -> dict[str, list[str]]:
    """The parts of a screen whose change means learned workflows must be checked again."""
    return {
        "fields": sorted({f"{norm(f.get('label'))}|{f.get('type')}" for f in screen.get("fields", []) if f.get("label")}),
        "buttons": sorted({norm(b.get("label")) for b in screen.get("buttons", []) if b.get("label")}),
        "tables": sorted({"|".join(norm(h) for h in t.get("headers", [])) for t in screen.get("tables", [])}),
    }


def signature_diff(old: dict[str, list[str]], new: dict[str, list[str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in ("fields", "buttons", "tables"):
        a, b = set(old.get(part, [])), set(new.get(part, []))
        if a != b:
            out[part] = {"removed": sorted(a - b), "added": sorted(b - a)}
    return out


class KnowledgeBase:
    def __init__(self, store: Store, vault: Vault, audit: Any) -> None:
        self.store = store
        self.vault = vault
        self.audit = audit

    # ---- applications --------------------------------------------------------------------------------------------

    def _app_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["origins"] = loads(row["origins"], [])
        row["profile"] = loads(row["profile"])
        return row

    def register_application(self, name: str, base_url: str, *, environment: str = "", description: str = "",
                             mode: str = "testing", app_version: str = "", extra_origins: list[str] | None = None,
                             by: str | None = None) -> dict[str, Any]:
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise AgentError("NAME_INVALID", "Give the application a name of up to 80 letters, digits or spaces.")
        if mode not in MODES:
            raise AgentError("MODE_INVALID", f"The mode must be one of {', '.join(MODES)}.")
        url = safe_app_url(base_url)
        origins = sorted({origin_of(url), *(origin_of(safe_app_url(o)) for o in (extra_origins or []))})
        if self.store.query_one("SELECT id FROM applications WHERE name = ?", (name,)):
            raise AgentError("APP_EXISTS", f"An application called {name} is already registered.", 409)
        app_id = new_id("app")
        now = now_iso()
        self.store.execute("INSERT INTO applications (id, name, base_url, origins, environment, description, "
                           "app_version, mode, profile, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (app_id, name, url, dumps(origins), environment[:60], description[:2000], app_version[:60],
                            mode, "{}", by, now, now))
        self.audit.record("application.registered", actor=by, target=app_id, details={"name": name, "url": url,
                                                                                         "mode": mode})
        return self.application(app_id)

    def update_application(self, app_id: str, by: str | None = None, **changes: Any) -> dict[str, Any]:
        app = self.application(app_id)
        sets: dict[str, Any] = {}
        if "base_url" in changes and changes["base_url"]:
            sets["base_url"] = safe_app_url(changes["base_url"])
            sets["origins"] = dumps(sorted({origin_of(sets["base_url"]), *app["origins"]}))
        for key in ("environment", "description", "app_version"):
            if key in changes and changes[key] is not None:
                sets[key] = str(changes[key])[:2000]
        if changes.get("mode"):
            if changes["mode"] not in MODES:
                raise AgentError("MODE_INVALID", f"The mode must be one of {', '.join(MODES)}.")
            sets["mode"] = changes["mode"]
        if changes.get("extra_origins") is not None:
            sets["origins"] = dumps(sorted({origin_of(sets.get("base_url", app["base_url"])),
                                            *(origin_of(safe_app_url(o)) for o in changes["extra_origins"])}))
        if not sets:
            return app
        sets["updated_at"] = now_iso()
        self.store.execute(f"UPDATE applications SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                           [*sets.values(), app_id])
        self.audit.record("application.updated", actor=by, target=app_id, details={k: v for k, v in sets.items()
                                                                                    if k != "updated_at"})
        return self.application(app_id)

    def set_profile(self, app_id: str, profile: dict[str, Any], by: str | None = None) -> dict[str, Any]:
        """Login, logout, identity and role-verification knowledge (no secrets; refs to locator labels only)."""
        self.application(app_id)
        login = dict(profile.get("login") or {})
        clean = redact({k: v for k, v in profile.items() if k != "login"})
        if login:  # the sign-in page's field *labels* ("Password" is a label here, never a value)
            clean["login"] = {k: str(v)[:120] for k, v in login.items()
                              if k in ("url", "username", "password", "submit", "screen", "manual") and v is not None}
            if login.get("manual") is not None:
                clean["login"]["manual"] = bool(login.get("manual"))
        self.store.execute("UPDATE applications SET profile = ?, updated_at = ? WHERE id = ?",
                           (dumps(clean), now_iso(), app_id))
        self.audit.record("application.profile", actor=by, target=app_id, details={"keys": sorted(clean)})
        return self.application(app_id)

    def application(self, app_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM applications WHERE id = ? OR name = ?", (app_id, app_id))
        if not row:
            raise AgentError("APP_NOT_FOUND", "That application is not registered.", 404)
        return self._app_row(row)

    def applications(self) -> list[dict[str, Any]]:
        apps = [self._app_row(r) for r in self.store.query("SELECT * FROM applications ORDER BY name")]
        for a in apps:
            counts = self.store.query("SELECT kind, state, COUNT(*) AS n FROM knowledge k WHERE application_id = ? "
                                      "AND version = (SELECT MAX(version) FROM knowledge k2 WHERE k2.application_id ="
                                      " k.application_id AND k2.kind = k.kind AND k2.key = k.key) GROUP BY kind, state",
                                      (a["id"],))
            a["knowledge"] = counts
            a["accounts"] = self.store.query_one("SELECT COUNT(*) AS n FROM accounts WHERE application_id = ?",
                                                 (a["id"],))["n"]
        return apps

    def delete_application(self, app_id: str, by: str | None = None) -> None:
        app = self.application(app_id)
        for acc in self.accounts(app["id"]):
            self.delete_account(acc["id"], by=by)
        self.store.execute("DELETE FROM applications WHERE id = ?", (app["id"],))
        self.audit.record("application.deleted", actor=by, target=app["id"], details={"name": app["name"]})

    def allowed_origin(self, url: str) -> dict[str, Any] | None:
        """The registered application this URL belongs to (navigation is only allowed there)."""
        try:
            origin = origin_of(safe_app_url(url))
        except AgentError:
            return None
        for app in self.applications():
            if origin in app["origins"]:
                return app
        return None

    # ---- accounts and credentials --------------------------------------------------------------------------------

    def _account_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in row.items() if k != "credential_ref"}
        out["has_credential"] = bool(row.get("credential_ref")) and not row["revoked"]
        out["credential_expired"] = expired(row.get("expires_at"))
        out["revoked"] = bool(row["revoked"])
        return out

    def add_account(self, app_id: str, label: str, login_id: str, *, role_key: str = "", role_description: str = "",
                    environment: str = "", owner: str = "", by: str | None = None) -> dict[str, Any]:
        app = self.application(app_id)
        label, login_id = (label or "").strip(), (login_id or "").strip()
        if not NAME_RE.match(label):
            raise AgentError("LABEL_INVALID", "Give the account a label such as 'Entry Operator (test)'.")
        if not login_id or len(login_id) > 120 or any(c in login_id for c in "\r\n\t"):
            raise AgentError("LOGIN_INVALID", "The login ID must be 1-120 characters on one line.")
        if self.store.query_one("SELECT id FROM accounts WHERE application_id = ? AND label = ?", (app["id"], label)):
            raise AgentError("ACCOUNT_EXISTS", f"{label} already exists for {app['name']}.", 409)
        acc_id = new_id("acc")
        now = now_iso()
        self.store.execute("INSERT INTO accounts (id, application_id, label, login_id, role_key, role_description, "
                           "environment, owner, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (acc_id, app["id"], label, login_id, norm(role_key)[:60], role_description[:300],
                            environment[:60], owner[:80], now, now))
        self.audit.record("account.added", actor=by, target=acc_id, details={"application": app["name"],
                                                                                "label": label, "role": role_key})
        return self.account(acc_id)

    def update_account(self, acc_id: str, by: str | None = None, **changes: Any) -> dict[str, Any]:
        self.account(acc_id)
        sets: dict[str, Any] = {}
        for key in ("label", "login_id", "role_description", "environment", "owner"):
            if changes.get(key) is not None:
                sets[key] = str(changes[key]).strip()[:300]
        if changes.get("role_key") is not None:
            sets["role_key"] = norm(changes["role_key"])[:60]
        if "login_id" in sets or "role_key" in sets:  # a different identity must be verified again
            sets.update(auth_status="not_verified", verified_identity=None, verified_role=None, verified_at=None)
        if not sets:
            return self.account(acc_id)
        sets["updated_at"] = now_iso()
        self.store.execute(f"UPDATE accounts SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                           [*sets.values(), acc_id])
        self.audit.record("account.updated", actor=by, target=acc_id, details={k: v for k, v in sets.items()
                                                                                  if k != "updated_at"})
        return self.account(acc_id)

    def set_credential(self, acc_id: str, password: str, *, consent: bool, expires_at: str | None = None,
                       by: str | None = None) -> dict[str, Any]:
        row = self._raw_account(acc_id)
        ref = self.vault.save(password, consent=consent, ref=row.get("credential_ref") or None)
        self.vault.remember(password)
        now = now_iso()
        self.store.execute("UPDATE accounts SET credential_ref = ?, consent_at = ?, expires_at = ?, revoked = 0, "
                           "auth_status = 'not_verified', updated_at = ? WHERE id = ?",
                           (ref, now, expires_at, now, acc_id))
        self.audit.record("credential.saved", actor=by, target=acc_id,
                          details={"backend": self.vault.backend_name, "expires_at": expires_at})
        return self.account(acc_id)

    def revoke_credential(self, acc_id: str, by: str | None = None) -> dict[str, Any]:
        row = self._raw_account(acc_id)
        if row.get("credential_ref"):
            self.vault.revoke(row["credential_ref"])
        self.store.execute("UPDATE accounts SET credential_ref = NULL, revoked = 1, auth_status = 'revoked', "
                           "updated_at = ? WHERE id = ?", (now_iso(), acc_id))
        self.audit.record("credential.revoked", actor=by, target=acc_id)
        return self.account(acc_id)

    def delete_account(self, acc_id: str, by: str | None = None) -> None:
        row = self._raw_account(acc_id)
        if row.get("credential_ref"):
            try:
                self.vault.revoke(row["credential_ref"])
            except AgentError:
                pass
        self.store.execute("DELETE FROM accounts WHERE id = ?", (acc_id,))
        self.audit.record("account.deleted", actor=by, target=acc_id, details={"label": row["label"]})

    def mark_account(self, acc_id: str, *, auth_status: str, identity: str | None = None,
                     role: str | None = None) -> None:
        self.store.execute("UPDATE accounts SET auth_status = ?, verified_identity = ?, verified_role = ?, "
                           "verified_at = ?, updated_at = ? WHERE id = ?",
                           (auth_status, identity, role, now_iso() if auth_status == "verified" else None, now_iso(),
                            acc_id))

    def _raw_account(self, acc_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM accounts WHERE id = ?", (acc_id,))
        if not row:
            raise AgentError("ACCOUNT_NOT_FOUND", "That account does not exist.", 404)
        return row

    def account(self, acc_id: str) -> dict[str, Any]:
        return self._account_row(self._raw_account(acc_id))

    def accounts(self, app_id: str | None = None) -> list[dict[str, Any]]:
        if app_id:
            rows = self.store.query("SELECT * FROM accounts WHERE application_id = ? ORDER BY label",
                                    (self.application(app_id)["id"],))
        else:
            rows = self.store.query("SELECT * FROM accounts ORDER BY application_id, label")
        return [self._account_row(r) for r in rows]

    def credential_for(self, acc_id: str) -> str | None:
        """For the executor only: the password at the moment the login field is filled."""
        row = self._raw_account(acc_id)
        if row["revoked"] or not row.get("credential_ref"):
            return None
        if expired(row.get("expires_at")):
            raise AgentError("CREDENTIAL_EXPIRED", f"The saved credential for {row['label']} has expired. Replace it "
                             "in Accounts.", 409)
        return self.vault.reveal(row["credential_ref"])

    # ---- versioned knowledge -------------------------------------------------------------------------------------

    @staticmethod
    def _entry(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["data"] = loads(row["data"])
        row["state_label"] = STATE_LABELS.get(row["state"], row["state"])
        return row

    def propose(self, app_id: str, kind: str, key: str, data: dict[str, Any], *, source: str,
                state: str = "awaiting_approval", confidence: float = 0.5, by: str | None = None,
                note: str | None = None) -> dict[str, Any]:
        """Add a new version of a knowledge item (or return the current one when nothing changed)."""
        if kind not in KINDS:
            raise AgentError("KIND_INVALID", f"Unknown knowledge kind {kind}.")
        if state not in STATES or state in ("superseded",):
            raise AgentError("STATE_INVALID", f"Knowledge cannot be created in state {state}.")
        key = " ".join(str(key or "").split())[:160]
        if not key or not KEY_RE.match(key):
            raise AgentError("KEY_INVALID", "A knowledge key must be a short name (letters, digits, spaces, : / > -).")
        app = self.application(app_id)
        clean = redact(data)
        latest = self.latest(app["id"], kind, key)
        if latest and canonical(latest["data"]) == canonical(clean) and latest["state"] not in ("rejected",):
            return latest
        version = (latest["version"] + 1) if latest else 1
        now = now_iso()
        cur = self.store.execute(
            "INSERT INTO knowledge (application_id, kind, key, version, state, data, source, confidence, app_version, "
            "created_by, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (app["id"], kind, key, version, state, dumps(clean), source[:40], float(max(0.0, min(1.0, confidence))),
             app.get("app_version") or "", by, (note or "")[:500], now, now))
        self.audit.record("knowledge.proposed", actor=by, target=f"{app['id']}:{kind}:{key}",
                          details={"version": version, "state": state, "source": source})
        return self.entry(int(cur.lastrowid))

    def entry(self, entry_id: int) -> dict[str, Any]:
        row = self.store.query_one("SELECT * FROM knowledge WHERE id = ?", (entry_id,))
        if not row:
            raise AgentError("KNOWLEDGE_NOT_FOUND", "That knowledge entry does not exist.", 404)
        return self._entry(row)

    def latest(self, app_id: str, kind: str, key: str) -> dict[str, Any] | None:
        row = self.store.query_one("SELECT * FROM knowledge WHERE application_id = ? AND kind = ? AND key = ? "
                                   "ORDER BY version DESC LIMIT 1", (app_id, kind, key))
        return self._entry(row) if row else None

    def approved(self, app_id: str, kind: str, key: str) -> dict[str, Any] | None:
        row = self.store.query_one("SELECT * FROM knowledge WHERE application_id = ? AND kind = ? AND key = ? AND "
                                   "state = 'approved' ORDER BY version DESC LIMIT 1", (app_id, kind, key))
        return self._entry(row) if row else None

    def items(self, app_id: str | None = None, kind: str | None = None, state: str | None = None,
              current_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM knowledge k WHERE 1=1"
        params: list[Any] = []
        if app_id:
            sql += " AND application_id = ?"
            params.append(self.application(app_id)["id"])
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if current_only:
            sql += (" AND version = (SELECT MAX(version) FROM knowledge k2 WHERE k2.application_id = k.application_id"
                    " AND k2.kind = k.kind AND k2.key = k.key)")
        if state:
            sql += " AND state = ?"
            params.append(state)
        return [self._entry(r) for r in self.store.query(sql + " ORDER BY kind, key, version", params)]

    def approved_items(self, app_id: str, kind: str) -> list[dict[str, Any]]:
        rows = self.store.query(
            "SELECT * FROM knowledge k WHERE application_id = ? AND kind = ? AND state = 'approved' AND version = "
            "(SELECT MAX(version) FROM knowledge k2 WHERE k2.application_id = k.application_id AND k2.kind = k.kind "
            "AND k2.key = k.key AND k2.state = 'approved') ORDER BY key", (app_id, kind))
        return [self._entry(r) for r in rows]

    def history(self, app_id: str, kind: str, key: str) -> list[dict[str, Any]]:
        return [self._entry(r) for r in self.store.query(
            "SELECT * FROM knowledge WHERE application_id = ? AND kind = ? AND key = ? ORDER BY version",
            (self.application(app_id)["id"], kind, key))]

    def set_state(self, entry_id: int, state: str, *, by: str | None = None, note: str | None = None) -> dict[str, Any]:
        if state not in STATES:
            raise AgentError("STATE_INVALID", f"Unknown state {state}.")
        e = self.entry(entry_id)
        now = now_iso()
        with self.store.transaction() as tx:
            if state == "approved":
                tx.execute("UPDATE knowledge SET state = 'superseded', updated_at = ? WHERE application_id = ? AND "
                           "kind = ? AND key = ? AND state = 'approved' AND id != ?",
                           (now, e["application_id"], e["kind"], e["key"], entry_id))
                tx.execute("UPDATE knowledge SET state = ?, approver = ?, approved_at = ?, note = COALESCE(?, note), "
                           "updated_at = ? WHERE id = ?", (state, by, now, note, now, entry_id))
            else:
                tx.execute("UPDATE knowledge SET state = ?, note = COALESCE(?, note), updated_at = ? WHERE id = ?",
                           (state, note, now, entry_id))
        self.audit.record(f"knowledge.{state}", actor=by, target=f"{e['application_id']}:{e['kind']}:{e['key']}",
                          details={"version": e["version"], "note": note})
        return self.entry(entry_id)

    def approve(self, entry_id: int, by: str | None = None, note: str | None = None) -> dict[str, Any]:
        e = self.entry(entry_id)
        if e["state"] in ("rejected", "superseded"):
            raise AgentError("STATE_INVALID", f"A {STATE_LABELS[e['state']]} version cannot be approved; edit it to "
                             "create a new version.", 409)
        return self.set_state(entry_id, "approved", by=by, note=note)

    def reject(self, entry_id: int, by: str | None = None, note: str | None = None) -> dict[str, Any]:
        return self.set_state(entry_id, "rejected", by=by, note=note)

    def correct(self, entry_id: int, data: dict[str, Any], by: str | None = None, note: str | None = None) -> dict[str, Any]:
        """A person's correction becomes a new version awaiting approval (provenance: user correction)."""
        e = self.entry(entry_id)
        return self.propose(e["application_id"], e["kind"], e["key"], data, source="user_correction",
                            state="awaiting_approval", confidence=1.0, by=by, note=note)

    def rollback(self, app_id: str, kind: str, key: str, version: int, by: str | None = None) -> dict[str, Any]:
        """Make an earlier version current again (as a new approved version, so history is never rewritten)."""
        old = self.store.query_one("SELECT * FROM knowledge WHERE application_id = ? AND kind = ? AND key = ? AND "
                                   "version = ?", (self.application(app_id)["id"], kind, key, version))
        if not old:
            raise AgentError("KNOWLEDGE_NOT_FOUND", f"Version {version} does not exist.", 404)
        app = self.application(app_id)
        latest = self.latest(app["id"], kind, key)
        now = now_iso()
        cur = self.store.execute(
            "INSERT INTO knowledge (application_id, kind, key, version, state, data, source, confidence, app_version, "
            "created_by, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (app["id"], kind, key, latest["version"] + 1, "awaiting_approval", old["data"], "rollback",
             old["confidence"], app.get("app_version") or "", by, f"rollback to version {version}", now, now))
        entry = self.approve(int(cur.lastrowid), by=by, note=f"rollback to version {version}")
        self.audit.record("knowledge.rollback", actor=by, target=f"{app['id']}:{kind}:{key}",
                          details={"to_version": version})
        return entry

    def needs_revalidation(self, app_id: str, kind: str, key: str, reason: dict[str, Any],
                           by: str = "change-detection") -> dict[str, Any] | None:
        cur = self.approved(app_id, kind, key)
        if not cur:
            return None
        e = self.propose(app_id, kind, key, {**cur["data"], "_change": reason}, source="change_detection",
                         state="needs_revalidation", confidence=cur["confidence"], by=by,
                         note="The application changed; check and approve again.")
        self.store.execute("UPDATE knowledge SET state = 'superseded' WHERE id = ?", (cur["id"],))
        return e

    # ---- change detection ----------------------------------------------------------------------------------------

    def detect_change(self, app_id: str, screen_key: str, observed: dict[str, Any]) -> dict[str, Any] | None:
        """Compare an observed screen with its approved knowledge; flag the screen and its workflows if it changed."""
        known = self.approved(app_id, "screen", screen_key)
        if not known:
            return None
        diff = signature_diff(screen_signature(known["data"]), screen_signature(observed))
        if not diff:
            return None
        flagged = [screen_key]
        self.needs_revalidation(app_id, "screen", screen_key, diff)
        for wf in self.approved_items(app_id, "workflow"):
            screens = {s.get("screen") for s in wf["data"].get("steps", [])}
            if screen_key in screens:
                self.needs_revalidation(app_id, "workflow", wf["key"], {"screen": screen_key, **diff})
                flagged.append(f"workflow:{wf['key']}")
        self.audit.record("application.changed", target=app_id, details={"screen": screen_key, "diff": diff,
                                                                            "flagged": flagged})
        return {"screen": screen_key, "diff": diff, "flagged": flagged}

    # ---- locator memory ------------------------------------------------------------------------------------------

    def locator(self, app_id: str, screen: str, label: str) -> dict[str, Any] | None:
        row = self.store.query_one("SELECT * FROM locators WHERE application_id = ? AND screen = ? AND label = ?",
                                   (app_id, screen, label))
        if not row:
            row = self.store.query_one("SELECT * FROM locators WHERE application_id = ? AND label = ? AND "
                                       "(screen = '' OR screen = '*') LIMIT 1", (app_id, label))
        if not row:
            return None
        row["fingerprint"] = loads(row["fingerprint"])
        row["alternatives"] = loads(row["alternatives"], [])
        row["history"] = loads(row["history"], [])
        return row

    def locators(self, app_id: str) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM locators WHERE application_id = ? ORDER BY screen, label",
                                (self.application(app_id)["id"],))
        for r in rows:
            r["fingerprint"] = loads(r["fingerprint"])
            r["alternatives"] = loads(r["alternatives"], [])
            r["history"] = loads(r["history"], [])
        return rows

    def remember_locator(self, app_id: str, screen: str, label: str, fingerprint: dict[str, Any], *,
                         kind: str = "control", confidence: float = 1.0, reason: str = "learned",
                         by: str | None = None) -> dict[str, Any]:
        fp = {k: v for k, v in redact(fingerprint).items() if k not in ("handle", "value", "rect")}
        now = now_iso()
        existing = self.locator(app_id, screen, label)
        if existing and existing["screen"] == screen:
            if canonical(existing["fingerprint"]) == canonical(fp):
                return existing
            history = existing["history"][-19:] + [{"version": existing["version"], "fingerprint": existing["fingerprint"],
                                                    "confidence": existing["confidence"], "replaced_at": now,
                                                    "reason": reason}]
            alternatives = (existing["alternatives"] + [existing["fingerprint"]])[-5:]
            self.store.execute("UPDATE locators SET fingerprint = ?, alternatives = ?, confidence = ?, version = ?, "
                               "history = ?, updated_at = ?, kind = ? WHERE id = ?",
                               (dumps(fp), dumps(alternatives), confidence, existing["version"] + 1, dumps(history),
                                now, kind, existing["id"]))
        else:
            self.store.execute("INSERT INTO locators (application_id, screen, label, kind, fingerprint, confidence, "
                               "updated_at) VALUES (?,?,?,?,?,?,?)", (app_id, screen, label, kind, dumps(fp),
                                                                      confidence, now))
        self.audit.record("locator.updated", actor=by, target=f"{app_id}:{screen}:{label}",
                          details={"reason": reason, "confidence": confidence})
        return self.locator(app_id, screen, label)  # type: ignore[return-value]

    # ---- export / import / backup / restore ----------------------------------------------------------------------

    def export(self, app_id: str | None = None) -> dict[str, Any]:
        apps = [self.application(app_id)] if app_id else [self._app_row(r) for r in
                                                          self.store.query("SELECT * FROM applications")]
        out: dict[str, Any] = {"format": "skyguard-agent-knowledge", "format_version": 1, "exported_at": now_iso(),
                               "applications": []}
        for a in apps:
            out["applications"].append({
                "application": {k: a[k] for k in ("name", "base_url", "origins", "environment", "description",
                                                  "app_version", "mode", "profile")},
                "accounts": [{k: acc[k] for k in ("label", "login_id", "role_key", "role_description", "environment")}
                             for acc in self.accounts(a["id"])],  # never credentials
                "knowledge": [{k: e[k] for k in ("kind", "key", "version", "state", "data", "source", "confidence",
                                                 "app_version", "approver", "approved_at", "created_at")}
                              for e in self.items(a["id"], current_only=False)],
                "locators": [{k: loc[k] for k in ("screen", "label", "kind", "fingerprint", "alternatives",
                                                  "confidence", "version")} for loc in self.locators(a["id"])],
            })
        return out

    def import_(self, data: dict[str, Any], by: str | None = None) -> dict[str, Any]:
        """Imported knowledge always arrives as ``awaiting_approval``: another installation's approval is not ours."""
        if data.get("format") != "skyguard-agent-knowledge":
            raise AgentError("IMPORT_INVALID", "This is not a SkyGuard agent knowledge export.")
        counts = {"applications": 0, "knowledge": 0, "locators": 0, "accounts": 0}
        for block in data.get("applications", [])[:200]:
            a = block.get("application") or {}
            existing = self.store.query_one("SELECT id FROM applications WHERE name = ?", (a.get("name"),))
            if existing:
                app_id = existing["id"]
            else:
                app_id = self.register_application(a.get("name", ""), a.get("base_url", ""),
                                                   environment=a.get("environment", ""),
                                                   description=a.get("description", ""), mode=a.get("mode", "testing"),
                                                   app_version=a.get("app_version", ""), by=by)["id"]
                counts["applications"] += 1
            for acc in block.get("accounts", [])[:500]:
                try:
                    self.add_account(app_id, acc.get("label", ""), acc.get("login_id", ""),
                                     role_key=acc.get("role_key", ""), role_description=acc.get("role_description", ""),
                                     environment=acc.get("environment", ""), by=by)
                    counts["accounts"] += 1
                except AgentError:
                    pass
            seen: set[tuple[str, str]] = set()
            for e in sorted(block.get("knowledge", []), key=lambda x: -int(x.get("version", 0))):
                if (e.get("kind"), e.get("key")) in seen or e.get("state") in ("rejected", "superseded"):
                    continue
                seen.add((e.get("kind"), e.get("key")))
                self.propose(app_id, e["kind"], e["key"], e.get("data") or {}, source="import",
                             state="awaiting_approval", confidence=float(e.get("confidence") or 0.5), by=by,
                             note="imported; review before use")
                counts["knowledge"] += 1
            for loc in block.get("locators", [])[:5000]:
                self.remember_locator(app_id, loc["screen"], loc["label"], loc.get("fingerprint") or {},
                                      kind=loc.get("kind", "control"), confidence=float(loc.get("confidence") or 0.5),
                                      reason="import", by=by)
                counts["locators"] += 1
        self.audit.record("knowledge.imported", actor=by, details=counts)
        return counts

    def backup(self, folder: Path | str, by: str | None = None) -> dict[str, Any]:
        folder = Path(folder)
        name = f"knowledge-{now_iso().replace(':', '').replace('+', 'p')[:22]}.sqlite3"
        dest = folder / name
        self.store.backup_to(dest)
        (folder / (name + ".json")).write_text(json.dumps({"created": now_iso(), "schema": self.store.schema_version,
                                                           "by": by}), encoding="utf-8")
        self.audit.record("knowledge.backup", actor=by, details={"file": name})
        return {"file": str(dest), "name": name}

    def backups(self, folder: Path | str) -> list[dict[str, Any]]:
        folder = Path(folder)
        if not folder.exists():
            return []
        return [{"name": p.name, "size": p.stat().st_size} for p in sorted(folder.glob("knowledge-*.sqlite3"),
                                                                            reverse=True)]

    def restore(self, folder: Path | str, name: str, by: str | None = None) -> dict[str, Any]:
        """Replace the knowledge with a backup (the current state is backed up first)."""
        import sqlite3

        folder = Path(folder)
        if not re.match(r"^knowledge-[\w.-]+\.sqlite3$", name or ""):
            raise AgentError("BACKUP_INVALID", "Choose one of the listed backups.")
        src = (folder / name).resolve()
        if src.parent != folder.resolve() or not src.is_file():
            raise AgentError("BACKUP_NOT_FOUND", "That backup does not exist.", 404)
        probe = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AgentError("BACKUP_DAMAGED", "That backup fails the integrity check.", 409)
            version = probe.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        finally:
            probe.close()
        if version > self.store.schema_version:
            raise AgentError("BACKUP_NEWER", "That backup comes from a newer version of the agent.", 409)
        safety = self.backup(folder, by=by)
        source = sqlite3.connect(str(src))
        try:
            with self.store._lock:
                source.backup(self.store._conn)
        finally:
            source.close()
        self.store.migrate()
        self.audit.record("knowledge.restored", actor=by, details={"from": name, "safety_backup": safety["name"]})
        return {"restored": name, "safety_backup": safety["name"]}
