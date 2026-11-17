"""23.10.0: the simple AI agent. CONNECT WEBSITE -> LEARN WHILE YOU BROWSE -> SAY WHAT YOU WANT -> ONE PREVIEW -> DONE.

A thin layer over the existing engine (OperationsAgent). It removes the steps that made the old agent hard to use, and
keeps every safety check the engine already has:

* Connect by URL only. The application name is suggested from the address; accounts are created from the sign-ins the
  agent sees (user name and role only; passwords stay in Windows Credential Manager and are entered by the person).
* Learning is trusted automatically, with an honest label. What the person was seen doing successfully, and what the
  application itself said (its own validation messages), become usable knowledge without an approval step. A task
  seen only once is marked *uncertain*; a model guess stays *AI suggestion* and never drives an execution.
* Plain language and voice. "Create two trips for tomorrow for driver John", "Create Aslam's trip from Dubai to Abu
  Dhabi", "Show pending trips", "Approve the selected trips", "Authorize all approved trips". Relative dates, names,
  routes, counts in words and record selections are turned into the values the engine's deterministic parser reads.
  Anything still missing is asked for once, in one question, and the answer completes the same request.
* Bulk. Values the person did not give are only filled by stated, deterministic rules (next free record number after
  the highest one seen, an available bus or driver that no learned rule rejects); the preview shows every value.
* Records to act on come from what the agent last saw, and every record's real status is checked live in the
  application before the action (the engine's pre-check), so a stale memory cannot approve the wrong record.
* Consequential operations (approve, authorize, delete, cancel, record, submit ...) are flagged so the interface asks
  for an explicit confirmation of the preview. Nothing runs without the one preview confirmation.

Standard library only (the companion of the browser extension runs it too).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any
from urllib.parse import urlsplit

from . import planner
from .knowledge import NAME_RE, origin_of, safe_app_url
from .util import AgentError, dumps, loads, norm, now_iso

CONSEQUENTIAL = re.compile(r"\b(approv|authori[sz]|reject|delet|remov|cancel|void|record|submit|post|pay|close|"
                           r"accept|confirm|finali[sz]|complet|dispatch|release)\w*", re.I)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
                "fifty": 50, "a couple of": 2, "a dozen": 12}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RESOURCE_FIELD = re.compile(r"\b(driver|bus|vehicle|car|truck|van|resource|staff|employee|agent|crew|operator|"
                            r"technician|room|machine|equipment)\b", re.I)
PERSON_FIELD = re.compile(r"\b(driver|staff|employee|agent|crew|operator|technician|person|passenger|customer|"
                          r"user|officer|manager|owner|assignee)\b", re.I)
PENDING_WORDS = ("pending", "open", "new", "waiting", "outstanding")
SELECTION_WORDS = re.compile(r"\b(selected|these|those|them|the same|that list|this list|the list)\b", re.I)
STOP_NAMES = {"Create", "Add", "Make", "Show", "List", "Approve", "Authorize", "Authorise", "Record", "Cancel", "Edit",
              "Delete", "Trip", "Trips", "Today", "Tomorrow", "From", "To", "For", "The", "All", "Please", "And",
              "Change", "Update", "New", "Book", "Schedule", "Driver", "Vehicle", "Bus"} | {d.title() for d in WEEKDAYS}

MIGRATION = """
CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
"""


# ---- small pure helpers (unit-tested) ---------------------------------------------------------------------------

def suggest_name(url: str, title: str | None = None) -> str:
    """A readable application name from the page title or the address ("fleetops.corp.local" -> "Fleetops")."""
    t = re.sub(r"\s+", " ", (title or "")).strip()
    t = re.split(r"\s+[|\-–:]\s+", t)[0].strip() if t else ""
    if t and NAME_RE.match(t) and not re.fullmatch(r"(log ?in|sign ?in|home|index|welcome|untitled)", t, re.I):
        return t[:80]
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        import ipaddress
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip or host in ("localhost",):
        seg = next((s for s in parts.path.split("/") if s and re.fullmatch(r"[A-Za-z][\w-]{1,40}", s)), "")
        base = seg.replace("-", " ").replace("_", " ").title() if seg else "Application"
        return f"{base} ({host}{' port ' + str(parts.port) if parts.port else ''})"[:80]
    labels = [p for p in host.split(".") if p and p not in ("www", "intranet", "app", "apps", "portal", "web")]
    name = labels[0] if labels else host
    return re.sub(r"[-_]+", " ", name).title()[:80] or "Application"


def words_to_numbers(text: str) -> str:
    def rep(m: re.Match[str]) -> str:
        return str(NUMBER_WORDS[m.group(0).lower()])
    pat = r"\b(" + "|".join(sorted((re.escape(k) for k in NUMBER_WORDS), key=len, reverse=True)) + r")\b(?=\s+\w)"
    return re.sub(pat, rep, text, flags=re.I)


def resolve_date_words(text: str, today: _dt.date) -> tuple[str, str | None]:
    """Find a relative date ("tomorrow", "today", "day after tomorrow", "next Monday", "on Friday") or an explicit
    one. Returns (text without the phrase, ISO date or None)."""
    t = text
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return t, m.group(1)
    rules: list[tuple[str, Any]] = [
        (r"\b(?:for\s+|on\s+)?(?:the\s+)?day\s+after\s+tomorrow(?:'s)?\b", lambda: today + _dt.timedelta(days=2)),
        (r"\b(?:for\s+|on\s+)?tomorrow(?:'s)?\b", lambda: today + _dt.timedelta(days=1)),
        (r"\b(?:for\s+|on\s+)?today(?:'s)?\b", lambda: today),
        (r"\b(?:for\s+|on\s+)?yesterday(?:'s)?\b", lambda: today - _dt.timedelta(days=1)),
    ]
    for pat, fn in rules:
        mm = re.search(pat, t, re.I)
        if mm:
            return (t[:mm.start()] + t[mm.end():]).strip(), fn().isoformat()
    mm = re.search(r"\b(?:for\s+|on\s+)?(next\s+)?(" + "|".join(WEEKDAYS) + r")(?:'s)?\b", t, re.I)
    if mm:
        target = WEEKDAYS.index(mm.group(2).lower())
        delta = (target - today.weekday()) % 7
        if delta == 0 or mm.group(1):
            delta = delta or 7
        return (t[:mm.start()] + t[mm.end():]).strip(), (today + _dt.timedelta(days=delta)).isoformat()
    mm = re.search(r"\bin\s+(\d{1,2})\s+days?\b", t, re.I)
    if mm:
        return (t[:mm.start()] + t[mm.end():]).strip(), (today + _dt.timedelta(days=int(mm.group(1)))).isoformat()
    return t, None


def capitalised_names(text: str) -> list[str]:
    """Words that look like a person's name in the request: "driver John", "Aslam's trip", "for Dina Yousef"."""
    out: list[str] = []
    for m in re.finditer(r"\b([A-Z][a-z]{2,})(?:'s)?\b", text):
        w = m.group(1)
        if w not in STOP_NAMES and w not in out:
            out.append(w)
    return out


def next_key(keys: list[str]) -> str | None:
    """The next free record number after the highest one seen with the most common prefix (T100, T102 -> T103)."""
    parsed = [re.fullmatch(r"([A-Za-z-]*?)(\d+)", k or "") for k in keys]
    parsed = [p for p in parsed if p]
    if not parsed:
        return None
    prefixes: dict[str, list[re.Match[str]]] = {}
    for p in parsed:
        prefixes.setdefault(p.group(1), []).append(p)
    prefix, group = max(prefixes.items(), key=lambda kv: len(kv[1]))
    top = max(group, key=lambda p: int(p.group(2)))
    width = len(top.group(2))
    return f"{prefix}{int(top.group(2)) + 1:0{width}d}"


# ---- the agent -------------------------------------------------------------------------------------------------

class Autopilot:
    """Simple-mode front of the OperationsAgent (the four tabs of SkyGuard's AI Agent page and the extension panel)."""

    def __init__(self, agent: Any, *, today: Any = None) -> None:
        self.agent = agent
        self.kb = agent.kb
        self.store = agent.store
        self._today = today
        self.store._conn.executescript(MIGRATION)

    def today(self) -> _dt.date:
        return self._today() if callable(self._today) else (self._today or _dt.date.today())

    # ---- connect -------------------------------------------------------------------------------------------------

    def connect(self, url: str, *, name: str | None = None, by: str | None = None) -> dict[str, Any]:
        """The application for this address: an existing one with the same origin, or a new one (name suggested)."""
        url = safe_app_url(url)
        origin = origin_of(url)
        for app in self.kb.applications():
            if origin in (app.get("origins") or [origin_of(app["base_url"])]):
                return {"application": app, "created": False}
        base = (name or "").strip() or suggest_name(url)
        candidate, n = base, 2
        names = {a["name"] for a in self.kb.applications()}
        while candidate in names:
            candidate = f"{base} {n}"[:80]
            n += 1
        app = self.kb.register_application(candidate, url, description="Connected in the AI Agent (simple mode).",
                                           by=by)
        return {"application": app, "created": True}

    # ---- trust what was learned ----------------------------------------------------------------------------------

    def trust(self, app_id: str, by: str = "auto-learning") -> dict[str, Any]:
        """Make what was verified by observation usable without an approval step; keep guesses as suggestions."""
        promoted: list[dict[str, Any]] = []
        model = self.agent.understanding.model(app_id) or {}
        flows = {f.get("title"): f for f in model.get("flows") or []}
        for e in self.kb.items(app_id):
            if e["state"] not in ("discovered", "awaiting_approval"):
                continue
            d = e["data"] or {}
            if e["kind"] == "workflow":
                if d.get("draft"):
                    continue
                f = flows.get(d.get("title") or e["key"]) or {}
                seen = int((d.get("evidence") or {}).get("successes") or f.get("successes") or 0)
                if seen < 1:
                    continue
                note = (f"Learned by watching: done successfully {seen} times." if seen >= 2 else
                        "Learned from one demonstration: uncertain until seen again.")
            elif e["kind"] == "rule":
                note = "The application's own message or screen constraint."
            elif e["kind"] in ("role", "lifecycle", "screen", "navigation", "login"):
                note = "Seen in the application."
            else:
                continue
            try:
                self.kb.approve(e["id"], by=by, note=note)
                promoted.append({"kind": e["kind"], "key": e["key"]})
            except AgentError:
                continue
        accounts = self.ensure_accounts(app_id, model)
        return {"promoted": promoted, "accounts": accounts}

    def ensure_accounts(self, app_id: str, model: dict[str, Any] | None = None, by: str = "auto-learning") -> list[str]:
        """One account per user the agent saw signing in, with the role the application showed. No password."""
        model = model or self.agent.understanding.model(app_id) or {}
        have = {norm(a["login_id"]) for a in self.kb.accounts(app_id)}
        made: list[str] = []
        for role in model.get("roles") or []:
            for user in role.get("users") or []:
                if not user or norm(user) in have or not NAME_RE.match(user):
                    continue
                try:
                    self.kb.add_account(app_id, user, user, role_key=role.get("key") or role.get("name") or "",
                                        role_description=f"Seen signed in as {role.get('name') or role.get('key')}.",
                                        by=by)
                    have.add(norm(user))
                    made.append(user)
                except AgentError:
                    continue
        return made

    # ---- conversation state ----------------------------------------------------------------------------------------

    def _state(self, conv: str) -> dict[str, Any]:
        row = self.store.query_one("SELECT data FROM conversation_state WHERE conversation_id = ?", (conv,))
        return loads(row["data"]) if row else {}

    def _save_state(self, conv: str, data: dict[str, Any]) -> None:
        self.store.execute("INSERT INTO conversation_state (conversation_id, data, updated_at) VALUES (?,?,?) "
                           "ON CONFLICT(conversation_id) DO UPDATE SET data = excluded.data, "
                           "updated_at = excluded.updated_at", (conv, dumps(data), now_iso()))

    # ---- knowledge for the Applications & Knowledge tab -------------------------------------------------------------

    def knowledge(self, app_id: str) -> dict[str, Any]:
        app = self.kb.application(app_id)
        model = self.agent.understanding.model(app_id) or {}
        flows = {f.get("title"): f for f in model.get("flows") or []}
        tasks, rules, suggestions = [], [], []
        for e in self.kb.items(app_id):
            d = e["data"] or {}
            if e["state"] in ("superseded", "rejected"):
                continue
            if e["kind"] == "workflow" and not d.get("draft"):
                f = flows.get(d.get("title") or e["key"]) or {}
                seen = int((d.get("evidence") or {}).get("successes") or f.get("successes") or 0)
                tasks.append({"id": e["id"], "title": d.get("title") or e["key"], "operation": d.get("operation"),
                              "entity": d.get("entity"), "roles": d.get("roles") or [], "seen": seen,
                              "fields": [p.get("field") or p["name"] for p in d.get("parameters") or []],
                              "usable": e["state"] == "approved",
                              "certainty": "verified" if e["state"] == "approved" and seen >= 2 else "uncertain",
                              "consequential": bool(CONSEQUENTIAL.search(d.get("operation") or e["key"])),
                              "state": e["state_label"], "version": e["version"]})
            elif e["kind"] == "rule":
                item = {"id": e["id"], "rule": d.get("description") or e["key"], "confidence": e.get("confidence"),
                        "state": e["state_label"], "evidence": (d.get("evidence") or d.get("source") or "")}
                (rules if e["state"] == "approved" else suggestions).append(item)
        return {"application": app, "tasks": tasks, "rules": rules, "suggestions": suggestions,
                "roles": model.get("roles") or [], "lifecycles": model.get("lifecycles") or [],
                "screens": [{"title": s.get("title"), "path": s.get("path"), "kind": s.get("kind")}
                            for s in model.get("screens") or []][:200],
                "records": len(model.get("records") or {}), "accounts": self.kb.accounts(app_id),
                "journal": self.agent.journal.stats(app_id)}

    # ---- commands ----------------------------------------------------------------------------------------------

    def command(self, text: str, *, conversation: str | None = None, user: str | None = None,
                interface: str | None = None, app_id: str | None = None, source: str = "typed") -> dict[str, Any]:
        """Plain language (typed or transcribed) to an answer, a question, or one preview to confirm."""
        raw = (text or "").strip()
        if not raw:
            raise AgentError("EMPTY", "Type or say what you want me to do.")
        conv = conversation or None
        state = self._state(conv) if conv else {}
        app = self.agent._app_for(raw, app_id or state.get("app_id"))
        if app and not re.search(r"\b(cancel that|never ?mind|forget it|start over)\b", raw, re.I):
            pending = state.get("pending")
            combined = f"{pending['text']}; {raw}" if pending and pending.get("app_id") == app["id"] else raw
            if pending and pending.get("retry") and re.fullmatch(r"\W*(again|retry|try again|done|ok|okay|saved|go|go "
                                                                 r"ahead|continue|yes)\W*", raw, re.I):
                combined = pending["text"]
            listing = self._show(app, raw, state)
            if listing:
                conv = conv or _conv_id()
                self.agent._remember(conv, "user", raw, user, interface or self.agent.interface, {"source": source})
                self.agent._remember(conv, "agent", listing["text"], user, interface or self.agent.interface,
                                     {"type": listing["type"], "app_id": app["id"]})
                state.update(app_id=app["id"], selection=listing.get("keys", []), selection_entity=listing.get("entity"))
                state.pop("pending", None)
                self._save_state(conv, state)
                return {"conversation": conv, "reply": listing}
            prepared = self._prepare(app, combined, state)
            if prepared.get("ask"):
                conv = conv or _conv_id()
                self.agent._remember(conv, "user", raw, user, interface or self.agent.interface, {"source": source})
                reply = {"type": "clarify", "app_id": app["id"], "text": prepared["ask"],
                         "missing": prepared.get("missing", [])}
                self.agent._remember(conv, "agent", reply["text"], user, interface or self.agent.interface,
                                     {"type": "clarify", "app_id": app["id"]})
                state.update(app_id=app["id"], pending={"app_id": app["id"], "text": prepared["text"]})
                self._save_state(conv, state)
                return {"conversation": conv, "reply": reply}
            text_for_engine = prepared["text"]
        else:
            prepared = {}
            text_for_engine = raw
            if conv:
                state.pop("pending", None)
        out = self.agent.chat(text_for_engine, conversation=conv, user=user, interface=interface,
                              app_id=app["id"] if app else app_id, source=source,
                              workflow=prepared.get("workflow") if self._usable(app, prepared) else None)
        reply = out["reply"]
        if app and reply.get("type") == "error" and reply.get("code") == "CREDENTIAL_MISSING":
            acc = next((a for a in self.kb.accounts(app["id"]) if a["label"] in reply.get("text", "")), None)
            reply = {"type": "need_credential", "app_id": app["id"], "account_id": acc["id"] if acc else None,
                     "account": acc["label"] if acc else None,
                     "text": (f"I need to sign in as {acc['label'] if acc else 'that account'} for this. Save its "
                              "password once in the secure form (it goes to Windows Credential Manager, never into "
                              "chat, learning or logs), then send the same request again.")}
            out["reply"] = reply
            state = self._state(out["conversation"])
            state.update(app_id=app["id"], pending={"app_id": app["id"], "text": text_for_engine, "retry": True})
            self._save_state(out["conversation"], state)
            return out
        if app and reply.get("type") in ("run",):
            reply["consequential"] = bool(prepared.get("consequential"))
            reply["understood"] = text_for_engine if text_for_engine != raw else None
            reply["filled"] = prepared.get("filled", [])
        state = self._state(out["conversation"])
        if app:
            state["app_id"] = app["id"]
        if reply.get("type") == "clarify" and app:
            state["pending"] = {"app_id": app["id"], "text": text_for_engine}
        else:
            state.pop("pending", None)
        self._save_state(out["conversation"], state)
        return out

    def _usable(self, app: dict[str, Any] | None, prepared: dict[str, Any]) -> bool:
        return bool(app and prepared.get("workflow")
                    and self.kb.approved(app["id"], "workflow", prepared["workflow"]))

    def _workflows(self, app_id: str) -> list[dict[str, Any]]:
        approved = self.kb.approved_items(app_id, "workflow")
        keys = {w["key"] for w in approved}
        learned = [e for e in self.kb.items(app_id, "workflow") if e["key"] not in keys
                   and e["state"] in ("discovered", "awaiting_approval") and not (e["data"] or {}).get("draft")]
        return approved + learned

    def _records(self, app_id: str, entity: str | None) -> dict[str, dict[str, Any]]:
        """Records as last seen while learning, updated with the outcomes the agent itself verified since then."""
        model = self.agent.understanding.model(app_id) or {}
        recs = {k: dict(r) for k, r in (model.get("records") or {}).items()}
        seen_at = str(model.get("_updated_at") or model.get("built_at") or "")
        lcs = {norm(lc.get("entity")): lc for lc in model.get("lifecycles") or []}
        runs = self.store.query("SELECT id, plan, updated_at FROM runs WHERE plan LIKE ? ORDER BY created_at",
                                (f'%"app_id": "{app_id}"%',))
        for r in runs:
            if seen_at and str(r["updated_at"]) < seen_at:
                continue
            plan = loads(r["plan"])
            stages = plan.get("stages") or []
            for item in self.agent.engine.items(r["id"]):
                done = (item["result"] or {}).get("stages") or {}
                for st in stages:
                    if done.get(str(st["index"])) != "confirmed_success":
                        continue
                    wf = self.kb.entry(st["workflow_entry_id"])
                    d = wf["data"] or {}
                    key_p = d.get("key_param") or (d.get("exists_check") or {}).get("param")
                    key = (item["record"] or {}).get(key_p) if key_p else None
                    if not key:
                        continue
                    ent = d.get("entity") or ""
                    status = d.get("expected_status") or ((lcs.get(norm(ent)) or {}).get("initial") or {}).get("status")
                    rec = recs.setdefault(key, {"entity": ent, "row": {}, "seen": 0})
                    if status:
                        rec["status"] = status
                    rec["verified_by_agent"] = r["id"]
                    row = dict(rec.get("row") or {})
                    for p in d.get("parameters") or []:
                        if p.get("field") and (item["record"] or {}).get(p["name"]) not in (None, ""):
                            row.setdefault(p["field"], item["record"][p["name"]])
                    rec["row"] = row
        return {k: r for k, r in recs.items() if not entity or norm(r.get("entity")) == norm(entity)}

    def _lifecycle(self, app_id: str, entity: str | None) -> dict[str, Any] | None:
        for lc in (self.agent.understanding.model(app_id) or {}).get("lifecycles") or []:
            if not entity or norm(lc.get("entity")) == norm(entity):
                return lc
        return None

    def _status_in_text(self, app_id: str, entity: str | None, text: str) -> str | None:
        lc = self._lifecycle(app_id, entity)
        if not lc:
            return None
        t = norm(text)
        for s in sorted(lc.get("statuses") or [], key=len, reverse=True):
            if re.search(rf"\b{re.escape(norm(s))}\b", t):
                return s
        if any(re.search(rf"\b{w}\b", t) for w in PENDING_WORDS):
            return (lc.get("initial") or {}).get("status") or (lc.get("statuses") or [None])[0]
        return None

    def _show(self, app: dict[str, Any], text: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """"Show pending trips", "list approved trips": what the agent last saw, which becomes the selection."""
        m = re.match(r"^\s*(?:please\s+)?(show|list|display|find|which are|what are)\b(?:\s+me)?\s+(?:all\s+|the\s+)*"
                     r"(.*)$", text, re.I)
        if not m:
            return None
        entities = {norm((r or {}).get("entity")) for r in self._records(app["id"], None).values()} - {""}
        rest = norm(m.group(2))
        entity = next((e for e in entities if re.search(rf"\b{re.escape(e)}(e?s)?\b", rest)), None)
        if not entity:
            return None
        status = self._status_in_text(app["id"], entity, rest)
        recs = self._records(app["id"], entity)
        rows = [{"key": k, **(r.get("row") or {}), "_status": r.get("status")} for k, r in recs.items()
                if not status or norm(r.get("status")) == norm(status)]
        for name in capitalised_names(text):
            rows = [r for r in rows if any(re.search(rf"\b{re.escape(name.lower())}\b", norm(v)) for v in r.values())]
        date = resolve_date_words(text, self.today())[1]
        if date:
            rows = [r for r in rows if any(str(v) == date for v in r.values())]
        keys = [r["key"] for r in rows]
        label = f"{status.lower()} " if status else ""
        if not rows:
            txt = f"I have not seen any {label}{entity}s in {app['name']}."
        else:
            txt = (f"{len(rows)} {label}{entity}{'s' if len(rows) != 1 else ''} as I last saw them in {app['name']}: "
                   + ", ".join(keys[:25]) + ("..." if len(keys) > 25 else "")
                   + ". They are now your selection; say for example 'approve the selected "
                   + f"{entity}s'. I check each one's real status in the application before acting.")
        return {"type": "records", "app_id": app["id"], "entity": entity, "status": status, "keys": keys,
                "rows": rows[:200], "text": txt}

    def _prepare(self, app: dict[str, Any], text: str, state: dict[str, Any]) -> dict[str, Any]:
        """Rewrite a plain request into the labelled form the engine's parser reads; ask once for what is missing."""
        t = words_to_numbers(text)
        workflows = self._workflows(app["id"])
        wf = match_by_verb(t, workflows) or planner.match_workflow(t, workflows)
        if not wf:
            from .assistant import canonical
            wf = planner.match_workflow(canonical(t, nouns=False), workflows)
        if not wf:
            return {"text": text}
        d = wf["data"]
        params = planner.workflow_params(wf)
        consequential = bool(CONSEQUENTIAL.search(d.get("operation") or wf["key"]))
        extra: list[str] = []
        filled: list[str] = []
        stripped, date = resolve_date_words(t, self.today())
        date_p = next((p for p in params if p.get("type") == "date"), None)
        if date and date_p and not re.search(rf"\b{re.escape(norm(date_p.get('field') or ''))}\s*:", norm(t)):
            extra.append(f"{date_p.get('field') or date_p['name']}: {date}")
        # "from Dubai to Abu Dhabi": a route-like option, or separate origin / destination fields
        rm = re.search(r"\bfrom\s+([A-Za-z][\w .'-]*?)\s+to\s+([A-Za-z][\w .'-]*?)(?=\s*(?:,|;|\bat\b|\bon\b|\bfor\b|"
                       r"\bwith\b|\bby\b|\busing\b|\bdriver\b|\bbus\b|\bvehicle\b|$))", stripped, re.I)
        if rm:
            a, b = rm.group(1).strip(), rm.group(2).strip()
            origin_p = next((p for p in params if re.search(r"\b(from|origin|source|start|pickup|departure (city|place|"
                                                            r"location))\b", norm(p.get("field") or p["name"]))), None)
            dest_p = next((p for p in params if re.search(r"\b(to|destination|dest|end|drop ?off|arrival (city|place|"
                                                          r"location))\b", norm(p.get("field") or p["name"]))), None)
            if origin_p and dest_p:
                extra += [f"{origin_p.get('field') or origin_p['name']}: {a}",
                          f"{dest_p.get('field') or dest_p['name']}: {b}"]
            else:
                for p in params:
                    hit = [o for o in p.get("options") or [] if norm(o) == norm(f"{a} to {b}")
                           or re.fullmatch(rf"{re.escape(norm(a))}\s*(?:to|-|->|→|–)\s*{re.escape(norm(b))}", norm(o))]
                    if hit:
                        extra.append(f"{p.get('field') or p['name']}: {hit[0]}")
                        break
        # names: "driver John", "Aslam's trip" -> the one option that contains the name
        ambiguous: list[str] = []
        for name in capitalised_names(stripped):
            if rm and (name.lower() in norm(rm.group(1)) or name.lower() in norm(rm.group(2))):
                continue
            for p in params:
                opts = p.get("options") or []
                if not opts or not PERSON_FIELD.search(p.get("field") or p["name"]):
                    continue
                if any(norm(o) in norm(t) for o in opts):
                    continue  # the full option is already in the request
                hits = [o for o in opts if re.search(rf"\b{re.escape(name.lower())}\b", norm(o))]
                if len(hits) == 1:
                    extra.append(f"{p.get('field') or p['name']}: {hits[0]}")
                elif len(hits) > 1:
                    ambiguous.append(f"Which {norm(p.get('field') or p['name'])} do you mean: {', '.join(hits)}?")
        if ambiguous:
            return {"text": text, "ask": " ".join(ambiguous), "missing": []}
        if d.get("operation") and _is_row_action(wf):
            return self._prepare_row_action(app, wf, t, extra, state, consequential)
        rewritten = t + ("; " + "; ".join(extra) if extra else "")
        parsed = planner.parse_structured(rewritten, wf)
        count = parsed.get("count") or 1
        given = set(parsed.get("fixed") or {}) | set(parsed.get("lists") or {}) | set(parsed.get("auto") or [])
        if parsed.get("numbering"):
            given.add(parsed["numbering"]["param"])
        key_p = d.get("key_param") or (d.get("exists_check") or {}).get("param")
        more: list[str] = []
        for p in params:
            if p["name"] in given or not p.get("required"):
                continue
            label = p.get("field") or p["name"]
            if p["name"] == key_p:
                nxt = next_key(list(self._records(app["id"], d.get("entity")).keys()))
                if nxt:
                    more.append(f"{label} numbers starting at {nxt}" if count > 1 else f"{label}: {nxt}")
                    filled.append(f"{label} from {nxt} (next free number after the highest one I have seen)")
                    given.add(p["name"])
                continue
            if p.get("options") and RESOURCE_FIELD.search(label) and p.get("type") == "select":
                more.append(f"{label}: available")
                filled.append(f"{label}: an available one that no learned rule rejects")
                given.add(p["name"])
        if more:
            rewritten += "; " + "; ".join(more)
        missing = [p for p in params if p.get("required") and p["name"] not in given]
        if missing:
            names = [p.get("field") or p["name"] for p in missing]
            hints = []
            for p in missing:
                if p.get("options"):
                    hints.append(f"{p.get('field') or p['name']} ({', '.join(p['options'][:4])}"
                                 f"{'...' if len(p['options']) > 4 else ''})")
                elif p.get("type") == "time" and count > 1:
                    hints.append(f"{p.get('field') or p['name']} (for example 'from 06:00 every 30 minutes')")
                elif p.get("type") == "date":
                    hints.append(f"{p.get('field') or p['name']} (for example 'tomorrow')")
            what = f"{count} {d.get('entity') or 'record'}{'s' if count > 1 else ''}"
            return {"text": rewritten, "ask": f"To {d.get('operation') or 'do'} {what} I still need: "
                    + "; ".join(hints or names) + ". Tell me, and I will prepare one preview.",
                    "missing": names}
        return {"text": rewritten, "consequential": consequential, "filled": filled, "workflow": wf["key"]}

    def _prepare_row_action(self, app: dict[str, Any], wf: dict[str, Any], t: str, extra: list[str],
                            state: dict[str, Any], consequential: bool) -> dict[str, Any]:
        d = wf["data"]
        key_p = d.get("key_param") or (planner.workflow_params(wf) or [{}])[0].get("name")
        param = next((p for p in planner.workflow_params(wf) if p["name"] == key_p), None) or {}
        label = param.get("field") or key_p or "key"
        recs = self._records(app["id"], d.get("entity"))
        keys: list[str] = []
        named = [k for k in recs if re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", t, re.I)]
        if named:
            keys = named
        elif SELECTION_WORDS.search(t) and state.get("selection"):
            keys = list(state["selection"])
        else:
            status = self._status_in_text(app["id"], d.get("entity"), t)
            if not status and re.search(r"\ball\b", t, re.I):
                status = self._requires(app["id"], wf)
            if status:
                keys = [k for k, r in recs.items() if norm(r.get("status")) == norm(status)]
                for name in capitalised_names(t):
                    keys = [k for k in keys if any(re.search(rf"\b{re.escape(name.lower())}\b", norm(v))
                                                   for v in (recs[k].get("row") or {}).values())]
                if not keys:
                    return {"text": t, "ask": f"I have not seen any {status.lower()} {d.get('entity') or 'record'}s. "
                            "Open the list in the browser while I am learning, or name the records.", "missing": []}
        if not keys and re.search(r"(?<![\w-])[A-Za-z]{0,6}-?\d{2,}(?![\w-])", t):
            keys = re.findall(r"(?<![\w-])([A-Za-z]{0,6}-?\d{2,})(?![\w-])", t)
        if not keys:
            return {"text": t, "ask": f"Which {d.get('entity') or 'record'}s? Name them ({label}), say 'all "
                    f"{(self._requires(app['id'], wf) or 'pending').lower()}', or first ask me to show them.",
                    "missing": [label]}
        if len(keys) > planner.MAX_BATCH:
            keys = keys[:planner.MAX_BATCH]
        base = re.split(r"[;,]", t)[0]
        rewritten = f"{base}; {label}: {', '.join(keys)}" + ("; " + "; ".join(extra) if extra else "")
        return {"text": rewritten, "consequential": consequential, "keys": keys, "workflow": wf["key"],
                "filled": [f"{len(keys)} record(s): {', '.join(keys[:10])}{'...' if len(keys) > 10 else ''}"]}

    def _requires(self, app_id: str, wf: dict[str, Any]) -> str | None:
        """The status a record must have before this action, from the learned lifecycle."""
        req = ((wf["data"].get("target") or {}).get("requires_status"))
        if req:
            return req
        lc = self._lifecycle(app_id, wf["data"].get("entity"))
        title = wf["data"].get("title") or wf["key"]
        for tr in (lc or {}).get("transitions") or []:
            if norm(tr.get("action")) == norm(title):
                return tr.get("from")
        return None

    # ---- tasks & history ---------------------------------------------------------------------------------------

    def tasks(self, app_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for r in self.agent.engine.runs(limit=limit, app_id=app_id):
            items = self.agent.engine.items(r["id"])
            counts: dict[str, int] = {}
            for i in items:
                bucket = _bucket(i["status"])
                counts[bucket] = counts.get(bucket, 0) + 1
            out.append({**self.agent.engine.view_summary(r), "counts": counts})
        return out


def _bucket(status: str) -> str:
    """Completed, failed, skipped, uncertain or pending: the four outcomes the person reads, plus not-yet-done."""
    s = status or ""
    if s == "confirmed_success":
        return "completed"
    if s == "confirmed_failure":
        return "failed"
    if s == "unknown":
        return "uncertain"
    if s in ("skipped", "cancelled"):
        return "skipped"
    return "pending"


def match_by_verb(text: str, workflows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The task named by the first operation word of the request ("Authorize all approved trips" -> Authorize: the
    later "approved" describes the records, it is not the action)."""
    best: tuple[int, dict[str, Any]] | None = None
    low = norm(text)
    for wf in workflows:
        d = wf["data"] or {}
        op = norm(d.get("operation"))
        if not op:
            continue
        stem = op[:-1] if op.endswith("e") else op
        m = re.search(rf"\b{re.escape(stem)}\w*", low)
        if not m:
            continue
        ent = norm(d.get("entity"))
        if ent and not re.search(rf"\b{re.escape(ent)}", low) and len([w for w in workflows
                                                                     if norm(w["data"].get("operation")) == op]) > 1:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), wf)
    return best[1] if best else None


def _is_row_action(wf: dict[str, Any]) -> bool:
    d = wf["data"]
    if d.get("target_kind") in ("row", "existing") or (d.get("target") or {}).get("kind") == "row":
        return True
    return any(s.get("action") == "open_row" or s.get("row") for s in d.get("steps") or []) and \
        len(planner.workflow_params(wf)) <= 2


def _conv_id() -> str:
    from .util import new_id
    return new_id("conv")
