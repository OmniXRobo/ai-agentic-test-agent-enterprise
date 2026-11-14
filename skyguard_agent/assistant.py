"""Chat that understands the application it learned (23.7.0).

Questions are answered from the application model built by passive learning (understanding.py), read-only and at
once, without approvals: *how do I create a trip*, *who can approve trips*, *what happens after a trip is approved*,
*what statuses can a trip have*, *what rules apply to trip number*, *what can a manager do*, *where is the trip list*,
*what is the status of T100*, *how many trips are planned*, *what have you learned*.

Each answer is built deterministically from what was observed and says how often it was seen; nothing is invented.
When a question does not fit a known shape, the facts most related to it are retrieved (BM25 over sentences generated
from the model, with business synonyms) and, if the local model is available, it phrases an answer *only* from those
facts (page-derived text goes in as untrusted data). Requests to *do* something still go to the planner, preview and
confirmation; synonyms (add / new / make / register = create, authorise = authorize ...) are understood there too.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .guard import SYSTEM_RULES, wrap_untrusted
from .understanding import plural, singular, tokens
from .util import dumps, norm

SYNONYMS = {
    "create": ("add", "new", "make", "register", "enter", "raise", "open a new", "set up", "insert", "book", "schedule",
               "plan"),
    "update": ("edit", "change", "modify", "amend", "correct"),
    "delete": ("remove", "erase", "drop"),
    "approve": ("approval", "sign off", "okay", "accept the request"),
    "authorize": ("authorise", "authorisation", "authorization", "authorized", "authorised"),
    "cancel": ("void", "call off", "abort"),
    "status": ("state", "stage", "lifecycle", "life cycle", "progress", "statuses", "states", "stages"),
    "role": ("roles", "profile", "user type", "user types", "permission", "permissions", "access", "privilege",
             "privileges", "rights"),
    "rule": ("rules", "validation", "validations", "constraint", "constraints", "condition", "conditions", "policy",
             "policies", "check", "checks", "restriction", "restrictions"),
    "screen": ("screens", "page", "pages", "form", "forms", "window", "menu", "tab"),
}
_VERB_FORMS = {"created": "create", "creating": "create", "creates": "create", "approved": "approve",
               "approving": "approve", "approves": "approve", "authorizes": "authorize", "authorizing": "authorize",
               "authorises": "authorize", "authorising": "authorize", "authorised": "authorized", "authorise": "authorize",
               "updated": "update", "updating": "update", "deleted": "delete", "deleting": "delete",
               "accepted": "accept", "accepting": "accept", "rejected": "reject", "rejecting": "reject",
               "cancelled": "cancel", "canceled": "cancel", "booked": "book", "submitted": "submit"}

QUESTION_START = re.compile(r"^\s*(how|what|what's|whats|which|who|whom|whose|where|when|why|can|could|does|do|did|is|"
                            r"are|was|were|should|explain|tell|show|list|describe|give|summari[sz]e|any)\b", re.I)
COMMAND_START = re.compile(r"^\s*(please\s+)?(can|could|would|will)\s+you\s+(please\s+)?(create|add|make|approve|"
                           r"authori[sz]e|accept|reject|update|edit|delete|remove|cancel|book|register|enter|submit|"
                           r"assign|run|do|execute)\b", re.I)


_NOUNS = ("status", "role", "rule", "screen")


def canonical(text: str, nouns: bool = True) -> str:
    """The request with business synonyms replaced by the words learned flows use (for matching only).
    ``nouns=False`` keeps words such as check / page / access as they are (used to match a task to perform)."""
    t = " " + norm(text) + " "
    for word, form in _VERB_FORMS.items():
        t = re.sub(rf"\b{word}\b", form, t)
    for canon, alts in SYNONYMS.items():
        if not nouns and canon in _NOUNS:
            continue
        for a in sorted(alts, key=len, reverse=True):
            t = re.sub(rf"\b{re.escape(a)}\b", canon, t)
    return " ".join(t.split())


def is_question(text: str) -> bool:
    t = norm(text)
    if COMMAND_START.match(t):
        return False
    return t.endswith("?") or bool(QUESTION_START.match(t))


_RULEISH = re.compile(r"\b(double.?book\w*|overlap\w*|twice|same time|clash\w*|duplicate\w*|unique|past date|"
                      r"in the past|future)\b")


def _list(items: list[str], limit: int = 8) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    more = f" and {len(items) - limit} more" if len(items) > limit else ""
    items = items[:limit]
    return (", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else items[0]) + more


def _times(n: int) -> str:
    return "once" if n == 1 else "twice" if n == 2 else f"{n} times"


class Assistant:
    def __init__(self, kb: Any, understanding: Any, llm: Any = None) -> None:
        self.kb = kb
        self.understanding = understanding
        self.llm = llm

    # ---- entry point ---------------------------------------------------------------------------------------------

    def answer(self, app: dict[str, Any], text: str) -> dict[str, Any] | None:
        model = self.understanding.model(app["id"])
        if not model or not (model.get("screens") or model.get("flows")):
            return None
        q = canonical(text)
        name = app["name"]
        for handler in (self._summary, self._record_status, self._count, self._lifecycle, self._who, self._role_can,
                        self._how, self._rules, self._fields, self._screens, self._roles_list):
            try:
                out = handler(model, q, text, name)
            except (KeyError, ValueError, TypeError, IndexError):
                out = None
            if out:
                return {"type": "answer", "app_id": app["id"], "text": out["text"], "topic": out.get("topic"),
                        "grounded": True}
        return self._retrieve(model, q, text, app)

    # ---- matching helpers ----------------------------------------------------------------------------------------

    @staticmethod
    def _entity(model: dict[str, Any], q: str) -> dict[str, Any] | None:
        best, score = None, 0
        for e in model.get("entities") or []:
            n = e["name"]
            if re.search(rf"\b({re.escape(n)}|{re.escape(plural(n))})\b", q):
                s = 2 + len(e["flows"])
                if s > score:
                    best, score = e, s
        return best

    @staticmethod
    def _flow(model: dict[str, Any], q: str, *, prefer_kind: str | None = None) -> dict[str, Any] | None:
        qt = set(tokens(q))
        best, best_score = None, 0.0
        for f in model.get("flows") or []:
            score = 0.0
            op, ent = norm(f.get("operation")), norm(f.get("entity"))
            if op and re.search(rf"\b{re.escape(op)}\w*\b", q):
                score += 2
            if norm(f.get("commit")) and re.search(rf"\b{re.escape(norm(f['commit']))}\b", q):
                score += 1.5
            if ent and re.search(rf"\b({re.escape(ent)}|{re.escape(plural(ent))})\b", q):
                score += 2
            score += 0.3 * len(qt & set(tokens(f["title"])))
            if prefer_kind and f.get("kind") == prefer_kind:
                score += 0.2
            score += min(f.get("successes", 0), 5) * 0.05
            if score > best_score:
                best, best_score = f, score
        return best if best_score >= 2 else None

    @staticmethod
    def _lifecycle_for(model: dict[str, Any], entity: dict[str, Any] | None, q: str) -> dict[str, Any] | None:
        lcs = model.get("lifecycles") or []
        if entity:
            for lc in lcs:
                if lc["entity"] == entity["name"]:
                    return lc
        for lc in lcs:
            if any(re.search(rf"\b{re.escape(norm(s))}\b", q) for s in lc["statuses"]):
                return lc
        return lcs[0] if len(lcs) == 1 else None

    @staticmethod
    def _role(model: dict[str, Any], q: str) -> dict[str, Any] | None:
        for r in sorted(model.get("roles") or [], key=lambda r: -len(r["key"])):
            if re.search(rf"\b{re.escape(r['key'])}s?\b", q):
                return r
        return None

    # ---- answer shapes -------------------------------------------------------------------------------------------

    def _summary(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(what (do|did) you (know|learn\w*|understand)|what have you (learn\w*|understood|seen)|"
                         r"summary|summari[sz]e|overview|what is this (app|application|system)|tell me about)\b", q):
            return None
        st = model.get("stats") or {}
        screens = [s for s in model["screens"] if s["type"] not in ("sign-in", "verification", "confirmation",
                                                                     "refused")]
        lines = [f"From watching {name} ({st.get('pages', 0)} pages, {st.get('actions', 0)} actions) I understand:"]
        if model.get("entities"):
            lines.append("• It manages " + _list([plural(e["name"]) for e in model["entities"][:6]]) + ".")
        if model["flows"]:
            lines.append("• Tasks: " + _list([f"{f['title']} (seen {_times(f['successes'])})" for f in model["flows"]
                                             if f["successes"]]) + ".")
        for lc in model.get("lifecycles") or []:
            lines.append(f"• A {lc['entity']} goes through " + " → ".join(lc["statuses"]) + ".")
        if model.get("roles"):
            lines.append("• Roles: " + _list([r["name"] + (f" ({_list([p['flow'] for p in r['performed']], 3)})"
                                                         if r["performed"] else "") for r in model["roles"]]) + ".")
        if model.get("rules"):
            lines.append(f"• {len(model['rules'])} business rule(s), for example: "
                         + _list([r["text"] for r in model["rules"][:3]]) + ".")
        names = list(dict.fromkeys(s["name"] for s in screens))
        lines.append(f"• {len(names)} main screen(s): " + _list(names, 8) + ".")
        pending = [f["title"] for f in model["flows"] if f.get("usable")]
        if pending:
            lines.append("Ask me how any task works, who does it or what the rules are. To let me perform a task, "
                         "approve its workflow once in Knowledge.")
        return {"text": "\n".join(lines), "topic": "summary"}

    def _record_status(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        records = model.get("records") or {}
        if not records:
            return None
        words = {w.lower() for w in re.findall(r"[\w-]+", text)}
        hits = [(k, v) for k, v in records.items() if k.lower() in words
                and (re.search(r"\d", k) or (k.isupper() and len(k) >= 3))]
        if not hits:
            return None
        parts = []
        for k, v in hits[:5]:
            row = ", ".join(f"{c}: {x}" for c, x in list(v["row"].items())[:7] if x)
            if v.get("status"):
                parts.append(f"{(v.get('entity') or 'Record').capitalize()} {k} was {v['status']} when I last saw it "
                             f"({row}).")
            else:
                parts.append(f"{k}: {row}.")
        lc = next((x for x in model.get("lifecycles") or [] if hits and x["entity"] == hits[0][1].get("entity")), None)
        if lc and hits[0][1].get("status"):
            nxt = [t for t in lc["transitions"] if t["from"] == hits[0][1]["status"]]
            if nxt:
                t = nxt[0]
                parts.append(f"Next step: {t['action'] or 'an action'} → {t['to']}"
                             + (f", done by {_list(t['roles'])}." if t["roles"] else "."))
        parts.append("This is what the application showed while you browsed; I can open the list to check it now.")
        return {"text": " ".join(parts), "topic": "record"}

    def _count(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\bhow many\b", q):
            return None
        ent = self._entity(model, q)
        recs = [v for v in (model.get("records") or {}).values() if not ent or v.get("entity") == ent["name"]]
        if not recs:
            return None
        status = next((s for lc in model.get("lifecycles") or [] for s in lc["statuses"]
                       if re.search(rf"\b{re.escape(norm(s))}\b", norm(text))), None)
        noun = plural(ent["name"]) if ent else "records"
        if status:
            n = sum(1 for r in recs if norm(r.get("status")) == norm(status))
            one = singular(noun) if n == 1 else noun
            return {"text": f"In the lists I saw, {n} {one} {'was' if n == 1 else 'were'} {status} (out of {len(recs)} "
                            "I have seen). "
                            "Counts come from the pages you browsed, not a live query.", "topic": "count"}
        by = Counter(r.get("status") or "no status" for r in recs)
        return {"text": f"I have seen {len(recs)} {noun}: " + _list([f"{n} {s}" for s, n in by.most_common()]) + ".",
                "topic": "count"}

    def _lifecycle(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        after = re.search(r"\b(after|next|then|once|when)\b", q)
        about_status = re.search(r"\bstatus\b", q)
        if not (about_status or after and re.search(r"\b(happen\w*|next|step|then)\b", q)):
            return None
        ent = self._entity(model, q)
        lc = self._lifecycle_for(model, ent, norm(text))
        if not lc:
            return None
        named = [s for s in lc["statuses"] if re.search(rf"\b{re.escape(norm(s))}\b", norm(text))]
        if after and named:
            s = named[0]
            outs = [t for t in lc["transitions"] if t["from"] == s]
            if not outs:
                return {"text": f"I have not seen a {lc['entity']} move on from {s} yet; {s} may be the last status.",
                        "topic": "lifecycle"}
            return {"text": " ".join(f"After {s}, a {lc['entity']} becomes {t['to']} when "
                                     + (f"{_list(t['roles'])} " if t["roles"] else "someone ")
                                     + f"does '{t['action'] or 'the next action'}' (seen {_times(t['count'])})."
                                     for t in outs), "topic": "lifecycle"}
        lines = [f"A {lc['entity']} moves through: " + " → ".join(lc["statuses"]) + "."]
        if lc.get("initial"):
            i = lc["initial"]
            lines.append(f"It starts as {i['status']} after '{i['action']}'" + (f" by {i['role'].title()}" if i["role"]
                                                                                 else "") + ".")
        for t in lc["transitions"]:
            lines.append(f"{t['from']} → {t['to']}: '{t['action'] or 'action not seen'}'"
                         + (f" by {_list([r.title() for r in t['roles']])}" if t["roles"] else "")
                         + f" (seen {_times(t['count'])}).")
        return {"text": " ".join(lines), "topic": "lifecycle"}

    def _who(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"^(who|whom|which role|what role|which user)\b|\bwho (can|is|are|does|do|should)\b|"
                         r"\b(allowed|permitted) to\b", q) or _RULEISH.search(norm(text)):
            return None
        flow = self._flow(model, q)
        if not flow:
            return None
        roles = [r for r in model.get("roles") or []]
        did = [r["name"] for r in roles if any(p["flow"] == flow["title"] for p in r["performed"])]
        offered = [r["name"] for r in roles if flow["commit"] in r["offered_actions"] and r["name"] not in did]
        refused = [f"{r['name']} ({x.split(': ', 1)[-1]})" for r in roles for x in r["refused"]
                   if flow.get("entity") and flow["entity"] in norm(x) and flow.get("operation")
                   and norm(flow["operation"])[:5] in canonical(x)]
        if not did and not offered:
            return {"text": f"I saw '{flow['title']}' done {_times(flow['successes'])} but could not tell which role "
                            "was signed in.", "topic": "who"}
        parts = [f"'{flow['title']}' is done by {_list(did)}" + (f" (seen {_times(flow['successes'])})." if did else ".")]
        if offered:
            parts.append(f"{_list(offered)} also see the '{flow['commit']}' button.")
        if refused:
            parts.append("Refused: " + _list(refused, 4) + ".")
        others = [r["name"] for r in roles if r["name"] not in did and r["name"] not in offered]
        if others:
            parts.append(f"I did not see {_list(others)} do it.")
        return {"text": " ".join(parts), "topic": "who"}

    def _role_can(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        role = self._role(model, q)
        if not role or not re.search(r"\b(can|do|does|allowed|able|permission|role|access|see|responsib\w*)\b", q):
            return None
        parts = [f"{role['name']}" + (f" (users I saw: {_list(role['users'], 4)})" if role["users"] else "") + ":"]
        if role["performed"]:
            parts.append("does " + _list([f"{p['flow']} ({_times(p['times'])})" for p in role["performed"]]) + ";")
        if role["offered_actions"]:
            parts.append("is offered " + _list(role["offered_actions"]) + ";")
        if role["screens"]:
            parts.append("uses the screens " + _list(role["screens"], 6) + ".")
        if role["refused"]:
            parts.append("Refused: " + _list(role["refused"], 4) + ".")
        return {"text": " ".join(parts), "topic": "role"}

    def _how(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(how|steps?|procedure|process (to|for)|way to|walk me through|guide me)\b", q):
            return None
        flow = self._flow(model, q)
        if not flow:
            return None
        lines = [self._flow_text(model, flow)]
        return {"text": "\n".join(lines), "topic": "how"}

    def _flow_text(self, model: dict[str, Any], flow: dict[str, Any]) -> str:
        params = flow.get("parameters") or []
        out = [f"{flow['title']}" + (f": {flow['purpose']}" if flow.get("purpose") else "")
               + f" (I saw it done {_times(flow['successes'])}"
               + (f", rejected by the application {_times(flow['failures'])}" if flow["failures"] else "") + ")."]
        menu = {norm(m["label"]): m for m in (model.get("navigation") or {}).get("menu") or []}
        if flow.get("kind") == "row_action":
            key = params[0]["field"] if params else "its key"
            out.append(f"1. Open {flow.get('screen_name') or 'the list'} ({flow.get('start_url')})"
                       + (" from the menu." if norm(flow.get("screen_name")) in menu else "."))
            req = (flow.get("target") or {}).get("requires_status")
            out.append(f"2. Find the {flow.get('entity') or 'record'}'s row by {key}"
                       + (f" (it must be {req})" if req else "") + ".")
            out.append(f"3. Press {flow['commit']}.")
            if flow.get("expected_status"):
                out.append(f"Afterwards the status is {flow['expected_status']}.")
        else:
            n = 1
            out.append(f"{n}. Open {flow.get('screen_name') or 'the form'} ({flow.get('start_url')})"
                       + (" from the menu." if norm(flow.get("screen_name")) in menu else "."))
            for st in flow.get("steps") or []:
                if st["action"] == "navigate" or st.get("commit"):
                    continue
                n += 1
                p = next((x for x in params if x["name"] == st.get("param")), None)
                if st["action"] == "click":
                    out.append(f"{n}. Click {st['target']}.")
                elif p:
                    verb = "Choose" if p["type"] == "select" else "Tick" if p["type"] == "check" else "Enter"
                    hint = ""
                    if p.get("options"):
                        hint = f" (one of {_list(p['options'], 4)})"
                    elif p.get("example"):
                        hint = f" (for example {p['example']}" + (f", format {p['format']}" if p.get("format") else "") + ")"
                    out.append(f"{n}. {verb} {p['field']}{hint}" + (" (optional)" if p.get("optional") else "") + ".")
            n += 1
            out.append(f"{n}. Press {flow['commit']}.")
        if flow.get("success_messages"):
            out.append(f"The application confirms with a message like \"{flow['success_messages'][0]}\".")
        if flow.get("initial_status"):
            out.append(f"A new {flow.get('entity') or 'record'} starts as {flow['initial_status']}.")
        if flow.get("roles"):
            out.append("Done by " + _list([r.title() for r in flow["roles"]]) + ".")
        rules = [r["text"] for r in model.get("rules") or [] if r["flow"] == flow["title"]
                 and r["source"] in ("application message", "field constraint")]
        if rules:
            out.append("Rules: " + _list(rules, 5) + ".")
        return "\n".join(out)

    def _rules(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(rule|required|mandatory|must|unique|allowed values|limit\w*|valid\w*|format|condition)\b",
                         q) and not _RULEISH.search(norm(text)):
            return None
        rules = model.get("rules") or []
        if not rules:
            return {"text": "I have not seen any business rule yet. Rules appear when the application rejects an entry "
                            "or a field declares a constraint.", "topic": "rules"}
        flow = self._flow(model, q)
        qt = set(tokens(q)) | set(tokens(text))
        field_hits = [r for r in rules if qt & set(tokens(r["text"])) - {"must", "be", "is", "a", "the", "rule"}]
        chosen = [r for r in rules if flow and r["flow"] == flow["title"]]
        if field_hits and (not chosen or len(field_hits) < len(chosen)):
            narrowed = [r for r in field_hits if not flow or r["flow"] == flow["title"]]
            chosen = narrowed or field_hits
        chosen = chosen or rules
        lines = []
        for r in chosen[:10]:
            src = "the application said so" if r["source"] == "application message" else r["source"]
            ev = f" e.g. \"{r['evidence'][0]}\"" if r.get("evidence") else ""
            lines.append(f"• {r['text']} ({r['flow']}; {src}{ev}).")
        return {"text": "Rules I learned:\n" + "\n".join(lines) + "\nRules are checked in the preview; only approved "
                        "ones block a record.", "topic": "rules"}

    def _fields(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(fields?|information|data|details|need to (enter|fill|provide)|options?|values?|choices?|"
                         r"inputs?)\b", q):
            return None
        flow = self._flow(model, q) or next((f for f in model.get("flows") or [] if f.get("operation") == "create" and
                                             self._entity(model, q) and f.get("entity") == self._entity(model, q)["name"]),
                                            None)
        if not flow or not flow.get("parameters"):
            return None
        lines = [f"'{flow['title']}' needs:"]
        for p in flow["parameters"]:
            extra = f": one of {_list(p['options'], 10)}" if p.get("options") else \
                (f" (for example {p['example']})" if p.get("example") else "")
            lines.append(f"• {p['field']}{' (optional)' if p.get('optional') else ''}{extra}"
                         + (f"; usually {p['default']}" if p.get("default") else ""))
        return {"text": "\n".join(lines), "topic": "fields"}

    def _screens(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(screen|where|navigate|go to|find the|menu)\b", q):
            return None
        screens = [s for s in model["screens"] if s["type"] not in ("sign-in", "verification", "confirmation",
                                                                     "refused")]
        ent = self._entity(model, q)
        hits = [s for s in screens if ent and s["entity"] == ent["name"]] if ent else []
        kind = next((k for k, w in (("list", r"\b(list|lists|all|overview|table)\b"), ("form", r"\b(form|create|"
                     r"enter)\b"), ("search", r"\bsearch\b")) if re.search(w, q)), None)
        if kind and any(s["type"] == kind for s in hits):
            hits = [s for s in hits if s["type"] == kind]
        if hits:
            lines = [f"Screens for {plural(ent['name'])}:"]
            menu = {norm(m["label"]): m for m in (model.get("navigation") or {}).get("menu") or []}
            for s in hits[:6]:
                via = next((e for e in (model.get("navigation") or {}).get("edges") or []
                            if e.get("to_id") == s["id"] and e.get("via") and norm(e["via"]) in menu), None) or \
                    next((e for e in (model.get("navigation") or {}).get("edges") or []
                          if e.get("to_id") == s["id"] and e.get("via")), None)
                lines.append(f"• {s['name']} ({s['path']}, {s['type']})" + (f", reached with '{via['via']}'" if via
                                                                             else ""))
            return {"text": "\n".join(lines), "topic": "screens"}
        menu = (model.get("navigation") or {}).get("menu") or []
        return {"text": (f"The menu has {_list([m['label'] for m in menu])}. " if menu else "")
                + f"I know {len(screens)} screen(s): " + _list([f"{s['name']} ({s['type']})" for s in screens], 12)
                + ".", "topic": "screens"}

    def _roles_list(self, model: dict[str, Any], q: str, text: str, name: str) -> dict[str, Any] | None:
        if not re.search(r"\b(role|users?|who uses)\b", q):
            return None
        roles = model.get("roles") or []
        if not roles:
            return None
        return {"text": "Roles I saw: " + "; ".join(
            f"{r['name']} ({_list([p['flow'] for p in r['performed']], 3) or 'viewing only'})" for r in roles) + ".",
                "topic": "roles"}

    # ---- retrieval fallback --------------------------------------------------------------------------------------

    @staticmethod
    def facts(model: dict[str, Any]) -> list[str]:
        out = []
        for s in model.get("screens") or []:
            out.append(f"Screen {s['name']} at {s['path']} is a {s['type']} screen"
                       + (f" about {s['entity']}" if s["entity"] else "")
                       + (f" with fields {_list([f['label'] for f in s['fields']], 10)}" if s["fields"] else "")
                       + (f" and buttons {_list([b['label'] for b in s['buttons']], 8)}" if s["buttons"] else "")
                       + (f", used by {_list(s['roles'], 4)}" if s["roles"] else "") + ".")
        for f in model.get("flows") or []:
            out.append(f"Task {f['title']} on screen {f.get('screen_name')} is committed with {f['commit']}"
                       + (f", fields {_list([p['field'] for p in f.get('parameters') or []], 10)}"
                          if f.get("parameters") else "")
                       + (f", done by {_list(f['roles'])}" if f.get("roles") else "") + f", seen {f['successes']} times.")
        for r in model.get("rules") or []:
            out.append(f"Rule for {r['flow']}: {r['text']}.")
        for lc in model.get("lifecycles") or []:
            out.append(f"{lc['entity']} statuses: " + " then ".join(lc["statuses"]) + ".")
            for t in lc["transitions"]:
                out.append(f"{lc['entity']} goes from {t['from']} to {t['to']} by {t['action']} "
                           f"done by {_list(t['roles'])}.")
        for r in model.get("roles") or []:
            out.append(f"Role {r['name']} uses {_list(r['screens'], 6)}"
                       + (f" and performs {_list([p['flow'] for p in r['performed']])}" if r["performed"] else "")
                       + (f"; refused: {_list(r['refused'], 3)}" if r["refused"] else "") + ".")
        return out

    @staticmethod
    def _bm25(query: str, docs: list[str], k: int = 6) -> list[str]:
        qt = [t for t in tokens(canonical(query)) if len(t) > 1]
        dt = [tokens(canonical(d)) for d in docs]
        if not qt or not docs:
            return []
        n = len(docs)
        avg = sum(len(d) for d in dt) / n
        df = Counter(t for d in dt for t in set(d))
        scores = []
        for i, d in enumerate(dt):
            tf = Counter(d)
            s = 0.0
            for t in qt:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(d) / avg))
            scores.append((s, i))
        scores.sort(reverse=True)
        return [docs[i] for s, i in scores[:k] if s > 0]

    def _retrieve(self, model: dict[str, Any], q: str, text: str, app: dict[str, Any]) -> dict[str, Any] | None:
        top = self._bm25(text, self.facts(model))
        if not top:
            return None
        if self.llm is not None:
            block, _ = wrap_untrusted("\n".join(top), 3000)
            try:
                reply = self.llm.chat([{"role": "system", "content": SYSTEM_RULES + " Answer the user's question about "
                                        "their application using only the facts given (they were observed in the "
                                        "application). If the facts do not answer it, say what you do know. Plain "
                                        "text, at most 6 short sentences."},
                                       {"role": "user", "content": f"Facts:\n{block}\nQuestion: {text}"}],
                                      max_tokens=300)
                answer = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
                if answer:
                    return {"type": "answer", "app_id": app["id"], "text": answer[:1500], "topic": "retrieval",
                            "facts": top, "grounded": True}
            except Exception:
                pass
        return {"type": "answer", "app_id": app["id"], "topic": "retrieval", "facts": top, "grounded": True,
                "text": "Here is what I learned that relates to your question:\n" + "\n".join(f"• {t}" for t in top)}

    def describe_model(self, model: dict[str, Any]) -> str:
        return dumps({k: model.get(k) for k in ("stats", "flows", "lifecycles", "roles")})[:2000]
