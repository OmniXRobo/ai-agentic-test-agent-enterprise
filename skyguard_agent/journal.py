"""The observation journal: what the agent saw while a person used a registered application (23.7.0).

Passive learning needs no session title, no "start / finish" and no explanation from the person. While they browse a
registered application normally (in the SkyGuard *Learn while I browse* browser, or in Chrome / Edge with the
extension's automatic learning on), every page they see and every click, choice and entry they make is written here
in a compact form. The understanding engine (understanding.py) reads the journal and works out the screens, flows,
statuses, business rules and roles on its own.

What is kept, and what never is:
* only pages and actions of registered applications (other origins are dropped before anything is stored);
* passwords, one-time codes and fields that look like secrets are kept as "[secret]" without the value;
* any text matching a saved credential, a card number or a code pattern is redacted (secrets_guard);
* page text is a short excerpt for understanding (identity line, messages); it is data, never instructions;
* the journal is capped per application (oldest entries are pruned) and can be cleared by an admin.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from . import locator
from .knowledge import KnowledgeBase, origin_of
from .secrets_guard import redact_text
from .understanding import field_label
from .util import AgentError, dumps, loads, new_id, norm, now_iso

MAX_PER_APP = 20000
EVENT_KINDS = ("click", "fill", "select", "check", "submit", "navigate")
_FIELD_TAGS = ("input", "select", "textarea")
_NOT_FIELD_TYPES = ("submit", "button", "reset", "image", "hidden")


def _clean(text: Any, limit: int, secrets: list[str]) -> str:
    return redact_text(" ".join(str(text or "").split()), secrets)[:limit]


def compact_page(snap: dict[str, Any], secrets: list[str] | None = None) -> dict[str, Any]:
    """The parts of a page snapshot that learning needs (no handles, no coordinates, no secret values)."""
    secrets = secrets or []
    fields, buttons, links = [], [], []
    seen_buttons: set[tuple[str, bool]] = set()
    for e in snap.get("elements", []):
        if not e.get("visible", True):
            continue
        tag, typ = e.get("tag"), (e.get("type") or "")
        label = _clean(field_label(e) if e.get("tag") in _FIELD_TAGS else locator.describe_target(e), 120, secrets)
        if tag in _FIELD_TAGS and typ not in _NOT_FIELD_TYPES:
            secret = bool(e.get("secret"))
            item = {"label": label, "tag": tag, "type": typ or tag, "required": bool(e.get("required")),
                    "section": _clean(e.get("section"), 60, secrets), "dialog": bool(e.get("dialog")),
                    "value": None if secret else _clean(e.get("value"), 120, secrets), "secret": secret,
                    "disabled": bool(e.get("disabled")), "readonly": bool(e.get("readonly"))}
            if e.get("constraints"):
                item["constraints"] = {k: str(v)[:120] for k, v in (e.get("constraints") or {}).items()
                                       if k in ("pattern", "min", "max", "minlength", "maxlength", "step",
                                                "inputmode", "autocomplete") and v not in (None, "")}
            if e.get("options"):
                item["options"] = [_clean(o.get("text"), 80, secrets) for o in e["options"]][:120]
            if e.get("invalid"):
                item["invalid"] = True
                item["validation"] = _clean(e.get("validation"), 120, secrets)
            if typ in ("checkbox", "radio"):
                item["checked"] = bool(e.get("checked"))
            fields.append(item)
        elif e.get("role") == "link" and e.get("href"):
            links.append({"label": label, "href": str(e.get("href"))[:300]})
        elif e.get("role") in ("button", "menuitem", "tab", "option", "switch") or tag == "button" \
                or typ in ("submit", "button"):
            in_row = bool(e.get("table"))
            if (norm(label), in_row) in seen_buttons:
                continue
            seen_buttons.add((norm(label), in_row))
            buttons.append({"label": label, "disabled": bool(e.get("disabled")), "in_row": in_row,
                            "section": _clean(e.get("section"), 60, secrets), "dialog": bool(e.get("dialog"))})
    text = str(snap.get("text") or "")
    return {
        "url": str(snap.get("url") or "")[:500], "title": _clean(snap.get("title"), 120, secrets),
        "heading": _clean(snap.get("heading"), 120, secrets),
        "text": _clean(text, 1500, secrets),
        "fields": fields[:150], "buttons": buttons[:120], "links": links[:150],
        "tables": [{"caption": _clean(t.get("caption"), 60, secrets), "layout": bool(t.get("layout")),
                    "headers": [_clean(h, 40, secrets) for h in t.get("headers", [])][:30],
                    "rows": [[_clean(c, 80, secrets) for c in r][:30] for r in t.get("rows", [])[:60]]}
                   for t in snap.get("tables", [])[:6] if not t.get("layout")],
        "messages": [{"kind": m.get("kind") or "info", "text": _clean(m.get("text"), 200, secrets)}
                     for m in snap.get("messages", [])][:10],
        "dialogs": [_clean(d.get("title"), 80, secrets) for d in snap.get("dialogs", [])][:5],
        "auth": {k: (snap.get("auth") or {}).get(k) for k in ("password_fields", "otp_fields", "captcha")},
    }


def compact_event(e: dict[str, Any], secrets: list[str] | None = None) -> dict[str, Any] | None:
    secrets = secrets or []
    if not isinstance(e, dict) or e.get("kind") not in EVENT_KINDS:
        return None
    el = e.get("element") or {}
    secret = bool(e.get("secret") or el.get("secret") or el.get("type") == "password")
    fp = locator.fingerprint_of(el) if el else None
    if fp and fp.get("table"):
        fp["table"] = {"row": _clean((fp["table"] or {}).get("row"), 240, secrets)}
    return {"kind": e["kind"], "url": str(e.get("url") or "")[:500], "title": _clean(e.get("title"), 120, secrets),
            "heading": _clean(e.get("heading"), 120, secrets), "element": fp,
            "options": [_clean(o.get("text"), 80, secrets) for o in (el.get("options") or [])][:120],
            "required": bool(el.get("required")),
            "value": None if secret else _clean(e.get("value"), 200, secrets), "secret": secret,
            "at": e.get("at")}


def page_signature(p: dict[str, Any]) -> str:
    """Two snapshots with the same signature show the same thing (used to skip repeats)."""
    parts = [urlsplit(p.get("url") or "").path, p.get("heading") or "",
             "|".join(sorted(f"{norm(f['label'])}={f.get('value') or ''}" for f in p.get("fields", []))),
             "|".join(m["text"] for m in p.get("messages", [])),
             "|".join(str(len(t.get("rows", []))) + ":" + "/".join(t.get("rows", [[]])[0] if t.get("rows") else [])
                      for t in p.get("tables", [])),
             "|".join(p.get("dialogs", []))]
    return "\n".join(parts)


class Journal:
    """Append-only store of observed pages and actions, per application and browsing session."""

    def __init__(self, kb: KnowledgeBase, known_secrets: Any = None) -> None:
        self.kb = kb
        self.store = kb.store
        self._known = known_secrets or (lambda: [])
        self._last_sig: dict[str, str] = {}

    def new_session(self, app_id: str, *, interface: str, by: str | None, mode: str = "watch") -> dict[str, Any]:
        app = self.kb.application(app_id)
        sid = new_id(mode)
        now = now_iso()
        self.store.execute("INSERT INTO learning_sessions (id, application_id, mode, status, data, created_by, "
                           "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                           (sid, app["id"], mode, "recording", dumps({"title": "Learn while I browse",
                                                                       "interface": interface, "pages": 0,
                                                                       "events": 0}), by, now, now))
        self.kb.audit.record("learning.watch_started", actor=by, interface=interface, target=sid,
                             details={"application": app["name"]})
        return {"id": sid, "application_id": app["id"], "status": "recording"}

    def end_session(self, sid: str, by: str | None = None) -> None:
        self.store.execute("UPDATE learning_sessions SET status = 'finished', updated_at = ? WHERE id = ? AND "
                           "status = 'recording'", (now_iso(), sid))
        self.kb.audit.record("learning.watch_finished", actor=by, target=sid)

    def ensure_session(self, app_id: str, sid: str | None, *, interface: str, by: str | None) -> str:
        """The extension names its own session per tab; create the row on first use."""
        if sid:
            row = self.store.query_one("SELECT id, application_id FROM learning_sessions WHERE id = ?", (sid,))
            if row:
                if row["application_id"] != self.kb.application(app_id)["id"]:
                    raise AgentError("SESSION_INVALID", "That learning session belongs to another application.", 409)
                return sid
            if not re.fullmatch(r"[\w.-]{4,80}", sid):
                raise AgentError("SESSION_INVALID", "Invalid learning session id.")
            now = now_iso()
            self.store.execute("INSERT INTO learning_sessions (id, application_id, mode, status, data, created_by, "
                               "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                               (sid, self.kb.application(app_id)["id"], "watch", "recording",
                                dumps({"title": "Learn while I browse", "interface": interface}), by, now, now))
            return sid
        return self.new_session(app_id, interface=interface, by=by)["id"]

    def record(self, app_id: str, sid: str, events: list[dict[str, Any]] | None = None,
               snapshot: dict[str, Any] | None = None) -> dict[str, int]:
        """Store what the person did (events, in order) and then what the page shows afterwards (snapshot)."""
        app = self.kb.application(app_id)
        secrets = list(self._known() or [])
        now = now_iso()
        stored = {"events": 0, "pages": 0}
        rows: list[tuple[Any, ...]] = []
        for raw in (events or [])[:300]:
            ev = compact_event(raw, secrets)
            if not ev:
                continue
            if ev["url"] and origin_of(ev["url"]) not in app["origins"]:
                continue  # only the registered application is learned
            rows.append((app["id"], sid, now, "event", ev["url"], dumps(ev)))
            stored["events"] += 1
        if snapshot and snapshot.get("url") and origin_of(str(snapshot["url"])) in app["origins"]:
            page = compact_page(snapshot, secrets)
            sig = page_signature(page)
            if self._last_sig.get(sid) != sig or stored["events"]:
                self._last_sig[sid] = sig
                rows.append((app["id"], sid, now, "page", page["url"], dumps(page)))
                stored["pages"] += 1
        if rows:
            with self.store.transaction() as tx:
                for r in rows:
                    tx.execute("INSERT INTO observations (application_id, session_id, at, kind, url, data) "
                               "VALUES (?,?,?,?,?,?)", r)
            self._prune(app["id"])
            row = self.store.query_one("SELECT data FROM learning_sessions WHERE id = ?", (sid,))
            if row:
                d = loads(row["data"])
                d["pages"] = int(d.get("pages") or 0) + stored["pages"]
                d["events"] = int(d.get("events") or 0) + stored["events"]
                self.store.execute("UPDATE learning_sessions SET data = ?, updated_at = ? WHERE id = ?",
                                   (dumps(d), now, sid))
        return stored

    def _prune(self, app_id: str) -> None:
        row = self.store.query_one("SELECT COUNT(*) AS n, MIN(id) AS lo FROM observations WHERE application_id = ?",
                                   (app_id,))
        n = int((row or {}).get("n") or 0)
        if n > MAX_PER_APP:
            self.store.execute("DELETE FROM observations WHERE application_id = ? AND id IN (SELECT id FROM "
                               "observations WHERE application_id = ? ORDER BY id LIMIT ?)",
                               (app_id, app_id, n - MAX_PER_APP))

    def entries(self, app_id: str, after: int = 0, limit: int = MAX_PER_APP) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT id, session_id, at, kind, url, data FROM observations WHERE application_id = ? "
                                "AND id > ? ORDER BY id LIMIT ?", (self.kb.application(app_id)["id"], after, limit))
        out = []
        for r in rows:
            d = loads(r["data"])
            d.update({"_id": r["id"], "_session": r["session_id"], "_at": r["at"], "_t": r["kind"]})
            out.append(d)
        return out

    def stats(self, app_id: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT COUNT(*) AS n, SUM(kind = 'page') AS pages, SUM(kind = 'event') AS events, "
                                   "COUNT(DISTINCT session_id) AS sessions, MAX(id) AS last, MAX(at) AS last_at "
                                   "FROM observations WHERE application_id = ?", (self.kb.application(app_id)["id"],))
        return {"observations": int(row["n"] or 0), "pages": int(row["pages"] or 0), "events": int(row["events"] or 0),
                "sessions": int(row["sessions"] or 0), "last_id": int(row["last"] or 0), "last_at": row["last_at"]}

    def clear(self, app_id: str, by: str | None = None) -> int:
        app = self.kb.application(app_id)
        n = self.stats(app["id"])["observations"]
        self.store.execute("DELETE FROM observations WHERE application_id = ?", (app["id"],))
        self.store.execute("DELETE FROM app_model WHERE application_id = ?", (app["id"],))
        self.kb.audit.record("learning.journal_cleared", actor=by, target=app["id"], details={"observations": n})
        return n
