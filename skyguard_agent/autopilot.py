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

from . import planner, secrets_guard
from .intent import Intent, classify, compatible, navigation_target, relevance, workflow_category
from .knowledge import NAME_RE, origin_of, safe_app_url
from .util import AgentError, digest, dumps, loads, norm, now_iso

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

    def __init__(self, agent: Any, *, today: Any = None, auto_trust: bool = True) -> None:
        self.agent = agent
        self.kb = agent.kb
        self.store = agent.store
        self._today = today
        self.store._conn.executescript(MIGRATION)
        if auto_trust:
            agent.after_digest = self.trust

    def today(self) -> _dt.date:
        return self._today() if callable(self._today) else (self._today or _dt.date.today())

    # ---- connect -------------------------------------------------------------------------------------------------

    def connect(self, url: str, *, name: str | None = None, by: str | None = None,
                environment: str | None = None) -> dict[str, Any]:
        """The application for this address: an existing one with the same origin, or a new one (name suggested).
        23.11.1: the environment (Test, Production ...) is kept with it; a test environment is simply its own
        address, so its knowledge never mixes with production's."""
        url = safe_app_url(url)
        origin = origin_of(url)
        env = re.sub(r"[^\w .()-]", "", (environment or "").strip())[:60]
        for app in self.kb.applications():
            if origin in (app.get("origins") or [origin_of(app["base_url"])]):
                if env and env != app.get("environment"):
                    app = self.kb.update_application(app["id"], by=by, environment=env)
                return {"application": app, "created": False}
        base = (name or "").strip() or suggest_name(url)
        candidate, n = base, 2
        names = {a["name"] for a in self.kb.applications()}
        while candidate in names:
            candidate = f"{base} {n}"[:80]
            n += 1
        app = self.kb.register_application(candidate, url, description="Connected in the AI Agent (simple mode).",
                                           environment=env, by=by)
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
                # 23.11.1: one observation is not proof of a rule. A rule seen once stays a candidate (shown, never
                # enforced) until it is seen again or an administrator confirms it.
                if int(d.get("observations") or 1) < 2:
                    continue
                note = f"The application's own message or screen constraint, seen {d.get('observations')} times."
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
        seen_roles = {norm(u) for r in model.get("roles") or [] for u in r.get("users") or []}
        for user in model.get("login_users") or []:
            # 23.11.1: a sign-in the agent saw where the application shows no role: the account is created with the
            # role unverified; the role is read from the application when it signs in (never guessed)
            if not user or norm(user) in have or norm(user) in seen_roles or not NAME_RE.match(user):
                continue
            try:
                self.kb.add_account(app_id, user, user, role_key="", by=by,
                                    role_description="Seen signing in while learning; the application did not show "
                                                     "a role.")
                have.add(norm(user))
                made.append(user)
            except AgentError:
                continue
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
        for item in rules + suggestions:
            e = self.kb.entry(item["id"])
            d = e["data"] or {}
            item.update(observations=int(d.get("observations") or 1), applies_to=d.get("applies_to"),
                        verification=("confirmed by an administrator" if e["state"] == "approved" and
                                      int(d.get("observations") or 1) < 2 else
                                      "verified (seen repeatedly)" if e["state"] == "approved" else
                                      "candidate: seen once, not enforced" if int(d.get("observations") or 1) < 2
                                      else "suggestion: not enforced until confirmed"))
        sess = self.store.query_one("SELECT id, status, data, created_by, created_at, updated_at FROM "
                                    "learning_sessions WHERE application_id = ? ORDER BY created_at DESC LIMIT 1",
                                    (app["id"],))
        pages = [{"title": s.get("name"), "path": s.get("path"), "type": s.get("type"), "visits": s.get("visits"),
                  "entity": s.get("entity")} for s in model.get("screens") or []
                 if s.get("type") not in ("sign-in", "verification")]
        unverified = [t["title"] for t in tasks if t["certainty"] != "verified"] + \
            [r["rule"] for r in suggestions]
        return {"application": app, "tasks": tasks, "rules": rules, "suggestions": suggestions, "pages": pages,
                "unverified": unverified,
                "last_session": ({"id": sess["id"], "status": sess["status"], "by": sess["created_by"],
                                  "started": sess["created_at"], "ended": sess["updated_at"],
                                  **{k: v for k, v in loads(sess["data"]).items() if k in ("pages", "events")}}
                                 if sess else None),
                "retrieval": self.verify_retrieval(app["id"]),
                "roles": model.get("roles") or [], "lifecycles": model.get("lifecycles") or [],
                "screens": [{"title": s.get("title"), "path": s.get("path"), "kind": s.get("kind")}
                            for s in model.get("screens") or []][:200],
                "records": len(model.get("records") or {}), "accounts": self.kb.accounts(app_id),
                "journal": self.agent.journal.stats(app_id)}

    # ---- commands ----------------------------------------------------------------------------------------------

    def command(self, text: str, *, conversation: str | None = None, user: str | None = None,
                interface: str | None = None, app_id: str | None = None, source: str = "typed") -> dict[str, Any]:
        """Plain language (typed or transcribed) to an answer, a question, a page to open, or one preview to confirm.

        23.11.1: the intent is decided first (intent.classify), and only a task that performs that kind of operation
        and fits the request's words may be prepared. A new request replaces an unanswered earlier one instead of
        being glued to it. Navigation, reading and questions never produce an execution preview."""
        raw = (text or "").strip()
        if not raw:
            raise AgentError("EMPTY", "Type or say what you want me to do.")
        iface = interface or self.agent.interface
        if secrets_guard.inspect(raw, self.agent._known_secrets()).contains_secret:
            return self.agent.chat(raw, conversation=conversation, user=user, interface=interface, app_id=app_id,
                                   source=source)  # withheld there: nothing of it is stored, logged or planned
        conv = conversation or _conv_id()
        state = self._state(conv)
        app = self.agent._app_for(raw, app_id or state.get("app_id"))
        if app and state.get("app_id") and state["app_id"] != app["id"]:
            state = {"app_id": app["id"]}  # another application: nothing of the other one's conversation carries over
        if not app:
            return self.agent.chat(raw, conversation=conv, user=user, interface=interface, app_id=app_id, source=source)
        self.agent._remember(conv, "user", raw, user, iface, {"source": source, "app_id": app["id"]})

        def done(reply: dict[str, Any], **extra: Any) -> dict[str, Any]:
            reply.setdefault("app_id", app["id"])
            self.agent._remember(conv, "agent", reply.get("text", ""), user, iface,
                                 {k: v for k, v in reply.items() if k in ("type", "run_id", "app_id", "url", "intent")})
            state["app_id"] = app["id"]
            state.update(extra)
            self._save_state(conv, state)
            return {"conversation": conv, "reply": reply}

        if re.search(r"^\W*(cancel that|never ?mind|forget it|start over|stop)\W*$", raw, re.I):
            had = state.pop("pending", None)
            return done({"type": "cleared", "text": "OK, I dropped that request." if had else
                         "OK. Nothing is waiting."})
        intent = classify(raw, app_names=[a["name"] for a in self.kb.applications()])
        pending = self._fresh_pending(state, app)
        combined = raw
        if pending and pending.get("retry") and re.fullmatch(
                r"\W*(again|retry|try again|done|ok|okay|saved|go|go ahead|continue|yes)\W*", raw, re.I):
            combined, intent = pending["text"], Intent(**pending["intent"])
        elif pending and intent.kind in ("data_entry", "unknown"):
            combined, intent = f"{pending['text']}; {raw}", Intent(**pending["intent"])  # the answer to my question
        state.pop("pending", None)  # a new request replaces an unanswered one (it is never glued to it)
        self.agent.audit.record("chat.intent", actor=user, interface=iface, target=app["id"],
                                details={"intent": intent.kind, "verb": intent.verb, "continued": combined != raw})

        if intent.kind == "navigate":
            return done(self._navigate(app, intent))
        if intent.kind == "read":
            listing = self._show(app, raw if combined == raw else combined, state)
            if listing:
                listing["intent"] = intent.as_dict()
                return done(listing, selection=listing.get("keys", []), selection_entity=listing.get("entity"))
            page = navigation_target(Intent("navigate", target=intent.target, words=intent.words), app,
                                     self._screens(app["id"]), self._workflows(app["id"]))
            if page and page["kind"] == "page":
                return done({"type": "navigate", "url": page["url"], "title": page["title"], "intent": intent.as_dict(),
                             "text": f"Opening {page['title']} in {app['name']}."})
            ans = self.agent.assistant.answer(app, raw)
            if ans:
                return done(ans)
            return done({"type": "clarify", "intent": intent.as_dict(),
                         "text": f"I have not seen that in {app['name']} yet. Open it in the browser while I am "
                                 "learning, or tell me which page to open."})
        if intent.kind == "explain":
            ans = self.agent.assistant.answer(app, raw)
            return done(ans or {"type": "answer", "grounded": False,
                                "text": f"I have not learned enough about {app['name']} to answer that yet."})
        if not intent.is_operation:
            sugg = [x["text"] for x in self.suggestions(app["id"])[:3]]
            return done({"type": "clarify", "intent": intent.as_dict(),
                         "text": "What would you like me to do in " + app["name"] + "?"
                                 + (" For example: " + "; ".join(sugg) + "." if sugg else "")})

        if not self._workflows(app["id"]):
            return done({"type": "no_workflows", "intent": intent.as_dict(),
                         "text": f"I have not learned any task in {app['name']} yet. Open Connect & Learn, press Start "
                                 "learning and do the task once in the browser; then ask me again."})
        wf, why = self._choose(app, intent, combined)
        if not wf:
            return done({"type": "clarify", "intent": intent.as_dict(), "text": why})
        prepared = self._prepare(app, combined, state, wf=wf)
        if prepared.get("ask"):
            return done({"type": "clarify", "text": prepared["ask"], "missing": prepared.get("missing", []),
                         "workflow": wf["key"], "intent": intent.as_dict()},
                        pending={"app_id": app["id"], "text": combined, "intent": intent.as_dict(),
                                 "workflow": wf["key"], "at": now_iso()})
        dup = self._duplicate(app["id"], prepared["text"], wf["key"])
        if dup:
            return done({"type": "run", "run_id": dup, "duplicate": True, "workflow": wf["key"],
                         "text": "This request is already prepared (same task and values); here it is again. "
                                 "Nothing new was created."})
        context = {"intent": intent.as_dict(), "command": combined, "conversation": conv, "app_id": app["id"],
                   "workflow": wf["key"], "fingerprint": _fingerprint(app["id"], wf["key"], prepared["text"])}
        out = self.agent.chat(prepared["text"], conversation=conv, user=user, interface=interface, app_id=app["id"],
                              source=source, workflow=wf["key"] if self._usable(app, prepared) else None,
                              context=context, remember=False)
        reply = out["reply"]
        if reply.get("type") == "error" and reply.get("code") == "CREDENTIAL_MISSING":
            acc = next((a for a in self.kb.accounts(app["id"]) if a["label"] in reply.get("text", "")), None)
            reply = {"type": "need_credential", "account_id": acc["id"] if acc else None,
                     "account": acc["label"] if acc else None, "workflow": wf["key"],
                     "security_notice": "Passwords and codes go only into this secure form (Windows Credential "
                                        "Manager). Never type them in the chat.",
                     "text": f"I need to sign in as {acc['label'] if acc else 'that account'} for this. Save its "
                             "password once in the secure form (Windows Credential Manager), then I continue with "
                             "the same request."}
            return done(reply, pending={"app_id": app["id"], "text": prepared["text"], "retry": True,
                                        "intent": intent.as_dict(), "workflow": wf["key"], "at": now_iso()})
        if reply.get("type") == "clarify":
            return done({**reply, "intent": intent.as_dict(), "workflow": wf["key"]},
                        pending={"app_id": app["id"], "text": prepared["text"], "intent": intent.as_dict(),
                                 "workflow": wf["key"], "at": now_iso()})
        if reply.get("type") == "run":
            reply.update(consequential=bool(prepared.get("consequential")), workflow=wf["key"],
                         understood=prepared["text"] if prepared["text"] != raw else None,
                         filled=prepared.get("filled", []), intent=intent.as_dict())
        return done(reply)

    # ---- 23.11.1: routing helpers -------------------------------------------------------------------------------

    PENDING_MINUTES = 30

    def _fresh_pending(self, state: dict[str, Any], app: dict[str, Any]) -> dict[str, Any] | None:
        """The unanswered question of this conversation, only for the same application and only for a while."""
        p = state.get("pending")
        if not p or p.get("app_id") != app["id"] or not p.get("intent"):
            return None
        try:
            at = _dt.datetime.fromisoformat(str(p.get("at")).replace("Z", "+00:00"))
            age = (_dt.datetime.now(_dt.timezone.utc) - at).total_seconds() / 60
        except ValueError:
            return None
        return p if age <= self.PENDING_MINUTES else None

    def _screens(self, app_id: str) -> list[dict[str, Any]]:
        return list((self.agent.understanding.model(app_id) or {}).get("screens") or [])

    def _navigate(self, app: dict[str, Any], intent: Intent) -> dict[str, Any]:
        page = navigation_target(intent, app, self._screens(app["id"]), self._workflows(app["id"]))
        if not page:
            known = [s["name"] for s in self._screens(app["id"]) if s.get("type") not in ("sign-in", "verification")]
            return {"type": "clarify", "intent": intent.as_dict(),
                    "text": f"Which page of {app['name']} do you mean?"
                            + (f" I know: {', '.join(known[:8])}." if known else
                               " I have not learned its pages yet; I can open its start page.")}
        return {"type": "navigate", "url": page["url"], "title": page["title"], "intent": intent.as_dict(),
                "text": f"Opening {page['title']}" + (f" in {app['name']}" if page["kind"] == "page" else "") + "."}

    def _choose(self, app: dict[str, Any], intent: Intent, text: str) -> tuple[dict[str, Any] | None, str]:
        """The one learned task for an operation request, or the one question to ask. Never a task of another kind,
        and never a task about another entity than the one named."""
        workflows = self._workflows(app["id"])
        same_kind = [w for w in workflows if compatible(intent, w)]
        what = f"{intent.verb or intent.kind} {intent.target}".strip()
        if not same_kind:
            can = sorted({(w["data"] or {}).get("title") or w["key"] for w in workflows})
            return None, (f"I have not learned how to {what} in {app['name']}. Do it once in the browser while I "
                          "am learning (Connect & Learn), then ask me again."
                          + (f" What I can do there: {', '.join(can[:8])}." if can else ""))
        entities = {norm((w["data"] or {}).get("entity")) for w in workflows} | \
            {norm(s.get("entity")) for s in self._screens(app["id"])}
        scored = sorted(((relevance(Intent(intent.kind, intent.verb, text, intent.words), w,
                                    other_entities=entities), w) for w in same_kind), key=lambda x: -x[0])
        scored = [(sc, w) for sc, w in scored if sc >= 0.5]
        if not scored:
            titles = [(w["data"] or {}).get("title") or w["key"] for w in same_kind]
            return None, (f"I do not know how to {what} in {app['name']}. Did you mean: {', '.join(titles[:6])}?")
        top = [w for sc, w in scored if sc == scored[0][0]]
        if len(top) > 1:
            named = match_by_verb(text, top) or planner.match_workflow(text, top)
            if named:
                return named, ""
            titles = [(w["data"] or {}).get("title") or w["key"] for w in top]
            return None, f"Which one do you mean: {', '.join(titles[:6])}?"
        return top[0], ""

    def _duplicate(self, app_id: str, text: str, wf_key: str, minutes: int = 10) -> str | None:
        """The run this same request already produced and that is not finished (no second submission)."""
        fp = _fingerprint(app_id, wf_key, text)
        since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes)).isoformat()
        for r in self.store.query("SELECT id, status, request FROM runs WHERE application_id = ? AND created_at >= ? "
                                  "ORDER BY created_at DESC LIMIT 50", (app_id, since)):
            if r["status"] in ("planning", "inspecting", "awaiting_confirmation", "running", "paused") and \
                    (loads(r["request"]) or {}).get("context", {}).get("fingerprint") == fp:
                return r["id"]
        return None

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
        runs = self.store.query("SELECT id, plan, updated_at FROM runs WHERE application_id = ? ORDER BY created_at",
                                (app_id,))
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

    def _prepare(self, app: dict[str, Any], text: str, state: dict[str, Any],
                 wf: dict[str, Any] | None = None) -> dict[str, Any]:
        """Rewrite a plain request into the labelled form the engine's parser reads; ask once for what is missing.
        23.11.1: ``wf`` is the task the router chose; no other task is looked for here."""
        t = words_to_numbers(text)
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
        # 23.11.1: a field named in plain words with its value ("odometer end 500 fuel used 30" for the fields
        # "Odometer end (km)" and "Fuel used (litres)"); only free-text fields, a number or one word as the value
        for p in params:
            label = p.get("field") or p["name"]
            core = norm(re.sub(r"\(.*?\)", " ", label))
            if p.get("options") or p.get("type") == "date" or len(core) < 3 or re.search(
                    rf"\b{re.escape(core)}\b[^;:]*?:", norm(t)) or any(x.startswith(label + ":") for x in extra):
                continue
            vm = re.search(rf"\b{re.escape(core)}\s*(?:\([^)]*\)\s*)?(?:is|=|of|:)?\s*(-?\d+(?:[.,]\d+)?)\b", norm(t))
            if vm:
                extra.append(f"{re.sub(r'\s*\([^)]*\)', '', label).strip()}: {vm.group(1)}")
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
            # 23.11.1: a vehicle or similar resource is chosen only when the person said "any available"; otherwise
            # it is missing information and the person is asked
            if p.get("options") and RESOURCE_FIELD.search(label) and p.get("type") == "select" and re.search(
                    rf"\b(any|an?)\s+(available|free)\b|\bany\s+{re.escape(norm(label))}\b", norm(t)):
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

    # ---- 23.11.1: retrieval check, suggestions, the simple preview ----------------------------------------------

    def verify_retrieval(self, app_id: str) -> dict[str, Any]:
        """Proves that what was saved can be used: every usable task is found again by the same router a command
        goes through (from its own name), and every learned page by a navigation request. Nothing is executed."""
        app = self.kb.application(app_id)
        checks: list[dict[str, Any]] = []
        for wf in self._workflows(app["id"]):
            d = wf["data"] or {}
            title = d.get("title") or wf["key"]
            it = classify(title)
            got, why = self._choose(app, it, title) if it.is_operation else (None, "not an operation name")
            checks.append({"kind": "task", "name": title, "ok": bool(got and got["key"] == wf["key"]),
                           "found": got["key"] if got else None, "usable": self._usable(app, {"workflow": wf["key"]}),
                           **({"problem": why} if not got else {})})
        for sc in self._screens(app["id"])[:40]:
            if sc.get("type") in ("sign-in", "verification") or not sc.get("name"):
                continue
            it = Intent("navigate", target=sc["name"], words=[w for w in re.findall(r"[a-z0-9]+", norm(sc["name"]))
                                                              if len(w) > 1])
            page = navigation_target(it, app, self._screens(app["id"]), [])
            checks.append({"kind": "page", "name": sc["name"], "ok": bool(page and page["kind"] == "page"),
                           "found": page["title"] if page else None})
        bad = [c for c in checks if not c["ok"]]
        return {"checked": len(checks), "ok": not bad, "failed": bad, "checks": checks,
                "tasks": sum(1 for c in checks if c["kind"] == "task" and c["ok"]),
                "pages": sum(1 for c in checks if c["kind"] == "page" and c["ok"])}

    def suggestions(self, app_id: str, *, page_url: str | None = None, can_act: bool = True,
                    conversation: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
        """What the person can usefully ask now, built only from what was learned for this application: the page
        open in the learning browser, tasks the configured accounts may perform, records and their statuses, and
        the person's recent requests. Nothing that was not learned, and no task without an account allowed to do
        it. View-only SkyGuard users get only reading and navigation suggestions."""
        app = self.kb.application(app_id)
        model = self.agent.understanding.model(app["id"]) or {}
        accounts = self.kb.accounts(app["id"])
        roles_with_account = {norm(a.get("verified_role") or a.get("role_key")) for a in accounts}
        out: list[dict[str, Any]] = []

        def add(text: str, kind: str, why: str) -> None:
            if text and all(norm(x["text"]) != norm(text) for x in out):
                out.append({"text": text, "kind": kind, "why": why})

        def permitted(wf: dict[str, Any]) -> bool:
            roles = {norm(r) for r in (wf["data"] or {}).get("roles") or [] if r}
            return can_act and bool(accounts) and (not roles or bool(roles & roles_with_account)) and \
                self._usable(app, {"workflow": wf["key"]})

        here = None
        if page_url:
            from .understanding import path_template
            from urllib.parse import urlsplit as _us
            path = path_template(page_url)
            here = next((sc for sc in model.get("screens") or [] if sc.get("path") == path), None)
            if path in ("/", "") and not here:
                here = None
        workflows = self._workflows(app["id"])
        lcs = {norm(lc.get("entity")): lc for lc in model.get("lifecycles") or []}
        records = self._records(app["id"], None)
        ent_here = norm((here or {}).get("entity"))
        # 1. the page the person is on
        if here:
            if (here.get("tables") or here.get("type") == "list") and ent_here:
                add(f"Show today's {plural_of(ent_here)}", "read", f"you are on {here.get('name')}")
                lc = lcs.get(ent_here)
                if lc and lc.get("initial"):
                    add(f"Show {str(lc['initial'].get('status') or '').lower()} {plural_of(ent_here)}", "read",
                        "the first status of the lifecycle")
            for wf in workflows:
                d = wf["data"] or {}
                if norm(d.get("entity")) == ent_here and permitted(wf):
                    add(self._task_phrase(wf, lcs), "task", f"a task learned on {here.get('name')}")
        # 2. records waiting for a step the accounts may perform
        for wf in workflows:
            if not permitted(wf):
                continue
            d = wf["data"] or {}
            req = self._requires(app["id"], wf) if _is_row_action(wf) else None
            ent = norm(d.get("entity"))
            if req and any(norm(r.get("status")) == norm(req) and norm(r.get("entity")) == ent
                           for r in records.values()):
                add(f"Show {req.lower()} {plural_of(ent)}", "read",
                    f"{plural_of(ent)} waiting for {norm(d.get('operation') or 'the next step')}")
                add(f"{(d.get('operation') or 'process').capitalize()} all {req.lower()} {plural_of(ent)}", "task",
                    f"needs status {req}")
        # 3. tasks the accounts may perform
        for wf in workflows:
            if permitted(wf) and workflow_category(wf) == "create":
                add(self._task_phrase(wf, lcs), "task", "a learned task")
        # 4. the person's recent requests in this application
        if conversation:
            for h in reversed(self.agent.history(conversation, limit=40)):
                if h["speaker"] == "user" and (h.get("data") or {}).get("app_id") == app["id"] and \
                        not h["text"].startswith("[message withheld") and len(h["text"]) < 120:
                    add(h["text"], "recent", "you asked this before")
                if sum(1 for x in out if x["kind"] == "recent") >= 2:
                    break
        # 5. places and explanations
        for sc in sorted(model.get("screens") or [], key=lambda x: -(x.get("visits") or 0))[:3]:
            if sc.get("type") not in ("sign-in", "verification") and sc is not here and sc.get("name"):
                add(f"Open the {sc['name']} page", "navigate", "a page you used")
        for ent, lc in list(lcs.items())[:1]:
            sts = lc.get("statuses") or []
            if len(sts) >= 2 and ent:
                add(f"What happens after a {ent} is {str(sts[1]).lower()}?", "explain", "the learned status flow")
        return out[:limit]

    @staticmethod
    def _task_phrase(wf: dict[str, Any], lcs: dict[str, Any]) -> str:
        d = wf["data"] or {}
        op, ent = norm(d.get("operation") or "do"), norm(d.get("entity"))
        if workflow_category(wf) == "create":
            lead = "Create a new" if op in ("create", "add", "save", "store", "submit") else f"{op.capitalize()} a new"
            return f"{lead} {ent or 'record'}"
        return f"{op.capitalize()} {('a ' + ent) if ent else 'a record'}"

    def simple_view(self, run_id: str) -> dict[str, Any]:
        """The short preview the person reads (Action, Application, the values, Role, Status) and what can be done
        with it. ``can_confirm`` is false whenever no record can run; the page then offers Edit request / Provide
        missing data / Cancel instead of Confirm. The same rule is enforced again when Confirm arrives."""
        view = self.agent.engine.view(run_id)
        run, plan, items = view["run"], view["plan"], view["items"]
        p = run.get("preview") or {}
        stages = plan.get("stages") or []
        wf = self.kb.entry(stages[0]["workflow_entry_id"]) if stages else None
        params = planner.workflow_params(wf) if wf else []
        label = {pp["name"]: pp.get("field") or pp["name"] for pp in params}
        recs = p.get("records") or []
        blocked = [f["message"] for r in recs for f in r.get("findings") or [] if f.get("enforced")
                   or f.get("severity") == "error"]
        missing = sorted({label.get(f.get("field"), f.get("field")) for r in recs for f in r.get("findings") or []
                          if f.get("type") == "required" and (f.get("enforced") or f.get("severity") == "error")
                          and f.get("field")})
        ready = int((p.get("totals") or {}).get("ready") or 0)
        status_text = {"planning": "Preparing the preview", "inspecting": "Checking the application (read-only)",
                       "awaiting_confirmation": "Ready for confirmation" if ready else "Cannot run: see below",
                       "running": "Running", "paused": "Waiting for you", "completed": "Completed",
                       "partial": "Partly completed", "failed": "Failed", "stopped": "Cancelled",
                       "cancelled": "Cancelled"}.get(run["status"], run["status"])
        stage0 = {**(stages[0] if stages else {}), **((p.get("stages") or [{}])[0] if p else {})}
        fields = [{label.get(k, k).replace("_", " "): v for k, v in (r.get("values") or {}).items()} for r in recs]
        result = self.outcome_text(run_id) if run["status"] in ("completed", "partial", "failed") else None
        return {"run_id": run_id, "action": " → ".join(s.get("title") or "" for s in stages),
                "application": plan.get("app_name"), "records": fields, "count": len(recs) or len(items),
                "role": stage0.get("role_verified") or stage0.get("role_claimed") or None,
                "account": stage0.get("account"), "status": run["status"], "status_text": status_text,
                "ready": ready, "blocked": int((p.get("totals") or {}).get("blocked") or 0),
                "can_confirm": run["status"] == "awaiting_confirmation" and ready > 0,
                "consequential": run_is_consequential(view), "missing": missing,
                "blocked_reasons": sorted(set(blocked))[:10], "preview_hash": run.get("preview_hash"),
                "next_actions": (["confirm", "edit", "cancel"] if run["status"] == "awaiting_confirmation" and ready
                                 else ["provide_missing", "edit", "cancel"] if run["status"] == "awaiting_confirmation"
                                 else []),
                "result": result,
                "request_text": (self.agent.engine.run(run_id).get("request") or {}).get("context", {}).get("command")}

    def confirm(self, run_id: str, preview_hash: str, *, acknowledge: bool, by: str | None,
                interface: str | None = None, app_id: str | None = None) -> dict[str, Any]:
        """The one confirmation, validated on the server (SkyGuard and the extension's companion alike): the plan
        must belong to the selected application and to the request it was made for, at least one record must be
        able to run, and a consequential operation needs the person's acknowledgement as well as the preview hash
        (which the engine checks against the current preview, so a stale preview is refused)."""
        run = self.agent.engine.run(run_id)
        ctx = (run.get("request") or {}).get("context") or {}
        stage_wf = ((run.get("plan") or {}).get("stages") or [{}])[0].get("workflow")
        if app_id and app_id != run["application_id"]:
            raise AgentError("PLAN_OTHER_APPLICATION", "This preview belongs to another application than the one "
                             "selected. Nothing was executed.", 409)
        if ctx and (ctx.get("app_id") not in (None, run["application_id"])
                    or (ctx.get("workflow") and ctx["workflow"] != stage_wf)
                    or (ctx.get("intent") and not Intent(**ctx["intent"]).is_operation)):
            raise AgentError("PLAN_UNRELATED", "This preview does not match the request it was made for. Nothing "
                             "was executed; send the request again.", 409)
        if run.get("status") == "awaiting_confirmation" and \
                not ((run.get("preview") or {}).get("totals") or {}).get("ready"):
            sv = self.simple_view(run_id)
            why = ("Missing information: " + ", ".join(sv["missing"]) + "." if sv["missing"] else
                   "; ".join(sv["blocked_reasons"][:3]) or "Every record is blocked.")
            raise AgentError("NOTHING_TO_RUN", f"Nothing can run, so there is nothing to confirm. {why} Use Provide "
                             "missing data or Edit request.", 409)
        if run_is_consequential(run) and not acknowledge:
            raise AgentError("ACKNOWLEDGE_REQUIRED", "This operation changes records (for example creates, approves, "
                             "authorizes, cancels or deletes them). Tick the box after reading the preview.", 409)
        view = self.agent.engine.confirm(run_id, preview_hash, by=by, interface=interface)
        self.agent.audit.record("run.confirmed_simple", actor=by, interface=interface, target=run_id,
                                details={"acknowledged": bool(acknowledge), "context": bool(ctx)})
        return view

    def outcome_text(self, run_id: str) -> str:
        """The verified result in the application's own words: the record number and status it reported. Nothing
        is invented; a value the application did not show is not mentioned."""
        view = self.agent.engine.view(run_id)
        plan, items = view["plan"], view["items"]
        title = " → ".join(s.get("title") or "" for s in plan.get("stages") or [])
        parts = []
        for it in items:
            ev = {}
            for v in ((it.get("result") or {}).get("evidence") or {}).values():
                ev.update({k: v[k] for k in ("record_key", "record_status") if isinstance(v, dict) and v.get(k)})
            if it["status"] == "confirmed_success":
                bits = [f"{title}: done, confirmed by the application"]
                if ev.get("record_key"):
                    bits.append(f"number {ev['record_key']}")
                if ev.get("record_status"):
                    bits.append(f"status {ev['record_status']}")
                parts.append(", ".join(bits) + ".")
            elif it["status"] == "confirmed_failure":
                parts.append(f"{title}: not done. {(it.get('result') or {}).get('reason') or ''}".strip())
            elif it["status"] == "unknown":
                parts.append(f"{title}: not confirmed by the application; check it there.")
            elif it["status"] == "skipped":
                parts.append(f"{title}: skipped ({(it.get('result') or {}).get('reason') or 'not run'}).")
        return " ".join(parts) or "Nothing was executed."

    # ---- tasks & history ---------------------------------------------------------------------------------------

    def tasks(self, app_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for r in self.agent.engine.runs(limit=limit, app_id=app_id):
            items = self.agent.engine.items(r["id"])
            counts: dict[str, int] = {}
            for i in items:
                bucket = _bucket(i["status"])
                counts[bucket] = counts.get(bucket, 0) + 1
            plan = self.agent.engine.run(r["id"])["plan"]
            out.append({"id": r["id"], "application_id": r["application_id"], "status": r["status"],
                        "title": " → ".join(s["title"] for s in plan.get("stages") or []) or r["id"],
                        "kind": plan.get("kind") or "task", "message": (r.get("summary") or {}).get("message"),
                        "created_by": r["created_by"], "created_at": r["created_at"],
                        "finished_at": r["finished_at"], "records": len(items), "counts": counts,
                        "result": self.outcome_text(r["id"]) if r["status"] in ("completed", "partial", "failed")
                        and plan.get("kind") != "discovery" else None})
        return out


def run_is_consequential(run: dict[str, Any]) -> bool:
    """A run whose task changes the state of records (approve, authorize, cancel, delete ...): its confirmation needs
    the person's explicit acknowledgement as well as the preview hash, in SkyGuard and in the extension alike."""
    return any(CONSEQUENTIAL.search(f"{s.get('workflow')} {s.get('title')}") for s in (run.get("plan") or {}).get("stages")
               or [])


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


def plural_of(word: str) -> str:
    from .understanding import plural
    return plural(word) if word else "records"


def _fingerprint(app_id: str, wf_key: str, text: str) -> str:
    return digest([app_id, wf_key, " ".join(norm(text).split())])[:32]


def _conv_id() -> str:
    from .util import new_id
    return new_id("conv")
