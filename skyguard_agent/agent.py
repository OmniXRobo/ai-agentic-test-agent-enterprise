"""OperationsAgent: the one agent both interfaces talk to.

SkyGuard QA constructs it with its own database folder, the Windows Credential Manager store and its AI gateway; the
standalone companion constructs it with DPAPI and a direct link to the local llama-server. Same code, same knowledge
format, same rules, so a workflow learned in one interface runs in the other (export / import, or a shared database
when the companion is set to use SkyGuard's).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import AGENT_NAME, __version__, planner, secrets_guard
from .assistant import Assistant, canonical, is_question
from .audit import Audit
from .executor import Engine
from .journal import Journal
from .knowledge import KnowledgeBase
from .learning import Learning
from .llm import NoLLM
from .store import Store
from .understanding import Understanding
from .util import AgentError, dumps, loads, new_id, norm, now_iso
from .vault import Vault

HELP = ("I learn your applications by watching you use them, and then answer questions about them and operate "
        "them through their normal screens. Turn on 'Learn while I browse' and just work as usual; I work out the "
        "screens, tasks, statuses, rules and roles myself. Then ask: 'how do I create a trip', 'who approves trips', "
        "'what happens after a trip is approved', 'what rules apply to trip number', 'what is the status of T100'. "
        "Or try: 'discover Transport', 'learn Create trip', 'create 3 trips on 2026-12-01 from 06:00 every 30 minutes, "
        "routes: Dubai to Abu Dhabi, Abu Dhabi to Dubai, buses: BUS-1, BUS-2, drivers: available, trip numbers "
        "starting at DXB100', 'status', 'what do you know about Transport'. Every operation shows a preview first "
        "and runs only after you confirm it. Never type passwords or codes here: use Accounts.")


class OperationsAgent:
    def __init__(self, data_dir: Path | str, *, vault: Vault, llm: Any = None,
                 transcriber: Callable[[bytes, str], str] | None = None, interface: str = "skyguard",
                 db_path: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(db_path or self.data_dir / "knowledge.sqlite3")
        self.vault = vault
        self.audit = Audit(self.store, known_secrets=vault.known_values)
        self.kb = KnowledgeBase(self.store, vault, self.audit)
        self.engine = Engine(self.store, self.kb, self.audit)
        self.llm = llm or NoLLM()
        self.learning = Learning(self.kb, self.engine, None if isinstance(self.llm, NoLLM) else self.llm)
        self.engine.learning = self.learning
        # 23.7.0: passive learning (journal -> understanding) and chat that answers from what was understood
        self.journal = Journal(self.kb, known_secrets=vault.known_values)
        self.understanding = Understanding(self.kb, self.journal, None if isinstance(self.llm, NoLLM) else self.llm)
        self.assistant = Assistant(self.kb, self.understanding, None if isinstance(self.llm, NoLLM) else self.llm)
        self.transcriber = transcriber
        self.interface = interface
        self.recovered = self.engine.recover_interrupted()

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def set_llm(self, llm: Any) -> None:
        """The model is replaceable at any time; knowledge lives in the database, not in the model."""
        self.llm = llm or NoLLM()
        self.learning.llm = None if isinstance(self.llm, NoLLM) else self.llm
        self.understanding.llm = self.learning.llm
        self.assistant.llm = self.learning.llm

    # ---- 23.7.0: learn while the person browses ---------------------------------------------------------------

    DIGEST_EVERY = 25

    def observe(self, app_id: str, session_id: str | None, events: list[dict[str, Any]] | None,
                snapshot: dict[str, Any] | None, *, by: str | None = None, interface: str | None = None,
                final: bool = False, use_ai: bool = True) -> dict[str, Any]:
        """Record what the person did and saw; rebuild the understanding every few observations."""
        app = self.kb.application(app_id)
        sid = self.journal.ensure_session(app["id"], session_id, interface=interface or self.interface, by=by)
        stored = self.journal.record(app["id"], sid, events or [], snapshot)
        out: dict[str, Any] = {"session_id": sid, **stored, "digested": False}
        if final:
            self.journal.end_session(sid, by=by)
        if final or self.understanding.pending(app["id"]) >= self.DIGEST_EVERY:
            res = self.understanding.digest(app["id"], by="passive-learning", use_ai=use_ai)
            out.update(digested=True, changes=res["changes"])
        return out

    def understanding_view(self, app_id: str) -> dict[str, Any]:
        app = self.kb.application(app_id)
        return {"application": {"id": app["id"], "name": app["name"]}, "model": self.understanding.model(app["id"]),
                "journal": self.journal.stats(app["id"]), "pending": self.understanding.pending(app["id"])}

    def info(self) -> dict[str, Any]:
        return {"agent": AGENT_NAME, "version": __version__, "interface": self.interface,
                "knowledge_db": str(self.store.path), "schema": self.store.schema_version,
                "vault": self.vault.backend_name, "vault_available": self.vault.available,
                "llm": getattr(self.llm, "name", "none"), "recovered_runs": self.recovered}

    # ---- chat ------------------------------------------------------------------------------------------------

    def _known_secrets(self) -> list[str]:
        return self.vault.known_values()

    def _remember(self, conversation: str, speaker: str, text: str, user: str | None, interface: str,
                  data: dict[str, Any] | None = None) -> None:
        self.store.execute("INSERT INTO chat (conversation_id, at, speaker, text, interface, user, data) VALUES "
                           "(?,?,?,?,?,?,?)", (conversation, now_iso(), speaker,
                                               secrets_guard.redact_text(text, self._known_secrets())[:4000],
                                               interface, user, dumps(data or {})))

    def history(self, conversation: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM chat WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                                (conversation, max(1, min(limit, 500))))
        for r in rows:
            r["data"] = loads(r["data"])
        return list(reversed(rows))

    def chat(self, text: str, *, conversation: str | None = None, user: str | None = None,
             interface: str | None = None, app_id: str | None = None, source: str = "typed") -> dict[str, Any]:
        interface = interface or self.interface
        conversation = conversation or new_id("conv")
        text = (text or "").strip()[:4000]
        if not text:
            raise AgentError("EMPTY", "Type or say what you want me to do.")
        check = secrets_guard.inspect(text, self._known_secrets())
        if check.contains_secret:
            # nothing of the message is stored, logged, indexed or sent to the model
            self._remember(conversation, "user", "[message withheld: it contained a credential]", user, interface,
                           {"intercepted": True, "source": source})
            self.audit.record("chat.secret_intercepted", actor=user, interface=interface,
                              details={"kinds": sorted(set(check.kinds)), "source": source})
            reply = {"type": "secret_intercepted", "text": check.reply, "open": "accounts"}
            self._remember(conversation, "agent", reply["text"], user, interface, {"type": reply["type"]})
            return {"conversation": conversation, "reply": reply}
        self._remember(conversation, "user", text, user, interface, {"source": source})
        try:
            reply = self._interpret(text, user=user, interface=interface, app_id=app_id)
        except AgentError as exc:
            reply = {"type": "error", "text": exc.message, "code": exc.code}
        self._remember(conversation, "agent", reply.get("text", ""), user, interface,
                       {k: v for k, v in reply.items() if k in ("type", "run_id", "app_id")})
        return {"conversation": conversation, "reply": reply}

    def _app_for(self, text: str, app_id: str | None) -> dict[str, Any] | None:
        if app_id:
            return self.kb.application(app_id)
        apps = self.kb.applications()
        t = norm(text)
        named = [a for a in apps if norm(a["name"]) and re.search(rf"\b{re.escape(norm(a['name']))}\b", t)]
        if named:
            return named[0]
        return apps[0] if len(apps) == 1 else None

    def _interpret(self, text: str, *, user: str | None, interface: str, app_id: str | None) -> dict[str, Any]:
        t = norm(text)
        if t in ("help", "?", "what can you do", "what can you do?"):
            return {"type": "help", "text": HELP}
        app = self._app_for(text, app_id)
        if re.match(r"^(status|what are you doing|progress)\b", t):
            runs = self.engine.runs(limit=5, app_id=app["id"] if app else None)
            if not runs:
                return {"type": "status", "text": "No executions yet."}
            lines = [f"{r['id']}: {r['status']}" + (f" - {r['summary'].get('message')}" if r["summary"].get("message")
                                                    else "") for r in runs]
            return {"type": "status", "text": "Latest executions: " + " | ".join(lines), "runs": runs}
        if not app:
            apps = self.kb.applications()
            if not apps:
                return {"type": "need_app", "text": "No application is registered yet. Register one (name and URL) "
                                                    "under Applications, then add its accounts."}
            return {"type": "need_app", "text": "Which application? " + ", ".join(a["name"] for a in apps),
                    "applications": [{"id": a["id"], "name": a["name"]} for a in apps]}
        if re.search(r"\b(watch me|learn while i (browse|work)|watch (what|how) i|learn by watching|start watching|"
                     r"learn automatically|observe me)\b", t):
            return {"type": "suggest_watch", "app_id": app["id"],
                    "text": f"Press 'Learn while I browse' (Learning tab, or turn on automatic learning in the browser "
                            f"extension) and use {app['name']} as you normally do. I work out the screens, tasks, "
                            "statuses, rules and roles myself; you do not need to explain anything. Passwords and "
                            "codes are never captured."}
        if is_question(text) or re.match(r"^(what do you know|knowledge|show knowledge|what have you learned)\b", t):
            ans = self.assistant.answer(app, text)
            if ans:
                return ans
        if re.match(r"^(discover|explore|scan|learn the application)\b", t):
            return {"type": "suggest_discovery", "app_id": app["id"],
                    "text": f"Automatic discovery opens {app['name']}, signs in with the account you choose and "
                            "inspects the screens read-only (nothing is submitted). Choose the account and press "
                            "Start discovery.", "accounts": [{"id": a["id"], "label": a["label"]}
                                                             for a in self.kb.accounts(app["id"])]}
        m = re.match(r"^(learn|teach|record|demonstrate)\s+(?:you\s+)?(?:how to\s+)?(.+)$", t)
        if m:
            return {"type": "suggest_guided", "app_id": app["id"], "title": text.split(None, 1)[1][:80],
                    "text": f"Start guided learning, then perform '{m.group(2)}' in {app['name']} yourself. I watch "
                            "your clicks and entries (passwords and codes are never captured) and build the workflow "
                            "for you to approve."}
        if re.match(r"^(what do you know|knowledge|show knowledge|what have you learned)\b", t):
            items = self.kb.items(app["id"])
            by_kind: dict[str, int] = {}
            for e in items:
                by_kind[f"{e['kind']} ({e['state_label']})"] = by_kind.get(f"{e['kind']} ({e['state_label']})", 0) + 1
            return {"type": "knowledge", "app_id": app["id"],
                    "text": f"{app['name']}: " + (", ".join(f"{n} {k}" for k, n in sorted(by_kind.items()))
                                                  or "nothing learned yet") + "."}
        return self._operation(text, app, user=user, interface=interface)

    def _operation(self, text: str, app: dict[str, Any], *, user: str | None, interface: str) -> dict[str, Any]:
        workflows = self.kb.approved_items(app["id"], "workflow")
        learned = self._learned_match(app, text, workflows)
        if learned:
            return learned
        if not workflows:
            return {"type": "no_workflows", "app_id": app["id"],
                    "text": f"I have no approved workflows for {app['name']} yet. Turn on 'Learn while I browse' and "
                            "do the task once yourself (or run discovery / guided learning), then approve it in "
                            "Knowledge."}
        accounts = self.kb.accounts(app["id"])
        wf = planner.match_workflow(canonical(text, nouns=False), workflows) or planner.match_workflow(text, workflows)
        parsed: dict[str, Any] | None = None
        dropped: list[str] = []
        if wf:
            parsed = planner.parse_structured(text, wf)
        if not isinstance(self.llm, NoLLM):
            try:
                proposal = planner.parse_with_llm(self.llm, text, workflows, accounts)
                cand = next((w for w in workflows if w["key"] == proposal.get("workflow")), None)
                if cand and (wf is None or cand["key"] == wf["key"]):
                    wf = cand
                    grounded, dropped = planner.ground(proposal, text, wf)
                    base = parsed if parsed and parsed.get("fixed") is not None and wf["key"] == cand["key"] else {}
                    parsed = _merge(base or {}, grounded)
            except AgentError:
                pass  # model offline: the deterministic parse stands
            except (ValueError, TypeError, KeyError):
                pass
        if not wf:
            return {"type": "clarify", "app_id": app["id"],
                    "text": "Which operation do you mean? I know: " + ", ".join(w["data"].get("title") or w["key"]
                                                                               for w in workflows)}
        account = planner.match_account(text, accounts)
        if not account:
            allowed = {norm(r) for r in wf["data"].get("roles", [])}
            fitting = [a for a in accounts if not allowed or norm(a.get("verified_role") or a.get("role_key")) in allowed]
            account = fitting[0] if len(fitting) == 1 else None
        if not account:
            return {"type": "clarify", "app_id": app["id"], "workflow": wf["key"],
                    "text": f"Which account should perform '{wf['data'].get('title') or wf['key']}'? "
                            + ", ".join(f"{a['label']} ({a.get('role_key') or 'role not set'})" for a in accounts),
                    "accounts": [{"id": a["id"], "label": a["label"]} for a in accounts]}
        rules = planner.rules_for(self.kb, app["id"], wf)
        options = planner.field_options(self.kb, app["id"], wf)
        records = planner.expand(parsed or {}, wf, options=options, rules=rules)
        missing = sorted({p["name"] for p in planner.workflow_params(wf) if p.get("required")
                          for r in records if not r.get(p["name"])})
        warnings = [f"Ignored values the model proposed that you did not say: {', '.join(dropped[:5])}."] if dropped else []
        if missing and len(records) == 1 and not any(v for k, v in records[0].items() if not k.startswith("_")):
            return {"type": "clarify", "app_id": app["id"], "workflow": wf["key"],
                    "text": f"Tell me the values for: {', '.join(p.get('field') or p['name'] for p in planner.workflow_params(wf))}."}
        view = self.engine.create(app_id=app["id"], stages=[{"workflow": wf["key"], "account_id": account["id"]}],
                                  records=records, interface=interface, by=user,
                                  request={"text": secrets_guard.redact_text(text), "parsed": parsed},
                                  warnings=warnings)
        run = view["run"]
        if run["status"] == "awaiting_confirmation":
            totals = run["preview"]["totals"]
            msg = (f"Preview ready: {wf['data'].get('title') or wf['key']} as {account['label']}, "
                   f"{totals['records']} record(s), {totals['ready']} ready, {totals['blocked']} blocked by rules. "
                   "Review it and press Confirm to execute.")
        else:
            msg = (f"Planned {len(records)} record(s) for {wf['data'].get('title') or wf['key']}. I will first sign "
                   f"in as {account['label']} and inspect the application (read-only) to check duplicates and "
                   "conflicts; then the preview appears for your confirmation.")
        return {"type": "run", "run_id": run["id"], "app_id": app["id"], "text": msg, "status": run["status"]}

    def _learned_match(self, app: dict[str, Any], text: str, approved: list[dict[str, Any]]) -> dict[str, Any] | None:
        """A request for a task that was learned by watching but is not approved yet: offer the one approval."""
        if approved and (planner.match_workflow(canonical(text, nouns=False), approved) or planner.match_workflow(text, approved)):
            return None
        learned = [e for e in self.kb.items(app["id"], "workflow")
                   if e["state"] in ("discovered", "awaiting_approval", "needs_revalidation")
                   and not (e["data"] or {}).get("draft")]
        if not learned:
            return None
        wf = planner.match_workflow(canonical(text, nouns=False), learned) or planner.match_workflow(text, learned)
        if not wf:
            return None
        d = wf["data"]
        ev = d.get("evidence") or {}
        steps = [s for s in d.get("steps") or [] if s.get("action") != "navigate"]
        return {"type": "needs_approval", "app_id": app["id"], "entry_id": wf["id"], "workflow": wf["key"],
                "text": f"I learned '{d.get('title') or wf['key']}' by watching"
                        + (f" (done {ev.get('successes')} time(s))" if ev.get("successes") else "")
                        + f": {len(d.get('parameters') or [])} field(s), {len(steps)} step(s)"
                        + (f", done by {', '.join(r.title() for r in d.get('roles') or [])}" if d.get("roles") else "")
                        + ". It needs one approval before I may run it. Press 'Approve and run' (or review it in "
                          "Knowledge) and I will prepare the preview."}

    # ---- voice -------------------------------------------------------------------------------------------------

    def transcribe(self, audio: bytes, content_type: str = "audio/wav") -> str:
        """Local speech-to-text. The text is returned for the user to edit; it is never executed directly."""
        if not self.transcriber:
            raise AgentError("VOICE_UNAVAILABLE", "No local speech recognizer is configured.", 503)
        if len(audio) > 10 * 1024 * 1024:
            raise AgentError("AUDIO_TOO_LARGE", "The recording is too long.", 413)
        text = self.transcriber(audio, content_type)
        return (text or "").strip()

    def close(self) -> None:
        self.store.close()


def _merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict):
            out[k] = {**(a.get(k) or {}), **v}
        elif isinstance(v, list) and k == "auto":
            out[k] = sorted(set(a.get(k) or []) | set(v))
        elif v not in (None, [], {}):
            out[k] = v
    return out
