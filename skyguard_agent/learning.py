"""Application learning: automatic read-only discovery and guided demonstration.

Automatic discovery runs inside the execution engine as a read-only run: it signs in normally (pausing for MFA/OTP),
then only *navigates links* inside the registered application and inspects each screen. It never clicks buttons,
never submits forms and skips links whose words suggest a change (delete, approve, logout, ...). Each screen becomes
``discovered`` knowledge with its fields, dropdown options, buttons, tables, pagination and messages; every field and
button goes into locator memory; a form becomes a *draft* workflow for a person to complete and approve. Seeing a
screen again that differs from its approved knowledge flags it (and the workflows using it) ``needs_revalidation``.

Guided learning records what a person does in the application (the observer in agent_dom.js reports clicks, typed
values and choices; password and code fields arrive only as "[secret]") and turns the demonstration into a workflow
with parameters, steps, locators, a success pattern and suggested business rules, all ``awaiting_approval``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from . import locator
from .guard import SYSTEM_RULES, wrap_untrusted
from .knowledge import KnowledgeBase, origin_of
from .llm import extract_json
from .util import AgentError, dumps, loads, new_id, norm, now_iso

DESTRUCTIVE = re.compile(r"\b(delete|remove|drop|purge|destroy|log ?out|sign ?out|logoff|cancel|approve|reject|"
                         r"authori[sz]e|submit|confirm|pay|transfer|reset|deactivate|disable|archive|terminate|"
                         r"accept|decline|execute|run|send|post|publish|void|refund|close)\b", re.I)
COMMIT_WORDS = re.compile(r"\b(save|submit|create|add|approve|authori[sz]e|confirm|send|update|accept|record|"
                          r"register|book|assign|apply|finish|complete|mark|ready|post)\b", re.I)
MAX_PAGES_DEFAULT = 25


def screen_key(snap: dict[str, Any]) -> str:
    """A readable, stable name for a screen: its heading (or title) and path."""
    head = (snap.get("heading") or snap.get("title") or "").strip()
    path = urlsplit(snap.get("url") or "").path.rstrip("/") or "/"
    name = re.sub(r"[^\w .:/>()&+-]", " ", head)[:60].strip() or "Page"
    return " ".join(f"{name} {path}".split())[:150]


def describe_screen(snap: dict[str, Any]) -> dict[str, Any]:
    fields, buttons, links = [], [], []
    for e in snap.get("elements", []):
        if not e.get("visible", True):
            continue
        label = locator.describe_target(e)
        if e.get("tag") in ("input", "select", "textarea") and e.get("type") not in ("submit", "button", "reset", "image"):
            fields.append({"label": label, "type": e.get("type") or e.get("tag"), "tag": e.get("tag"),
                           "required": bool(e.get("required")), "secret": bool(e.get("secret")),
                           "options": [o.get("text") for o in (e.get("options") or [])][:100],
                           "section": e.get("section") or "", "dialog": bool(e.get("dialog"))})
        elif e.get("role") == "link" and e.get("href"):
            links.append({"label": label, "href": e.get("href")})
        elif e.get("role") in ("button", "menuitem", "tab") or e.get("tag") == "button":
            buttons.append({"label": label, "disabled": bool(e.get("disabled")), "section": e.get("section") or ""})
    path = urlsplit(snap.get("url") or "").path or "/"
    return {"url_path": path, "title": snap.get("title"), "heading": snap.get("heading"),
            "fields": fields[:150], "buttons": buttons[:100], "links": links[:150],
            "tables": [{"caption": t.get("caption"), "headers": t.get("headers", []), "rows_seen": len(t.get("rows", []))}
                       for t in snap.get("tables", [])],
            "pagination": bool((snap.get("pagination") or {}).get("next") or (snap.get("pagination") or {}).get("prev")),
            "messages": [m.get("text") for m in snap.get("messages", [])][:10],
            "dialogs": [d.get("title") for d in snap.get("dialogs", [])],
            "auth": snap.get("auth") or {}}


def param_name(label: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", norm(label)).strip("_")
    return (name or "value")[:40]


def _field_type(el: dict[str, Any]) -> str:
    t = el.get("type") or ""
    if el.get("tag") == "select":
        return "select"
    if t in ("date", "datetime-local"):
        return "date"
    if t == "time":
        return "time"
    if t in ("number",):
        return "number"
    label = norm(el.get("label") or el.get("nearby") or el.get("name"))
    if re.search(r"\bdate\b", label):
        return "date"
    if re.search(r"\btime\b", label):
        return "time"
    return "text"


class Learning:
    def __init__(self, kb: KnowledgeBase, engine: Any, llm: Any = None) -> None:
        self.kb = kb
        self.engine = engine
        self.llm = llm

    # ---- automatic discovery: a read-only run in the engine --------------------------------------------------

    def start_discovery(self, app_id: str, *, account_id: str | None, by: str | None, interface: str,
                        max_pages: int = MAX_PAGES_DEFAULT, start_url: str | None = None) -> dict[str, Any]:
        app = self.kb.application(app_id)
        if account_id:
            acc = self.kb.account(account_id)
            if acc["application_id"] != app["id"]:
                raise AgentError("ACCOUNT_INVALID", "That account belongs to another application.")
        start = urljoin(app["base_url"], start_url or app["base_url"])
        if origin_of(start) not in app["origins"]:
            raise AgentError("URL_REFUSED", "Discovery can only start inside the registered application.")
        run_id = f"DISC-{new_id('x')[2:]}"
        now = now_iso()
        plan = {"app_id": app["id"], "app_name": app["name"], "kind": "discovery", "options": {},
                "stages": [{"index": 0, "title": "Automatic discovery (read-only)", "account_id": account_id,
                            "account": self.kb.account(account_id)["label"] if account_id else None,
                            "commits": False, "workflow": None, "workflow_entry_id": None}]}
        cp = {"phase": "discover", "stage": 0, "item": 0, "step": 0, "sub": "session", "session": {},
              "discover": {"queue": [start], "visited": [], "max_pages": max(1, min(int(max_pages), 200)),
                           "screens": [], "changes": []}}
        self.kb.store.execute("INSERT INTO runs (id, application_id, workflow_key, interface, status, plan, checkpoint, "
                              "created_by, created_at, updated_at, confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (run_id, app["id"], None, interface, "running", dumps(plan), dumps(cp), by, now, now, now))
        self.engine._event(run_id, "plan", f"Automatic discovery of {app['name']} (read-only, up to "
                                           f"{cp['discover']['max_pages']} screens).")
        self.kb.audit.record("discovery.started", actor=by, interface=interface, target=run_id,
                             details={"application": app["name"]})
        return self.engine.view(run_id)

    def discovery_step(self, run: dict[str, Any], cp: dict[str, Any], snap: dict[str, Any],
                       app: dict[str, Any]) -> dict[str, Any]:
        stage = run["plan"]["stages"][0]
        if stage.get("account_id"):
            d = self.engine._ensure_session(run, cp, snap, app, stage)
            if d is not None:
                return d
        disc = cp["discover"]
        cur = disc.get("current")
        if cur and snap and snap.get("url"):
            if origin_of(snap["url"]) in app["origins"]:
                self._record_screen(run, app, snap, disc, stage)
            disc["current"] = None
        while disc["queue"] and len(disc["visited"]) < disc["max_pages"]:
            url = disc["queue"].pop(0)
            key = _norm_url(url)
            if key in disc["visited"]:
                continue
            disc["visited"].append(key)
            disc["current"] = url
            return {"action": {"type": "navigate", "url": url}}
        return self._finish_discovery(run, app, disc)

    def _record_screen(self, run: dict[str, Any], app: dict[str, Any], snap: dict[str, Any], disc: dict[str, Any],
                       stage: dict[str, Any]) -> None:
        key = screen_key(snap)
        data = describe_screen(snap)
        change = self.kb.detect_change(app["id"], key, data)
        if change:
            disc["changes"].append(change)
            self.engine._event(run["id"], "change", f"'{key}' changed since it was approved: "
                                                    f"{', '.join(change['diff'])}. Marked Needs Revalidation.")
        entry = self.kb.propose(app["id"], "screen", key, data, source="automatic_discovery", state="discovered",
                                confidence=0.8, by="discovery")
        for e in snap.get("elements", []):
            if not e.get("visible", True) or e.get("role") == "link":
                continue
            if e.get("tag") in ("input", "select", "textarea", "button") or e.get("role") in ("button", "menuitem",
                                                                                            "tab", "combobox"):
                label = locator.describe_target(e)
                if label and label != "the control":
                    self.kb.remember_locator(app["id"], key, label[:120], locator.fingerprint_of(e),
                                             kind="field" if locator.kind_of(e) == "field" else "control",
                                             confidence=0.8, reason="automatic discovery")
        if data["fields"] and not data["auth"].get("password_fields"):
            self._draft_from_form(app, key, snap, data)
        if stage.get("account_id"):
            acc = self.kb.account(stage["account_id"])
            role = (run["checkpoint"].get("session") or {}).get("role") or acc.get("role_key") or "unknown"
            current = self.kb.latest(app["id"], "role", role)
            screens = dict((current or {}).get("data", {}).get("screens", {}))
            screens[key] = sorted({b["label"] for b in data["buttons"] if b.get("label")})[:60]
            self.kb.propose(app["id"], "role", role, {"name": role, "screens": screens,
                                                      "verified": bool((run["checkpoint"].get("session") or {}).get("role_verified"))},
                            source="automatic_discovery", state="discovered", confidence=0.7, by="discovery")
        disc["screens"].append(key)
        for link in data["links"]:
            href = urljoin(snap.get("url") or app["base_url"], link["href"])
            if href.startswith(("javascript:", "mailto:", "tel:")) or "#" in href.split("/")[-1][:1]:
                continue
            if origin_of(href) not in app["origins"] or DESTRUCTIVE.search(link.get("label") or "") \
                    or DESTRUCTIVE.search(urlsplit(href).path):
                continue
            if re.search(r"\.(pdf|zip|xlsx?|docx?|csv|png|jpe?g)$", urlsplit(href).path, re.I):
                continue
            k = _norm_url(href)
            if k not in disc["visited"] and href not in disc["queue"] and len(disc["queue"]) < 400:
                disc["queue"].append(href)
        self.engine._event(run["id"], "discover", f"Discovered '{key}': {len(data['fields'])} field(s), "
                                                  f"{len(data['buttons'])} button(s), {len(data['tables'])} table(s).",
                           entry=entry["id"])

    def _draft_from_form(self, app: dict[str, Any], key: str, snap: dict[str, Any], data: dict[str, Any]) -> None:
        submit = next((b["label"] for b in data["buttons"] if COMMIT_WORDS.search(b.get("label") or "")), None)
        if not submit:
            return
        params, steps = [], [{"action": "navigate", "url": snap.get("url")}]
        for f in data["fields"]:
            if f.get("secret"):
                continue
            name = param_name(f["label"])
            ftype = "select" if f["tag"] == "select" else _field_type({"type": f["type"], "label": f["label"],
                                                                       "tag": f["tag"]})
            params.append({"name": name, "field": f["label"], "type": ftype, "required": f["required"],
                           "options": f["options"] or None})
            steps.append({"action": "select" if f["tag"] == "select" else "fill", "screen": key,
                          "target": f["label"], "param": name, "optional": not f["required"]})
        steps.append({"action": "click", "screen": key, "target": submit, "commit": True})
        title = f"{submit} - {snap.get('heading') or snap.get('title') or key}"[:80]
        wf_key = f"Draft: {title}"[:150]
        if self.kb.latest(app["id"], "workflow", wf_key):
            return
        self.kb.propose(app["id"], "workflow", re.sub(r"[^\w .:/>()&+-]", " ", wf_key).strip(), {
            "title": title, "operation": norm(submit.split()[0]) if submit else "submit", "entity": "",
            "commits": True, "roles": [], "start_url": snap.get("url"), "screen": key, "parameters": params,
            "steps": steps, "verify": {}, "draft": True,
            "note": "Drafted by discovery from the form; complete verification and roles, then approve."},
            source="automatic_discovery", state="discovered", confidence=0.4, by="discovery")

    def _finish_discovery(self, run: dict[str, Any], app: dict[str, Any], disc: dict[str, Any]) -> dict[str, Any]:
        n = len(disc["screens"])
        msg = (f"Discovery of {app['name']} finished: {n} screen(s) learned (read-only, nothing submitted)"
               + (f", {len(disc['changes'])} changed screen(s) flagged for revalidation" if disc["changes"] else "")
               + ". Review them in Knowledge.")
        cp = run["checkpoint"]
        cp["phase"] = "done"
        self.engine._save(run, status="completed", checkpoint=cp, finished_at=now_iso(),
                          summary={"message": msg, "screens": disc["screens"], "changes": disc["changes"],
                                   "counts": {"confirmed_success": n}, "total": n})
        self.engine._event(run["id"], "done", msg)
        self.kb.audit.record("discovery.finished", target=run["id"], details={"screens": n})
        return {"done": {"status": "completed", "message": msg}}

    # ---- guided learning ---------------------------------------------------------------------------------------

    def start_guided(self, app_id: str, *, account_id: str | None, title: str, by: str | None) -> dict[str, Any]:
        app = self.kb.application(app_id)
        sid = new_id("learn")
        now = now_iso()
        data = {"title": (title or "Demonstrated workflow")[:80], "account_id": account_id, "events": [],
                "snapshots": []}
        self.kb.store.execute("INSERT INTO learning_sessions (id, application_id, mode, status, data, created_by, "
                              "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                              (sid, app["id"], "guided", "recording", dumps(data), by, now, now))
        self.kb.audit.record("learning.started", actor=by, target=sid, details={"application": app["name"],
                                                                                   "title": data["title"]})
        return self.session(sid)

    def session(self, sid: str) -> dict[str, Any]:
        row = self.kb.store.query_one("SELECT * FROM learning_sessions WHERE id = ?", (sid,))
        if not row:
            raise AgentError("LEARNING_NOT_FOUND", "That learning session does not exist.", 404)
        row["data"] = loads(row["data"])
        return row

    def sessions(self, app_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.kb.store.query("SELECT id, application_id, mode, status, created_by, created_at, updated_at, data "
                                   "FROM learning_sessions" + (" WHERE application_id = ?" if app_id else "")
                                   + " ORDER BY created_at DESC LIMIT 100", (app_id,) if app_id else ())
        for r in rows:
            try:
                d = loads(r.pop("data")) or {}
            except Exception:  # noqa: BLE001 - one damaged row must not hide the others
                d = {}
            if not isinstance(d, dict):
                d = {}
            ev = d.get("events")
            # guided sessions keep the events themselves; "Learn while I browse" sessions (23.7.0) keep a count
            r["events"] = len(ev) if isinstance(ev, list) else int(ev) if isinstance(ev, (int, float)) else 0
            r["pages"] = int(d.get("pages") or 0) if isinstance(d.get("pages"), (int, float)) else 0
            r["title"] = d.get("title")
            r["result"] = d.get("result")
        return rows

    def add_events(self, sid: str, events: list[dict[str, Any]], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        s = self.session(sid)
        if s["status"] != "recording":
            raise AgentError("LEARNING_CLOSED", "This learning session is no longer recording.", 409)
        _guided_only(s)
        app = self.kb.application(s["application_id"])
        data = s["data"]
        for e in events[:200]:
            if not isinstance(e, dict) or e.get("kind") not in ("click", "fill", "select", "check", "submit", "navigate"):
                continue
            url = str(e.get("url") or "")
            if url and origin_of(url) not in app["origins"]:
                continue  # only the registered application is learned
            el = e.get("element") or {}
            secret = bool(e.get("secret") or el.get("secret"))
            data["events"].append({"kind": e["kind"], "url": url[:500], "title": str(e.get("title") or "")[:120],
                                   "heading": str(e.get("heading") or "")[:120],
                                   "element": locator.fingerprint_of(el) if el else None,
                                   "options": [o.get("text") for o in (el.get("options") or [])][:100],
                                   "required": bool(el.get("required")),
                                   "value": None if secret else str(e.get("value") or "")[:200], "secret": secret,
                                   "at": e.get("at")})
        if snapshot:
            data["snapshots"].append({"url": snapshot.get("url"), "title": snapshot.get("title"),
                                      "heading": snapshot.get("heading"),
                                      "messages": snapshot.get("messages", [])[:10],
                                      "tables": [{"headers": t.get("headers"), "rows": t.get("rows", [])[:30]}
                                                 for t in snapshot.get("tables", [])[:3]]})
            data["snapshots"] = data["snapshots"][-20:]
        data["events"] = data["events"][-1000:]
        self.kb.store.execute("UPDATE learning_sessions SET data = ?, updated_at = ? WHERE id = ?",
                              (dumps(data), now_iso(), sid))
        return {"events": len(data["events"])}

    def finish_guided(self, sid: str, by: str | None = None, use_ai: bool = True) -> dict[str, Any]:
        s = self.session(sid)
        if s["status"] != "recording":
            raise AgentError("LEARNING_CLOSED", "This learning session is already finished.", 409)
        _guided_only(s)
        app = self.kb.application(s["application_id"])
        data = s["data"]
        events = [e for e in data["events"] if e.get("element") or e["kind"] == "navigate"]
        if not events:
            raise AgentError("LEARNING_EMPTY", "Nothing was demonstrated. Perform the workflow in the application "
                             "while learning is on.", 409)
        login_events, work = _split_login(events)
        profile = dict(app.get("profile") or {})
        if login_events:
            lp = _login_profile(login_events)
            if lp:
                profile["login"] = {**profile.get("login", {}), **lp}
                for ev in login_events:
                    if ev.get("element"):
                        self.kb.remember_locator(app["id"], "login", locator.describe_target(ev["element"]),
                                                 ev["element"], reason="guided learning (sign-in)", by=by)
                self.kb.set_profile(app["id"], profile, by=by)
        params: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []
        last_url = None
        for ev in work:
            screen = screen_key({"url": ev["url"], "heading": ev.get("heading"), "title": ev.get("title")})
            if ev["url"] and _norm_url(ev["url"]) != last_url:
                if not steps:
                    steps.append({"action": "navigate", "url": ev["url"]})
                last_url = _norm_url(ev["url"])
            el = ev.get("element")
            if not el:
                continue
            label = locator.describe_target(el)[:120]
            self.kb.remember_locator(app["id"], screen, label, el, reason="guided learning", by=by)
            if ev["kind"] in ("fill", "select", "check"):
                if ev.get("secret"):
                    steps.append({"action": "pause_for_user", "screen": screen, "target": label,
                                  "note": "secret value: the person enters it"})
                    continue
                name = param_name(label)
                existing = next((p for p in params if p["name"] == name), None)
                if existing:
                    existing["example"] = ev.get("value")
                    steps = [st for st in steps if st.get("param") != name]
                else:
                    params.append({"name": name, "field": label, "type": _field_type({**el, "tag": el.get("tag")}),
                                   "required": bool(ev.get("required")), "example": ev.get("value"),
                                   "options": ev.get("options") or None})
                steps.append({"action": ev["kind"], "screen": screen, "target": label, "param": name})
            elif ev["kind"] == "click":
                steps.append({"action": "click", "screen": screen, "target": label})
        steps = [s for s in steps if s.get("action") != "pause_for_user"]
        commit_idx = max((i for i, st in enumerate(steps) if st.get("action") == "click"
                          and COMMIT_WORDS.search(st.get("target") or "")), default=None)
        if commit_idx is not None:
            steps[commit_idx]["commit"] = True
            steps = steps[:commit_idx + 1]
        verify = _success_pattern(data.get("snapshots", []), params)
        key_param = next((p["name"] for p in params if re.search(r"(number|no|code|ref|id)$", p["name"])), None)
        exists = _list_check(data.get("snapshots", []), params, key_param)
        role = None
        if data.get("account_id"):
            acc = self.kb.account(data["account_id"])
            role = acc.get("verified_role") or acc.get("role_key") or None
        wf = {"title": data["title"], "operation": _operation(data["title"], steps), "entity": _entity(data["title"]),
              "commits": commit_idx is not None, "roles": [role] if role else [], "start_url": steps[0].get("url")
              if steps and steps[0].get("action") == "navigate" else app["base_url"],
              "parameters": params, "steps": steps, "verify": verify, "learned_from": sid,
              "role_note": "role taken from the demonstrating account; confirm it before approving" if role else ""}
        if exists:
            wf["exists_check"] = exists
            if verify.get("record_listed") is None:
                wf["verify"]["record_listed"] = {"url": exists["url"], "column": exists["column"], "param": exists["param"]}
        key = re.sub(r"[^\w .:/>()&+-]", " ", data["title"]).strip()[:150] or "Demonstrated workflow"
        entry = self.kb.propose(app["id"], "workflow", key, wf, source="guided_learning", state="awaiting_approval",
                                confidence=0.75, by=by, note="learned from a demonstration")
        suggestions = self._suggest_rules(app, key, params, use_ai=use_ai)
        data["result"] = {"workflow_entry": entry["id"], "rules": [r["id"] for r in suggestions]}
        self.kb.store.execute("UPDATE learning_sessions SET status = 'finished', data = ?, updated_at = ? WHERE id = ?",
                              (dumps(data), now_iso(), sid))
        self.kb.audit.record("learning.finished", actor=by, target=sid,
                             details={"workflow": key, "steps": len(steps), "parameters": len(params)})
        return {"workflow": entry, "rules": suggestions, "profile_learned": bool(login_events)}

    def cancel_guided(self, sid: str, by: str | None = None) -> dict[str, Any]:
        self.session(sid)
        self.kb.store.execute("UPDATE learning_sessions SET status = 'cancelled', updated_at = ? WHERE id = ?",
                              (now_iso(), sid))
        return self.session(sid)

    def _suggest_rules(self, app: dict[str, Any], wf_key: str, params: list[dict[str, Any]],
                       use_ai: bool) -> list[dict[str, Any]]:
        out = []
        for p in params:
            if p.get("type") == "date":
                out.append(("date", f"{p['field']} is a valid date", {"type": "date", "field": p["name"],
                                                                        "applies_to": wf_key}))
            if p.get("type") == "time":
                out.append(("time", f"{p['field']} is a valid time", {"type": "time", "field": p["name"],
                                                                        "applies_to": wf_key}))
            if p.get("type") == "select" and p.get("options"):
                out.append(("allowed", f"{p['field']} must be an offered option",
                            {"type": "allowed_values", "field": p["name"], "source": "options", "applies_to": wf_key}))
            if re.search(r"(number|no|code|ref)$", p["name"]):
                out.append(("unique", f"{p['field']} must be unique", {"type": "unique", "field": p["name"],
                                                                         "applies_to": wf_key}))
        entries = [self.kb.propose(app["id"], "rule", f"{wf_key}: {name}", rule, source="guided_learning",
                                   state="ai_suggested", confidence=0.5, by="learning")
                   for _k, name, rule in out]
        if use_ai and self.llm is not None and params:
            try:
                entries += self._llm_rules(app, wf_key, params)
            except Exception:
                pass  # the model is optional; nothing depends on it
        return entries

    def _llm_rules(self, app: dict[str, Any], wf_key: str, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from .rules import validate_rule

        fields = [{"name": p["name"], "label": p["field"], "type": p["type"]} for p in params]
        reply = self.llm.chat([{"role": "system", "content": SYSTEM_RULES + " Suggest business rules as JSON "
                                "{\"rules\": [{\"type\": one of required|pattern|date|time|range|unique|no_overlap,"
                                " \"field\" or \"resource\", ...}]} for the fields given. Only use the field names "
                                "given. At most 6 rules."},
                               {"role": "user", "content": f"Workflow: {wf_key}\nFields: {fields}"}],
                              max_tokens=500, json_mode=True)
        data = extract_json(reply)
        names = {p["name"] for p in params}
        out = []
        for r in (data.get("rules") if isinstance(data, dict) else []) or []:
            if not isinstance(r, dict) or validate_rule(r):
                continue
            used = {r.get(k) for k in ("field", "resource", "date", "time") if r.get(k)}
            if not used or not used <= names:
                continue
            r = {k: v for k, v in r.items() if k in ("type", "field", "resource", "date", "time", "regex", "min",
                                                     "max", "min_days", "max_days", "duration_minutes", "values")}
            r["applies_to"] = wf_key
            out.append(self.kb.propose(app["id"], "rule", f"{wf_key}: AI {r['type']} {r.get('field') or r.get('resource')}",
                                       r, source="ai_suggestion", state="ai_suggested", confidence=0.3, by="local-llm"))
        return out

    def describe_with_ai(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        """Optional: the local model names what a screen is for. Page text goes in as untrusted data only."""
        if self.llm is None:
            return None
        block, injected = wrap_untrusted(f"{snap.get('title')}\n{snap.get('heading')}\n{snap.get('text')}", 2500)
        reply = self.llm.chat([{"role": "system", "content": SYSTEM_RULES + ' Return JSON {"purpose": "...", '
                                '"entity": "..."} describing the screen in under 20 words.'},
                               {"role": "user", "content": block}], max_tokens=120, json_mode=True)
        data = extract_json(reply)
        if not isinstance(data, dict):
            return None
        return {"purpose": str(data.get("purpose", ""))[:160], "entity": str(data.get("entity", ""))[:40],
                "injection_seen": injected}


def _norm_url(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{(p.hostname or '').lower()}{':' + str(p.port) if p.port else ''}{p.path.rstrip('/') or '/'}" \
           + (f"?{p.query}" if p.query else "")


def _split_login(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Events on a sign-in page (a page where a secret was typed) are sign-in knowledge, not workflow steps."""
    login_urls = {_norm_url(e["url"]) for e in events if e.get("secret") and (e.get("element") or {}).get("type") == "password"}
    login = [e for e in events if _norm_url(e["url"]) in login_urls]
    return login, [e for e in events if _norm_url(e["url"]) not in login_urls]


def _login_profile(events: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for e in events:
        el = e.get("element") or {}
        label = locator.describe_target(el)
        if e["kind"] == "fill" and el.get("type") == "password":
            out["password"] = label
        elif e["kind"] == "fill" and not e.get("secret"):
            out.setdefault("username", label)
        elif e["kind"] == "click":
            out["submit"] = label
        out.setdefault("url", e["url"])
    return out


def _success_pattern(snaps: list[dict[str, Any]], params: list[dict[str, Any]]) -> dict[str, Any]:
    for s in reversed(snaps):
        for m in s.get("messages", []):
            if m.get("kind") == "success" or re.search(r"(success|created|saved|added|approved|updated|recorded)",
                                                       m.get("text", ""), re.I):
                pattern = re.escape(m["text"][:160])
                for p in params:
                    ex = str(p.get("example") or "")
                    if len(ex) >= 2 and re.escape(ex) in pattern:
                        pattern = pattern.replace(re.escape(ex), "{" + p["name"] + "}")
                return {"success_pattern": pattern}
    return {}


def _list_check(snaps: list[dict[str, Any]], params: list[dict[str, Any]], key_param: str | None) -> dict[str, Any] | None:
    if not key_param:
        return None
    example = norm(next((p.get("example") for p in params if p["name"] == key_param), ""))
    if not example:
        return None
    for s in reversed(snaps):
        for t in s.get("tables", []):
            for row in t.get("rows", []):
                for i, cell in enumerate(row):
                    if norm(cell) == example and i < len(t.get("headers") or []):
                        path = urlsplit(s.get("url") or "").path
                        return {"url": path, "list_url": path, "column": t["headers"][i], "param": key_param}
    return None


def _operation(title: str, steps: list[dict[str, Any]]) -> str:
    m = COMMIT_WORDS.search(title or "")
    if m:
        return norm(m.group(1))
    commit = next((s for s in steps if s.get("commit")), None)
    return norm((commit or {}).get("target", "submit").split()[0]) if commit else "view"


def _entity(title: str) -> str:
    words = [w for w in re.findall(r"[a-z]+", norm(title)) if not COMMIT_WORDS.fullmatch(w) and len(w) > 2
             and w not in ("the", "new", "and", "for")]
    return words[0] if words else ""


def _guided_only(s: dict[str, Any]) -> None:
    """23.7.1: a "Learn while I browse" session keeps a count of events, not the events (they are observations);
    it is stopped with Stop learning, not finished as a demonstrated workflow."""
    if s.get("mode") == "watch" or not isinstance((s.get("data") or {}).get("events"), list):
        raise AgentError("LEARNING_NOT_GUIDED", "This is a 'Learn while I browse' session. Stop it with Stop learning; "
                         "what it learned is under Understanding.", 409)
