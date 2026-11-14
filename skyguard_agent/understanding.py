"""Application understanding from passive observation (23.7.0).

The person just uses the application. This module reads the observation journal (journal.py) and works out, without
being told:

  screens      every distinct screen (URL pattern + heading), what kind it is (sign-in, list, form, search,
               confirmation, page), its fields, buttons and tables, which entity it is about and who used it
  navigation   how the person moves between screens (which link or button leads where), the main menu
  flows        tasks the person completed: the screen, the fields that change from one time to the next
               (parameters), the ones that stay (defaults), the button that commits, how the application confirms
               it (success message, the record in a list), and row actions on existing records (Approve, Accept ...)
  statuses     status columns, each record's status over time, and the lifecycle: which action, by which role,
               moves a record from one status to the next
  rules        the application's own validation messages after a submit (required, unique, date in the future,
               resource already booked at that time ...), HTML constraints, value formats and offered options
  roles        who is signed in (read from the page, learned from the sign-in), the screens and actions each role
               used or was offered, and what each role was refused
  sign-in      the sign-in screen's fields and the pattern that shows who is signed in (used to verify accounts)

All of it is deterministic and explainable (each finding carries how often it was seen); the local model is optional
and only adds short readable names. The result is the *application model* (shown in the Understanding tab and used by
chat at once, read-only) and, when it is stable, versioned knowledge entries: screens, roles and lifecycles as
Discovered, workflows as Discovered (seen once) or Awaiting Approval (seen consistently), rules as Discovered (from the
application's own messages) or AI Suggested (from patterns). As before, nothing learned can submit anything in the
application until a person approves the workflow.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from . import locator
from .guard import SYSTEM_RULES, wrap_untrusted
from .llm import extract_json
from .rules import parse_date, parse_time
from .util import digest, dumps, loads, norm, now_iso

COMMIT_WORDS = re.compile(r"\b(save|submit|create|add|store|approve|authori[sz]e|confirm|send|update|accept|record|"
                          r"register|book|assign|apply|finish|complete|mark|post|publish|reject|decline|cancel|"
                          r"delete|remove|close|archive|restore|verify|release|dispatch|issue|pay|transfer|"
                          r"process|generate|upload|import|reserve|order|checkout|check out)\b", re.I)
NOT_COMMIT = re.compile(r"^\s*(search|find|filter|go|next|previous|prev|back|more|show|view|open|details?|refresh|"
                        r"reset|clear|sort|expand|collapse|menu|close dialog|sign ?in|log ?in|login)\b", re.I)
SUCCESS_WORDS = re.compile(r"\b(success(fully)?|created|saved|added|approved|authori[sz]ed|updated|recorded|"
                           r"accepted|submitted|completed|booked|confirmed|registered|assigned|deleted|removed|"
                           r"cancell?ed|rejected|published|sent|done|thank you)\b", re.I)
REFUSAL_WORDS = re.compile(r"\b(not allowed|not authori[sz]ed|permission|access denied|forbidden|cannot access|"
                           r"(your )?role (cannot|can ?not|is not allowed)|insufficient (rights|privileges)|"
                           r"unauthori[sz]ed)\b", re.I)
STATUS_HEADER = re.compile(r"\b(status|state|stage|progress|workflow)\b", re.I)
KEY_HEADER = re.compile(r"\b(no|number|num|id|code|ref|reference|#)\b\.?$", re.I)
GENERIC_WORDS = {"new", "create", "add", "edit", "update", "list", "lists", "manage", "management", "details",
                 "detail", "view", "search", "my", "all", "page", "home", "dashboard", "welcome", "the", "and", "for",
                 "of", "to", "a", "an", "form", "screen", "overview", "summary", "report", "reports", "back", "not",
                 "found", "allowed", "sign", "in", "login", "log", "verification", "settings", "your", "with",
                 "save", "submit", "store", "approve", "authorize", "authorise", "accept"}
IDENTITY_PATTERNS = [
    r"(?:logged|signed) in as[:\s]+(?P<user>[\w.@-]+)\s*\((?P<role>[^)]{2,40})\)",
    r"(?:logged|signed) in as[:\s]+(?P<user>[\w.@-]+)",
    r"\bwelcome(?: back)?,?\s+(?P<user>[\w.@-]+)",
    r"\buser(?:name)?\s*[:\-]\s*(?P<user>[\w.@-]+)",
]
ROLE_PATTERN = r"\brole\s*[:\-]\s*(?P<role>[A-Za-z][\w -]{1,30}?)(?=\s*(?:\||$|,|\n|  ))"
MAX_FLOWS = 60


# ---- small text helpers ----------------------------------------------------------------------------------------------

def singular(word: str) -> str:
    w = norm(word)
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def plural(word: str) -> str:
    w = norm(word)
    if not w:
        return w
    if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
        return w[:-1] + "ies"
    if w.endswith(("s", "x", "ch", "sh")):
        return w + "es"
    return w + "s"


def tokens(text: Any) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", norm(text)):
        w = {"no": "number", "num": "number", "nbr": "number", "qty": "quantity", "dept": "department",
             "authorise": "authorize", "authorised": "authorized"}.get(w, w)
        out.append(singular(w) if not w.isdigit() else w)
    return out


def path_template(url: str) -> str:
    path = urlsplit(url or "").path or "/"
    segs = []
    for s in path.strip("/").split("/"):
        if not s:
            continue
        if re.search(r"\d", s) or re.fullmatch(r"[a-f0-9-]{16,}", s, re.I) or len(s) > 40:
            segs.append("{id}")
        else:
            segs.append(s.lower())
    return "/" + "/".join(segs)


def text_template(text: str) -> str:
    return " ".join("{id}" if re.search(r"\d", w) else w for w in str(text or "").split())[:80]


def entity_of(*texts: str, exclude: set[str] | frozenset[str] = frozenset()) -> str:
    for text in texts:
        words = [w for w in re.findall(r"[a-z]+", norm(text)) if len(w) > 2 and w not in GENERIC_WORDS
                 and w not in exclude and singular(w) not in exclude
                 and singular(w) not in GENERIC_WORDS and not COMMIT_WORDS.fullmatch(w)]
        if words:
            return singular(words[-1] if len(words) <= 2 else words[0])
    return ""


def param_name(label: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", norm(label)).strip("_")
    return (name or "value")[:40]


def safe_key(text: str) -> str:
    text = str(text or "").replace("{id}", ":id")
    return " ".join(re.sub(r"[^\w .:/>()&+-]", " ", text).split())[:150] or "Learned item"


_FORMAT_HINT = re.compile(r"^[\s]*([dmyhs]{1,4}[\s/.:-]*){2,4}(\s*(am|pm))?[\s]*$", re.I)


def field_label(fp: dict[str, Any]) -> str:
    """What a person calls a field: its label, else the text beside it when the accessible name is only a format
    hint such as YYYY-MM-DD or HH:MM."""
    if not fp:
        return ""
    if fp.get("label"):
        return str(fp["label"])
    name = str(fp.get("name") or "")
    if fp.get("nearby") and (not name or _FORMAT_HINT.match(name) or name == fp.get("placeholder")):
        return str(fp["nearby"])
    return locator.describe_target(fp)


def _field_type(label: str, tag: str | None, typ: str | None, values: list[str]) -> str:
    if tag == "select":
        return "select"
    if typ in ("date", "datetime-local"):
        return "date"
    if typ == "time":
        return "time"
    if typ == "number":
        return "number"
    if typ in ("checkbox", "radio"):
        return "check"
    vals = [v for v in values if v]
    if vals and all(parse_date(v) for v in vals) and all(re.search(r"\d", v) for v in vals):
        return "date"
    if vals and all(parse_time(v) for v in vals):
        return "time"
    if re.search(r"\bdate\b", norm(label)):
        return "date"
    if re.search(r"\btime\b", norm(label)):
        return "time"
    return "text"


def _date_format(values: list[str]) -> str | None:
    from .rules import DATE_FORMATS

    for name, fmt in DATE_FORMATS.items():
        ok = True
        for v in values:
            try:
                _dt.datetime.strptime(v.strip(), fmt)
            except ValueError:
                ok = False
                break
        if ok and values:
            return name
    return None


def overlap(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    ta -= {"the", "a", "an", "of", "is", "are", "be"}
    tb -= {"the", "a", "an", "of", "is", "are", "be"}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def classify_screen(p: dict[str, Any]) -> str:
    auth = p.get("auth") or {}
    if auth.get("password_fields"):
        return "sign-in"
    if auth.get("otp_fields"):
        return "verification"
    editable = [f for f in p.get("fields", []) if not f.get("secret") and not f.get("readonly")
                and f.get("type") not in ("checkbox", "radio")]
    commit = [b for b in p.get("buttons", []) if not b.get("in_row") and COMMIT_WORDS.search(b.get("label") or "")
              and not NOT_COMMIT.search(b.get("label") or "")]
    if len(editable) >= 2 and commit:
        return "form"
    if any(t.get("rows") for t in p.get("tables", [])):
        return "list"
    if editable and any(re.search(r"\b(search|find|filter)\b", b.get("label") or "", re.I) for b in p.get("buttons", [])):
        return "search"
    msgs = p.get("messages", [])
    if any(REFUSAL_WORDS.search(m.get("text") or "") for m in msgs):
        return "refused"
    if msgs and all(m.get("kind") == "success" or SUCCESS_WORDS.search(m.get("text") or "") for m in msgs):
        return "confirmation"
    if len(editable) >= 2:
        return "form"
    return "page"


def read_identity(p: dict[str, Any], profile: dict[str, Any], learned: list[str]) -> tuple[str | None, str | None]:
    text = str(p.get("text") or "")
    patterns = ([profile.get("identity", {}).get("pattern")] if (profile.get("identity") or {}).get("pattern") else []) \
        + learned + IDENTITY_PATTERNS
    user = role = None
    for pat in patterns:
        try:
            m = re.search(pat, text, re.I)
        except re.error:
            continue
        if m:
            g = m.groupdict()
            user = user or g.get("user")
            role = role or g.get("role")
            if user and role:
                break
    if not role:
        m = re.search(ROLE_PATTERN, text, re.I)
        if m:
            role = m.group("role")
    return (user.strip() if user else None), (" ".join(role.split()) if role else None)


def derive_identity_pattern(text: str, user: str) -> str | None:
    """After a sign-in with user ID ``user``: the page sentence that shows it becomes a reusable pattern."""
    if not user or len(user) < 2:
        return None
    m = re.search(rf"(?<![\w]){re.escape(user)}(?![\w])", text)
    if not m:
        return None
    before = text[max(0, m.start() - 40):m.start()]
    words = re.findall(r"[A-Za-z][A-Za-z,:]*", before.split("|")[-1])[-3:]
    if not words:
        return None
    prefix = r"\s+".join(re.escape(w) for w in words)
    after = text[m.end():m.end() + 60]
    pat = prefix + r"\s*(?P<user>[\w.@-]+)"
    if re.match(r"\s*\(([^)]{2,40})\)", after):
        pat += r"\s*\((?P<role>[^)]+)\)"
    elif re.match(r"\s*[-|]\s*([A-Za-z][\w ]{2,30})", after):
        pat += r"\s*[-|]\s*(?P<role>[A-Za-z][\w ]{2,30}?)(?=\s*(?:\||$))"
    try:
        re.compile(pat)
    except re.error:
        return None
    return pat


def _table_rows(t: dict[str, Any]) -> list[dict[str, str]]:
    headers = t.get("headers") or []
    out = []
    for r in t.get("rows", []):
        if not any(c for c in r):
            continue
        out.append({(headers[i] if i < len(headers) and headers[i] else f"col{i + 1}"): c for i, c in enumerate(r)})
    return out


def status_column(t: dict[str, Any]) -> str | None:
    headers = t.get("headers") or []
    for h in headers:
        if h and STATUS_HEADER.search(h):
            return h
    rows = _table_rows(t)
    if len(rows) < 4:
        return None
    for h in headers:
        vals = [r.get(h, "") for r in rows if r.get(h)]
        distinct = set(vals)
        if vals and 1 < len(distinct) <= 8 and len(distinct) < len(vals) and \
                all(len(v.split()) <= 3 and not re.search(r"\d", v) and v[:1].isupper() for v in distinct):
            return h
    return None


def key_column(t: dict[str, Any]) -> str | None:
    headers = t.get("headers") or []
    rows = _table_rows(t)
    if not rows:
        return None
    for prefer in (True, False):
        for h in headers:
            if not h:
                continue
            vals = [r.get(h, "") for r in rows]
            if not all(vals) or len(set(vals)) != len(vals):
                continue
            if prefer and not KEY_HEADER.search(h):
                continue
            if not prefer and not any(re.search(r"\d", v) for v in vals):
                continue
            return h
    return None


# ---- the miner -------------------------------------------------------------------------------------------------------

class Miner:
    """One pass over the journal. ``run`` returns the application model."""

    def __init__(self, app: dict[str, Any], profile: dict[str, Any] | None = None) -> None:
        self.app = app
        self.profile = dict(profile or {})
        self.screens: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.instances: list[dict[str, Any]] = []
        self.sightings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.logins: list[dict[str, Any]] = []
        self.identity_patterns: list[str] = []
        self.refusals: list[dict[str, Any]] = []
        self.role_info: dict[str, dict[str, Any]] = {}
        self.link_pages: Counter[str] = Counter()
        self.links_seen: dict[str, str] = {}
        self.query_keys: list[tuple[str, str, str]] = []  # (path, query param, value)
        self.pages_logged_in = 0
        self.app_words = {singular(w) for w in re.findall(r"[a-z]+", norm(app.get("name")))} | \
            {w for w in re.findall(r"[a-z]+", norm(app.get("name")))}

    # -- per page ------------------------------------------------------------------------------------------------

    def screen_id(self, p: dict[str, Any]) -> str:
        return f"{path_template(p.get('url') or '')}#{norm(text_template(p.get('heading') or p.get('title') or ''))}"

    def _screen(self, p: dict[str, Any], role: str | None) -> dict[str, Any]:
        sid = self.screen_id(p)
        s = self.screens.get(sid)
        name = text_template(p.get("heading") or p.get("title") or "") or path_template(p.get("url") or "")
        if not s:
            s = self.screens[sid] = {"id": sid, "name": name, "path": path_template(p.get("url") or ""),
                                     "example_url": urlsplit(p.get("url") or "").path or "/",
                                     "types": Counter(), "visits": 0, "roles": Counter(), "fields": {},
                                     "buttons": {}, "row_actions": {}, "tables": [], "messages": Counter(),
                                     "entity": entity_of(p.get("heading") or "", p.get("title") or "",
                                                         path_template(p.get("url") or "").replace("/", " "),
                                                         exclude=self.app_words)}
        s["visits"] += 1
        s["types"][classify_screen(p)] += 1
        if role:
            s["roles"][role] += 1
        for f in p.get("fields", []):
            if not f.get("label"):
                continue
            cur = s["fields"].setdefault(f["label"], {"label": f["label"], "tag": f.get("tag"), "type": f.get("type"),
                                                      "required": False, "options": [], "secret": bool(f.get("secret")),
                                                      "constraints": {}, "section": f.get("section") or ""})
            cur["required"] = cur["required"] or bool(f.get("required"))
            if f.get("options"):
                cur["options"] = f["options"]
            if f.get("constraints"):
                cur["constraints"].update(f["constraints"])
        for b in p.get("buttons", []):
            if not b.get("label"):
                continue
            bucket = s["row_actions"] if b.get("in_row") else s["buttons"]
            bucket.setdefault(b["label"], Counter())
            if role:
                bucket[b["label"]][role] += 1
        for t in p.get("tables", []):
            if t.get("headers") and t["headers"] not in s["tables"]:
                s["tables"].append(t["headers"])
        for m in p.get("messages", []):
            s["messages"][f"{m.get('kind')}|{text_template(m.get('text') or '')}"] += 1
        return s

    def _track_records(self, p: dict[str, Any], obs_id: int, role: str | None, screen: dict[str, Any]) -> None:
        for t in p.get("tables", []):
            if t.get("layout") or len(t.get("headers") or []) < 3:
                continue  # a form laid out in a table, or label / value pairs: not a list of records
            kc = key_column(t)
            if not kc:
                continue
            sc = status_column(t)
            for row in _table_rows(t):
                key = row.get(kc)
                if not key:
                    continue
                self.sightings[norm(key)].append({"obs": obs_id, "key": key, "key_column": kc, "status_column": sc,
                                                  "status": row.get(sc) if sc else None, "row": row, "role": role,
                                                  "screen": screen["id"], "path": urlsplit(p.get("url") or "").path,
                                                  "entity": screen.get("entity") or ""})

    # -- the pass ------------------------------------------------------------------------------------------------

    def run(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            by_session[e.get("_session") or "?"].append(e)
        for items in by_session.values():
            self._session(items)
        return self._model(entries)

    def _session(self, items: list[dict[str, Any]]) -> None:
        user = role = None
        last_page: dict[str, Any] | None = None
        last_click: str | None = None
        buffer: list[dict[str, Any]] = []
        pending: dict[str, Any] | None = None
        login_user: str | None = None
        login_events: list[dict[str, Any]] = []
        last_commit: dict[str, Any] | None = None
        for it in items:
            if it.get("_t") == "page":
                p = it
                auth = p.get("auth") or {}
                if auth.get("password_fields"):
                    if user:
                        user = role = None  # signed out
                else:
                    if login_user and not user:
                        pat = derive_identity_pattern(p.get("text") or "", login_user)
                        if pat and pat not in self.identity_patterns:
                            self.identity_patterns.append(pat)
                    if login_events and not auth.get("otp_fields"):
                        self._learn_login(login_events)
                        login_events = []
                    u, r = read_identity(p, self.profile, self.identity_patterns)
                    if u or r:
                        user, role = u or user, (r or role)
                        if login_user and u and norm(u) == norm(login_user):
                            login_user = None
                    if user:
                        self.pages_logged_in += 1
                role_key = norm(role or (f"user {user}" if user else "")) or None
                screen = self._screen(p, role_key)
                if role_key:
                    info = self.role_info.setdefault(role_key, {"name": role or f"user {user}", "users": set(),
                                                                "screens": Counter(), "performed": Counter(),
                                                                "refused": []})
                    if user:
                        info["users"].add(user)
                    info["screens"][screen["id"]] += 1
                if any(REFUSAL_WORDS.search(m.get("text") or "") for m in p.get("messages", [])) or \
                        REFUSAL_WORDS.search(p.get("heading") or ""):
                    msg = next((m["text"] for m in p.get("messages", []) if REFUSAL_WORDS.search(m.get("text") or "")),
                               p.get("heading") or "")
                    self.refusals.append({"role": role_key, "screen": screen["id"], "message": msg,
                                          "path": urlsplit(p.get("url") or "").path})
                self._track_records(p, it["_id"], role_key, screen)
                if user and not auth.get("password_fields"):
                    for ln in p.get("links", []):
                        if ln.get("label") and not re.search(r"\d", ln.get("href") or ""):
                            self.link_pages[ln["label"]] += 1
                            self.links_seen.setdefault(ln["label"], ln.get("href") or "")
                q = urlsplit(p.get("url") or "")
                for k, v in parse_qsl(q.query):
                    if v and k not in ("page", "sort", "order", "lang"):
                        self.query_keys.append((q.path, k, v))
                if last_page is not None:
                    a, b = self.screen_id(last_page), screen["id"]
                    if a != b:
                        edge = self.edges.setdefault((a, b), {"from": a, "to": b, "via": Counter(), "count": 0})
                        edge["count"] += 1
                        if last_click:
                            edge["via"][last_click] += 1
                if pending is not None and self._outcome(pending, p):
                    pending.pop("_events", None)
                    self.instances.append(pending)
                    pending = None
                last_page = p
                last_click = None
                continue
            ev = it
            el = ev.get("element") or {}
            page_is_login = bool(last_page and (last_page.get("auth") or {}).get("password_fields")) or \
                el.get("type") == "password"
            if page_is_login and ev["kind"] in ("fill", "click", "submit", "select"):
                login_events.append(ev)
                if ev["kind"] == "fill" and not ev.get("secret") and el.get("type") != "password" and ev.get("value"):
                    login_user = ev["value"]
                continue
            label = field_label(el) if el else ""
            if ev["kind"] == "submit" and last_commit is not None and \
                    (ev.get("url") or "") == (last_commit.get("url") or "") and \
                    (not ev.get("at") or not last_commit.get("at") or abs(int(ev["at"]) - int(last_commit["at"])) < 2500):
                last_commit = None
                continue  # the click on the submit button already counted
            if pending is not None:
                # an action before the page told us the outcome: decide with what we have
                self._outcome(pending, last_page or {}, final=True)
                if pending["outcome"] == "unknown":
                    # nothing happened ("Add passenger" opening a row, a stepper "+"): it was a step, not a commit
                    buffer = [{k: v for k, v in e.items() if k != "_commit"} for e in pending["_events"]] + buffer
                else:
                    pending.pop("_events", None)
                    self.instances.append(pending)
                pending = None
            if ev["kind"] == "click":
                last_click = label
            ev = {**ev, "_label": label}
            if self._is_commit(ev, last_page):
                ev["_commit"] = True
                if ev["kind"] == "submit":
                    btn = next((b["label"] for b in (last_page or {}).get("buttons", [])
                                if not b.get("in_row") and COMMIT_WORDS.search(b.get("label") or "")), "Submit")
                    ev["_label"] = btn
                pending = self._instance(buffer + [ev], ev, last_page, user, role)
                pending["_events"] = buffer + [ev]
                last_commit = ev
                buffer = []
                continue
            last_commit = None
            buffer.append(ev)
            if len(buffer) > 400:
                buffer = buffer[-200:]
        if pending is not None:
            self._outcome(pending, last_page or {}, final=True)
            pending.pop("_events", None)
            self.instances.append(pending)
        if login_events:
            self._learn_login(login_events)

    def _is_commit(self, ev: dict[str, Any], page: dict[str, Any] | None) -> bool:
        el = ev.get("element") or {}
        label = ev.get("_label") or ""
        if ev["kind"] == "submit":
            return True
        if ev["kind"] != "click":
            return False
        buttonish = el.get("tag") == "button" or el.get("type") in ("submit", "button", "image") or \
            el.get("role") in ("button", "menuitem")
        if not buttonish or not label or NOT_COMMIT.search(label):
            return False
        return bool(COMMIT_WORDS.search(label))

    def _instance(self, events: list[dict[str, Any]], commit: dict[str, Any], page: dict[str, Any] | None,
                  user: str | None, role: str | None) -> dict[str, Any]:
        url = commit.get("url") or (page or {}).get("url") or ""
        same = [e for e in events if (e.get("url") or url).split("#")[0] == url.split("#")[0]]
        values: dict[str, str] = {}
        meta: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        touched: set[str] = set()
        if page and page.get("url", "").split("#")[0] == url.split("#")[0]:
            for f in page.get("fields", []):
                if f.get("label") and not f.get("secret") and f.get("value") not in (None, "") and \
                        f.get("type") not in ("checkbox", "radio") and not (f.get("tag") == "select" and
                                                                           re.match(r"^\s*(--|select|choose|please)", f.get("value") or "", re.I)):
                    values[f["label"]] = f["value"]
                    meta[f["label"]] = {"tag": f.get("tag"), "type": f.get("type"), "options": f.get("options") or [],
                                        "required": bool(f.get("required"))}
                    order.append(f["label"])
        clicks = []
        for e in same[:-1]:
            el = e.get("element") or {}
            label = e.get("_label") or field_label(el)
            if e["kind"] in ("fill", "select", "check"):
                if e.get("secret"):
                    continue
                values[label] = e.get("value") or ""
                meta[label] = {"tag": el.get("tag"), "type": el.get("type"), "options": e.get("options") or [],
                               "required": bool(e.get("required"))}
                if label in order:
                    order.remove(label)
                order.append(label)
                touched.add(label)
            elif e["kind"] == "click":
                clicks.append({"label": label, "after": order[-1] if order else None, "element": el})
        row = (commit.get("element") or {}).get("table")
        inst = {"screen": self.screen_id(page) if page else f"{path_template(url)}#", "url": url,
                "commit": commit.get("_label") or "Submit", "commit_element": commit.get("element") or {},
                "values": values, "meta": meta, "order": order, "touched": touched, "clicks": clicks,
                "user": user, "role": norm(role or (f"user {user}" if user else "")) or None,
                "obs": commit.get("_id"), "outcome": None, "messages": [], "invalid": [], "row": None,
                "page_heading": (page or {}).get("heading") or "", "page_title": (page or {}).get("title") or ""}
        if row and page:
            inst["row"] = self._row_of(page, row.get("row") if isinstance(row, dict) else str(row))
        return inst

    @staticmethod
    def _row_of(page: dict[str, Any], row_text: str) -> dict[str, Any] | None:
        cells = [c.strip() for c in str(row_text or "").split("|") if c.strip()]
        for t in page.get("tables", []):
            for r in _table_rows(t):
                vals = [v for v in r.values() if v]
                if cells and all(c in vals for c in cells[:4]):
                    return {"row": r, "key_column": key_column(t), "status_column": status_column(t),
                            "headers": t.get("headers") or []}
        return {"row": {f"col{i + 1}": c for i, c in enumerate(cells)}, "key_column": None, "status_column": None,
                "headers": []}

    def _outcome(self, inst: dict[str, Any], p: dict[str, Any], final: bool = False) -> bool:
        errors = [m["text"] for m in p.get("messages", []) if m.get("kind") == "error"
                  or (re.search(r"\b(error|invalid|required|already|cannot|not available|must|failed)\b",
                                m.get("text") or "", re.I) and not SUCCESS_WORDS.search(m.get("text") or ""))]
        invalid = [f"{f['label']}: {f.get('validation') or 'invalid'}" for f in p.get("fields", []) if f.get("invalid")]
        success = [m["text"] for m in p.get("messages", []) if m.get("kind") == "success"
                   or SUCCESS_WORDS.search(m.get("text") or "")]
        success = [s for s in success if s not in errors]
        changed = self.screen_id(p) != inst["screen"] or (p.get("url") or "") != inst["url"]
        inst["after_url"] = p.get("url")
        inst["after_screen"] = self.screen_id(p) if p else None
        if any(REFUSAL_WORDS.search(e) for e in errors):
            inst["outcome"], inst["messages"] = "refused", errors
            return True
        if errors or invalid:
            inst["outcome"], inst["messages"], inst["invalid"] = "failure", errors, invalid
            return True
        if success:
            inst["outcome"], inst["messages"] = "success", success
            return True
        if changed and not (p.get("auth") or {}).get("password_fields"):
            inst["outcome"] = "success"
            return True
        if final:
            inst["outcome"] = "unknown"
            return True
        return False

    def _learn_login(self, events: list[dict[str, Any]]) -> None:
        out: dict[str, Any] = {}
        for e in events:
            el = e.get("element") or {}
            label = field_label(el)
            if e["kind"] == "fill" and (el.get("type") == "password" or e.get("secret")):
                out["password"] = label
            elif e["kind"] == "fill" and not e.get("secret"):
                out.setdefault("username", label)
                out["user"] = e.get("value")
            elif e["kind"] == "click" and label:
                out["submit"] = label
            out.setdefault("url", urlsplit(e.get("url") or "").path or "/")
        if out.get("username") and out.get("password"):
            self.logins.append(out)

    # -- assembling the model ------------------------------------------------------------------------------------

    def _model(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        screens = self._screens_out()
        flows, groups = self._flows()
        lifecycles = self._lifecycles(flows)
        rules = self._rules(flows, groups)
        self._attach_overlap(flows, rules)
        roles = self._roles(flows, screens)
        nav = self._navigation(screens)
        entities = self._entities(screens, flows, lifecycles)
        login = None
        if self.logins:
            last = self.logins[-1]
            login = {"url": last.get("url"), "username": last.get("username"), "password": last.get("password"),
                     "submit": last.get("submit") or "Sign in"}
        identity = self.identity_patterns[-1] if self.identity_patterns else None
        if not identity and any(re.search(IDENTITY_PATTERNS[0], str(e.get("text") or ""), re.I)
                                for e in entries if e.get("_t") == "page"):
            identity = IDENTITY_PATTERNS[0]
        records = {v[-1]["key"]: {"status": v[-1]["status"], "entity": v[-1]["entity"], "row": v[-1]["row"],
                                  "seen": len(v)} for v in self.sightings.values()}
        return {"application": self.app["name"], "built_at": now_iso(),
                "stats": {"observations": len(entries), "pages": sum(1 for e in entries if e.get("_t") == "page"),
                          "actions": sum(1 for e in entries if e.get("_t") == "event"),
                          "sessions": len({e.get("_session") for e in entries}),
                          "tasks_seen": len(self.instances)},
                "screens": screens, "navigation": nav, "flows": flows, "lifecycles": lifecycles, "rules": rules,
                "roles": roles, "entities": entities, "login": login,
                "identity": {"pattern": identity} if identity else None,
                "records": dict(list(records.items())[-500:])}

    def _screens_out(self) -> list[dict[str, Any]]:
        out = []
        for s in self.screens.values():
            out.append({"id": s["id"], "name": s["name"], "path": s["path"], "example_url": s["example_url"],
                        "type": s["types"].most_common(1)[0][0] if s["types"] else "page", "visits": s["visits"],
                        "entity": s["entity"], "roles": [r for r, _ in s["roles"].most_common()],
                        "fields": list(s["fields"].values())[:80],
                        "buttons": [{"label": b, "roles": sorted(c)} for b, c in s["buttons"].items()][:60],
                        "row_actions": [{"label": b, "roles": sorted(c)} for b, c in s["row_actions"].items()][:30],
                        "tables": s["tables"][:4],
                        "messages": [k.split("|", 1)[1] for k, _ in s["messages"].most_common(8)]})
        out.sort(key=lambda s: -s["visits"])
        return out

    def _flows(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for inst in self.instances:
            if inst["outcome"] == "unknown":
                continue
            gk = f"{inst['screen']}|{norm(inst['commit'])}|{'row' if inst.get('row') else 'form'}"
            groups[gk].append(inst)
        flows = []
        for gk, insts in groups.items():
            flow = self._flow(insts)
            if flow:
                flow["group"] = gk
                flows.append(flow)
        # one title per flow: two different screens doing "Create trip" get their screen name added
        seen: Counter[str] = Counter(f["title"] for f in flows)
        for f in flows:
            if seen[f["title"]] > 1:
                f["title"] = f"{f['title']} ({f['screen_name']})"[:80]
        flows.sort(key=lambda f: (-f["successes"], f["title"]))
        return flows[:MAX_FLOWS], groups

    def _operation(self, label: str, heading: str, row: bool) -> str:
        m = COMMIT_WORDS.search(label or "")
        verb = norm(m.group(1)) if m else norm((label or "submit").split()[0])
        if verb in ("save", "submit", "store", "add", "create", "register", "record", "post") and not row:
            if re.search(r"\b(edit|update|modify|change)\b", norm(heading)):
                return "update"
            return "create"
        return {"authorise": "authorize", "check out": "checkout"}.get(verb, verb)

    def _flow(self, insts: list[dict[str, Any]]) -> dict[str, Any] | None:
        succ = [i for i in insts if i["outcome"] == "success"]
        fails = [i for i in insts if i["outcome"] in ("failure", "refused")]
        ref = (succ or insts)[-1]
        screen = self.screens.get(ref["screen"]) or {}
        row = bool(ref.get("row"))
        op = self._operation(ref["commit"], ref.get("page_heading") or "", row)
        entity = screen.get("entity") or entity_of(ref["commit"], ref.get("page_heading") or "", exclude=self.app_words)
        if row:
            sight = next((s for i in reversed(succ or insts) for s in [self.sightings.get(
                norm((i.get("row") or {}).get("row", {}).get((i.get("row") or {}).get("key_column") or "", "")))] if s),
                None)
            if sight:
                entity = sight[-1]["entity"] or entity
        title = f"{op.capitalize()} {entity}".strip() if entity else ref["commit"]
        roles = Counter(i["role"] for i in succ if i.get("role"))
        base = {"title": title[:80], "operation": op, "entity": entity, "screen": ref["screen"],
                "screen_name": screen.get("name") or "", "commit": ref["commit"], "kind": "row_action" if row else "form",
                "seen": len(insts), "successes": len(succ), "failures": len(fails),
                "roles": [r for r, _ in roles.most_common()],
                "failure_messages": [m for i in fails for m in i["messages"]][:10],
                "success_messages": [m for i in succ for m in i["messages"]][:5]}
        if row:
            return self._row_flow(base, insts, succ)
        if not succ:
            base["parameters"] = self._params(insts, insts)
            base["usable"] = False
            return base
        params = self._params(succ, insts)
        if not params:
            return None
        base["parameters"] = params
        base["start_url"] = urlsplit(ref["url"]).path + (f"?{urlsplit(ref['url']).query}" if urlsplit(ref["url"]).query
                                                          else "")
        base["steps"] = self._steps(succ, params, ref)
        base["verify"] = self._success_pattern(succ, params)
        key = self._key_param(params, succ)
        if key:
            base["key_param"] = key
            listed = self._listed(key, params, succ)
            if listed:
                base["exists_check"] = listed
                base["verify"]["record_listed"] = {"url": listed["url"], "column": listed["column"], "param": key}
            init = Counter(self._first_status_after(i, key) for i in succ)
            init.pop(None, None)
            if init:
                base["initial_status"] = init.most_common(1)[0][0]
        base["usable"] = True
        base["consistency"] = self._consistency(succ)
        return base

    def _params(self, succ: list[dict[str, Any]], all_insts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        order: list[str] = []
        for i in succ:
            for label in i["order"]:
                if label not in order:
                    order.append(label)
        form = list((self.screens.get(succ[-1]["screen"]) or {}).get("fields", {}).keys())
        order.sort(key=lambda lbl: (form.index(lbl) if lbl in form else len(form) + order.index(lbl)))
        params = []
        used_names: set[str] = set()
        for label in order:
            vals = [i["values"].get(label) for i in succ]
            present = [v for v in vals if v not in (None, "")]
            if not present:
                continue
            touched = any(label in i["touched"] for i in succ)
            if not touched and len(set(present)) == 1 and len(succ) > 1:
                continue  # the application fills it in itself
            meta = next((i["meta"][label] for i in reversed(succ) if label in i["meta"]), {})
            name = param_name(label)
            while name in used_names:
                name += "_2"
            used_names.add(name)
            ftype = _field_type(label, meta.get("tag"), meta.get("type"), present)
            p: dict[str, Any] = {"name": name, "field": label, "type": ftype,
                                 "required": bool(meta.get("required")) or len(present) == len(succ),
                                 "example": present[-1], "values_seen": sorted(set(present))[:10]}
            if meta.get("options"):
                p["options"] = [o for o in meta["options"] if o and not re.match(r"^\s*(--|select|choose|please)", o,
                                                                                  re.I)][:120]
            if ftype == "date":
                fmt = _date_format(present)
                if fmt:
                    p["format"] = fmt
            if len(succ) >= 2 and len(set(present)) == 1 and touched:
                p["default"] = present[0]
            if len(present) < len(succ):
                p["optional"] = True
                p["required"] = False
            params.append(p)
        return params

    def _steps(self, succ: list[dict[str, Any]], params: list[dict[str, Any]], ref: dict[str, Any]) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = [{"action": "navigate", "url": urlsplit(ref["url"]).path +
                                        (f"?{urlsplit(ref['url']).query}" if urlsplit(ref["url"]).query else "")}]
        screen = safe_key(f"{(self.screens.get(ref['screen']) or {}).get('name') or ''}")
        by_label = {p["field"]: p for p in params}
        click_count: Counter[str] = Counter(c["label"] for i in succ for c in {c["label"]: c for c in i["clicks"]}.values())
        keep_clicks = {c for c, n in click_count.items() if n * 2 >= len(succ) and c and not NOT_COMMIT.search(c)
                       and c not in by_label}
        placed: set[str] = set()
        for label in [p["field"] for p in params]:
            for c in ref["clicks"]:
                if c["after"] is None and c["label"] in keep_clicks and c["label"] not in placed and not steps[1:]:
                    steps.append({"action": "click", "screen": screen, "target": c["label"]})
                    placed.add(c["label"])
            p = by_label.get(label)
            if p:
                act = {"select": "select", "check": "check"}.get(p["type"], "fill")
                st = {"action": act, "screen": screen, "target": label, "param": p["name"]}
                if p.get("optional"):
                    st["optional"] = True
                steps.append(st)
            for c in ref["clicks"]:
                if c["after"] == label and c["label"] in keep_clicks and c["label"] not in placed:
                    steps.append({"action": "click", "screen": screen, "target": c["label"]})
                    placed.add(c["label"])
        for p in params:
            if not any(s.get("param") == p["name"] for s in steps):
                act = {"select": "select", "check": "check"}.get(p["type"], "fill")
                steps.append({"action": act, "screen": screen, "target": p["field"], "param": p["name"],
                              **({"optional": True} if p.get("optional") else {})})
        steps.append({"action": "click", "screen": screen, "target": ref["commit"], "commit": True})
        return steps

    @staticmethod
    def _success_pattern(succ: list[dict[str, Any]], params: list[dict[str, Any]]) -> dict[str, Any]:
        patterns: Counter[str] = Counter()
        for i in succ:
            for m in i["messages"]:
                pattern = re.escape(m[:160])
                for p in sorted(params, key=lambda p: -len(str(i["values"].get(p["field"]) or ""))):
                    ex = str(i["values"].get(p["field"]) or "")
                    if len(ex) >= 2 and re.escape(ex) in pattern:
                        pattern = pattern.replace(re.escape(ex), "{" + p["name"] + "}")
                patterns[pattern] += 1
        if not patterns:
            return {}
        best, _ = max(patterns.items(), key=lambda kv: (kv[1], "{" in kv[0]))
        return {"success_pattern": best}

    def _key_param(self, params: list[dict[str, Any]], succ: list[dict[str, Any]]) -> str | None:
        named = [p for p in params if p["type"] == "text" and KEY_HEADER.search(norm(p["field"]))]
        if named:
            return named[0]["name"]
        for p in params:
            vals = [i["values"].get(p["field"]) for i in succ]
            if p["type"] == "text" and all(vals) and len(set(vals)) == len(vals) and any(norm(v) in self.sightings
                                                                                       for v in vals):
                return p["name"]
        return None

    def _listed(self, key: str, params: list[dict[str, Any]], succ: list[dict[str, Any]]) -> dict[str, Any] | None:
        label = next(p["field"] for p in params if p["name"] == key)
        for i in reversed(succ):
            v = i["values"].get(label)
            s = self.sightings.get(norm(v or ""))
            if not s:
                continue
            first = next((x for x in s if x["obs"] and i["obs"] and x["obs"] > i["obs"]), s[-1])
            path = first["path"]
            url = path
            for qpath, qk, qv in self.query_keys:
                if norm(qv) == norm(v) and qpath == path:
                    url = f"{path}?{qk}={{{key}}}"
                    break
            return {"url": url, "list_url": path, "column": first["key_column"], "param": key}
        return None

    def _first_status_after(self, inst: dict[str, Any], key: str) -> str | None:
        label = next((lbl for lbl in inst["values"] if param_name(lbl) == key), None)
        v = inst["values"].get(label or "")
        for s in self.sightings.get(norm(v or ""), []):
            if s["status"] and (not inst["obs"] or not s["obs"] or s["obs"] > inst["obs"]):
                return s["status"]
        return None

    @staticmethod
    def _consistency(succ: list[dict[str, Any]]) -> float:
        if len(succ) < 2:
            return 0.0
        sets = [frozenset(i["order"]) for i in succ]
        common = frozenset.intersection(*sets)
        union = frozenset.union(*sets)
        return round(len(common) / max(1, len(union)), 2)

    def _row_flow(self, base: dict[str, Any], insts: list[dict[str, Any]], succ: list[dict[str, Any]]) -> dict[str, Any] | None:
        ref = (succ or insts)[-1]
        r = ref.get("row") or {}
        kc, sc = r.get("key_column"), r.get("status_column")
        if not kc:
            base["usable"] = False
            base["parameters"] = []
            return base
        key = param_name(kc)
        before, after = Counter(), Counter()
        for i in succ:
            row = (i.get("row") or {}).get("row") or {}
            if sc and row.get(sc):
                before[row[sc]] += 1
            st = self._next_status(row.get(kc), i["obs"], row.get(sc) if sc else None)
            if st:
                after[st] += 1
        list_path = urlsplit(ref["url"]).path
        url = list_path
        for qpath, qk, qv in self.query_keys:
            if qpath == list_path and any(norm(qv) == norm(((i.get("row") or {}).get("row") or {}).get(kc)) for i in insts):
                url = f"{list_path}?{qk}={{{key}}}"
                break
        base.update({
            "parameters": [{"name": key, "field": kc, "type": "text", "required": True,
                            "example": ((ref.get("row") or {}).get("row") or {}).get(kc)}],
            "start_url": url, "key_param": key, "target_kind": "existing",
            "steps": [{"action": "navigate", "url": url},
                      {"action": "open_row", "control": ref["commit"], "param": key, "commit": True}],
            "exists_check": {"url": url, "list_url": list_path, "column": kc, "param": key,
                             **({"status_column": sc} if sc else {})},
            "usable": bool(succ)})
        pattern = self._success_pattern(succ, base["parameters"])
        if pattern:
            for i in succ:
                v = ((i.get("row") or {}).get("row") or {}).get(kc)
                if v and re.escape(v) in pattern["success_pattern"]:
                    pattern["success_pattern"] = pattern["success_pattern"].replace(re.escape(v), "{" + key + "}")
        base["verify"] = pattern
        if sc:
            base["target"] = {"status_column": sc}
            if before:
                base["target"]["requires_status"] = before.most_common(1)[0][0]
            if after:
                base["expected_status"] = after.most_common(1)[0][0]
                base["verify"]["record_listed"] = {"url": url, "column": kc, "param": key, "status_column": sc}
        return base

    def _next_status(self, key: str | None, obs: int | None, current: str | None) -> str | None:
        for s in self.sightings.get(norm(key or ""), []):
            if obs and s["obs"] and s["obs"] <= obs:
                continue
            if s["status"] and s["status"] != current:
                return s["status"]
        return None

    def _lifecycles(self, flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_entity: dict[str, dict[str, Any]] = {}
        commits = sorted([i for i in self.instances if i["outcome"] == "success"], key=lambda i: i["obs"] or 0)
        flow_of = {f["group"]: f for f in flows}

        def flow_title(inst: dict[str, Any]) -> str:
            gk = f"{inst['screen']}|{norm(inst['commit'])}|{'row' if inst.get('row') else 'form'}"
            return (flow_of.get(gk) or {}).get("title") or inst["commit"]

        for key, sights in self.sightings.items():
            statuses = [(s["obs"], s["status"], s) for s in sights if s["status"]]
            if not statuses:
                continue
            ent = next((s["entity"] for s in sights if s["entity"]), "") or "record"
            lc = by_entity.setdefault(ent, {"entity": ent, "status_column": statuses[0][2]["status_column"],
                                            "statuses": [], "transitions": {}, "initial": Counter(), "records": 0})
            lc["records"] += 1
            for _, st, _s in statuses:
                if st not in lc["statuses"]:
                    lc["statuses"].append(st)
            first_obs, first_status, _ = statuses[0]
            creator = next((i for i in commits if not i.get("row") and (i["obs"] or 0) < (first_obs or 0)
                            and any(norm(v) == key for v in i["values"].values())), None)
            if creator:
                lc["initial"][(first_status, flow_title(creator), creator.get("role") or "")] += 1
            prev = statuses[0]
            for cur in statuses[1:]:
                if cur[1] == prev[1]:
                    prev = cur
                    continue
                cause = None
                for i in commits:
                    if (prev[0] or 0) < (i["obs"] or 0) < (cur[0] or 0):
                        row = (i.get("row") or {}).get("row") or {}
                        if any(norm(v) == key for v in row.values()) or any(norm(v) == key for v in i["values"].values()):
                            cause = i
                tk = f"{prev[1]}>{cur[1]}"
                t = lc["transitions"].setdefault(tk, {"from": prev[1], "to": cur[1], "count": 0, "actions": Counter(),
                                                      "roles": Counter()})
                t["count"] += 1
                if cause:
                    t["actions"][flow_title(cause)] += 1
                    if cause.get("role"):
                        t["roles"][cause["role"]] += 1
                prev = cur
        out = []
        for lc in by_entity.values():
            trans = list(lc["transitions"].values())
            # order statuses along the observed transitions (initial first)
            init = lc["initial"].most_common(1)[0][0] if lc["initial"] else None
            order = [init[0]] if init else []
            changed = True
            while changed:
                changed = False
                for t in sorted(trans, key=lambda t: -t["count"]):
                    if order and t["from"] == order[-1] and t["to"] not in order:
                        order.append(t["to"])
                        changed = True
            if not order and trans:
                order = [trans[0]["from"], trans[0]["to"]]
            for st in lc["statuses"]:
                if st not in order:
                    order.append(st)
            out.append({"entity": lc["entity"], "status_column": lc["status_column"], "statuses": order,
                        "initial": {"status": init[0], "action": init[1], "role": init[2]} if init else None,
                        "transitions": [{"from": t["from"], "to": t["to"], "count": t["count"],
                                         "action": t["actions"].most_common(1)[0][0] if t["actions"] else None,
                                         "roles": [r for r, _ in t["roles"].most_common()]}
                                        for t in sorted(trans, key=lambda t: order.index(t["from"])
                                                        if t["from"] in order else 99)],
                        "records": lc["records"]})
        return out

    # -- rules -----------------------------------------------------------------------------------------------------

    def _rules(self, flows: list[dict[str, Any]], groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        rules: dict[str, dict[str, Any]] = {}

        def add(flow: dict[str, Any], rule: dict[str, Any], text: str, source: str, evidence: str | None = None,
                confidence: float = 0.6) -> None:
            field = rule.get("field") or rule.get("resource")
            key = safe_key(f"{flow['title']}: {rule['type']} {field}")
            cur = rules.get(key)
            if cur:
                cur["seen"] += 1
                if evidence and evidence not in cur["evidence"]:
                    cur["evidence"].append(evidence)
                cur["confidence"] = max(cur["confidence"], confidence)
                if source == "application message":
                    cur["source"] = source
                cur["rule"].update({k: v for k, v in rule.items() if v is not None})
                return
            rules[key] = {"key": key, "flow": flow["title"], "rule": {**rule, "applies_to": flow["title"]},
                          "text": text, "source": source, "evidence": [evidence] if evidence else [], "seen": 1,
                          "confidence": confidence}

        for flow in flows:
            params = flow.get("parameters") or []
            if flow["kind"] != "form":
                continue
            by_label = {p["field"]: p for p in params}
            date_p = next((p for p in params if p["type"] == "date"), None)
            time_p = next((p for p in params if p["type"] == "time"), None)
            screen = self.screens.get(flow["screen"]) or {}
            for p in params:
                f = screen.get("fields", {}).get(p["field"], {})
                cons = f.get("constraints") or {}
                if p["type"] == "date":
                    add(flow, {"type": "date", "field": p["name"], "format": p.get("format")},
                        f"{p['field']} is a date" + (f" ({p['format']})" if p.get("format") else ""), "observed values",
                        confidence=0.5)
                if p["type"] == "time":
                    add(flow, {"type": "time", "field": p["name"]}, f"{p['field']} is a time (HH:MM)", "observed values",
                        confidence=0.5)
                if p["type"] == "select" and p.get("options"):
                    add(flow, {"type": "allowed_values", "field": p["name"], "source": "options"},
                        f"{p['field']} must be one of the options the screen offers", "screen options", confidence=0.6)
                if cons.get("pattern"):
                    add(flow, {"type": "pattern", "field": p["name"], "regex": cons["pattern"]},
                        f"{p['field']} must match the format {cons['pattern']}", "field constraint", confidence=0.7)
                if cons.get("min") or cons.get("max"):
                    try:
                        add(flow, {"type": "range", "field": p["name"],
                                   "min": float(cons["min"]) if cons.get("min") else None,
                                   "max": float(cons["max"]) if cons.get("max") else None},
                            f"{p['field']} between {cons.get('min') or '-'} and {cons.get('max') or '-'}",
                            "field constraint", confidence=0.7)
                    except ValueError:
                        pass
                if f.get("required"):
                    add(flow, {"type": "required", "field": p["name"]}, f"{p['field']} is required", "field constraint",
                        confidence=0.7)
            for inst in groups.get(flow.get("group") or "", []):
                if inst["outcome"] != "failure":
                    continue
                for msg in inst["messages"] + inst["invalid"]:
                    self._rule_from_message(flow, inst, msg, by_label, date_p, time_p, add)
        return sorted(rules.values(), key=lambda r: (-r["confidence"], r["key"]))

    def _rule_from_message(self, flow: dict[str, Any], inst: dict[str, Any], msg: str, by_label: dict[str, Any],
                           date_p: dict[str, Any] | None, time_p: dict[str, Any] | None, add: Any) -> None:
        text = norm(msg)

        def field_for() -> dict[str, Any] | None:
            # a value the person typed that the message repeats names the field best ("Bus BUS-101 is ...")
            hits = [p for lbl, p in by_label.items() if inst["values"].get(lbl) and len(inst["values"][lbl]) >= 2
                    and norm(inst["values"][lbl]) in text]
            hits.sort(key=lambda p: -len(inst["values"].get(p["field"]) or ""))
            if hits:
                # prefer the field whose label is also in the message
                named = [p for p in hits if overlap(p["field"], msg) > 0]
                return (named or hits)[0]
            scored = sorted(((overlap(lbl, msg), p) for lbl, p in by_label.items()), key=lambda x: -x[0])
            return scored[0][1] if scored and scored[0][0] > 0 else None

        p = field_for()
        if re.search(r"\b(already exists?|must be unique|duplicate|already (used|taken|registered|in use))\b", text):
            if p:
                add(flow, {"type": "unique", "field": p["name"]}, f"{p['field']} must be unique", "application message",
                    evidence=msg, confidence=0.85)
            return
        if re.search(r"\b(not available|already (assigned|booked|scheduled|allocated)|overlap\w*|conflict\w*|clash\w*|"
                     r"double.?book\w*)\b", text) and date_p:
            if p and p is not date_p and p is not time_p:
                add(flow, {"type": "no_overlap", "resource": p["name"], "date": date_p["name"],
                           "time": time_p["name"] if time_p else None,
                           "duration_minutes": self._overlap_duration(flow, p, date_p, time_p)},
                    f"A {p['field'].lower()} cannot be booked twice at overlapping times", "application message",
                    evidence=msg, confidence=0.8)
            return
        if re.search(r"\b(today or later|in the future|future date|cannot be in the past|not in the past|"
                     r"must (depart|start|be) (today|later|after today)|past dates?)\b", text) and date_p:
            add(flow, {"type": "date", "field": date_p["name"], "min_days": 0, "format": date_p.get("format")},
                f"{date_p['field']} must be today or later", "application message", evidence=msg, confidence=0.85)
            return
        if re.search(r"\b(is required|are required|required field|cannot be (empty|blank)|must be (entered|provided|"
                     r"filled|selected)|please (enter|select|choose|fill))\b", text) and p:
            add(flow, {"type": "required", "field": p["name"]}, f"{p['field']} is required", "application message",
                evidence=msg, confidence=0.85)
            return
        m = re.search(r"\b(?:at least|minimum(?: of)?|not less than|greater than or equal to)\s+(\d+(?:\.\d+)?)", text)
        mx = re.search(r"\b(?:at most|maximum(?: of)?|not more than|less than or equal to|up to)\s+(\d+(?:\.\d+)?)", text)
        if (m or mx) and p:
            add(flow, {"type": "range", "field": p["name"], "min": float(m.group(1)) if m else None,
                       "max": float(mx.group(1)) if mx else None},
                f"{p['field']} limits: {msg}", "application message", evidence=msg, confidence=0.75)
            return
        if re.search(r"\b(invalid|not valid|wrong format|format)\b", text):
            for cand in ([p] if p else []) + [x for x in (date_p, time_p) if x]:
                if cand and cand["type"] in ("date", "time"):
                    add(flow, {"type": cand["type"], "field": cand["name"], **({"format": cand.get("format")}
                                                                                if cand["type"] == "date" else {})},
                        f"{cand['field']} must be a valid {cand['type']}", "application message", evidence=msg,
                        confidence=0.75)
            return

    def _overlap_duration(self, flow: dict[str, Any], res: dict[str, Any], date_p: dict[str, Any],
                          time_p: dict[str, Any] | None) -> int:
        """How long a booking lasts, from what the application refused and accepted (at least 60 minutes)."""
        if not time_p:
            return 24 * 60
        groups = [i for i in self.instances if f"{i['screen']}|{norm(i['commit'])}|form" == flow.get("group")]
        bookings = []
        for i in groups:
            if i["outcome"] != "success":
                continue
            d = parse_date(i["values"].get(date_p["field"]), date_p.get("format"))
            t = parse_time(i["values"].get(time_p["field"]))
            if d and t:
                bookings.append((norm(i["values"].get(res["field"])), _dt.datetime.combine(d, t), i["obs"] or 0))
        refused_gaps, accepted_gaps = [], []
        for i in groups:
            d = parse_date(i["values"].get(date_p["field"]), date_p.get("format"))
            t = parse_time(i["values"].get(time_p["field"]))
            if not (d and t):
                continue
            at = _dt.datetime.combine(d, t)
            who = norm(i["values"].get(res["field"]))
            earlier = [b for b in bookings if b[0] == who and b[2] < (i["obs"] or 0)]
            if not earlier:
                continue
            gap = min(abs((at - b[1]).total_seconds()) / 60 for b in earlier)
            if i["outcome"] == "failure" and any(norm(i["values"].get(res["field"]) or "") in norm(m)
                                                 for m in i["messages"]):
                refused_gaps.append(gap)
            elif i["outcome"] == "success" and gap > 0:
                accepted_gaps.append(gap)
        if not refused_gaps:
            return 60
        need = max(refused_gaps)
        duration = int((need // 30 + 1) * 30)
        if accepted_gaps:
            duration = min(duration, int(min(accepted_gaps))) if min(accepted_gaps) > need else duration
        return max(60, duration)

    def _attach_overlap(self, flows: list[dict[str, Any]], rules: list[dict[str, Any]]) -> None:
        """A flow with a no-overlap rule reads existing bookings from the list where its records appear."""
        for flow in flows:
            listed = flow.get("exists_check")
            if not listed or flow["kind"] != "form" or not listed.get("list_url"):
                continue
            headers = next((h for s in self.screens.values() if s["example_url"] == listed["list_url"]
                            for h in s["tables"] if listed["column"] in h), [])
            for r in rules:
                rule = r["rule"]
                if r["flow"] != flow["title"] or rule.get("type") != "no_overlap":
                    continue
                cols = {}
                for pname in (rule.get("resource"), rule.get("date"), rule.get("time")):
                    p = next((x for x in flow["parameters"] if x["name"] == pname), None)
                    if not p:
                        continue
                    best = max(headers, key=lambda h: (overlap(h, p["field"]), -len(h)), default=None)
                    if best and overlap(best, p["field"]) > 0:
                        cols[pname] = best
                if cols.get(rule.get("resource")) and cols.get(rule.get("date")):
                    flow["bookings_source"] = {"url": listed["list_url"], "columns": cols}

    # -- roles, navigation, entities -------------------------------------------------------------------------------

    def _roles(self, flows: list[dict[str, Any]], screens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = {s["id"]: s["name"] for s in screens}
        kinds = {s["id"]: s.get("type") for s in screens}
        out = []
        flow_by_group = {f["group"]: f["title"] for f in flows}
        for i in self.instances:
            if i["outcome"] == "success" and i.get("role") and i["role"] in self.role_info:
                gk = f"{i['screen']}|{norm(i['commit'])}|{'row' if i.get('row') else 'form'}"
                self.role_info[i["role"]]["performed"][flow_by_group.get(gk, i["commit"])] += 1
        for key, info in self.role_info.items():
            offered = set()
            for s in screens:
                if key in s["roles"]:
                    offered |= {b["label"] for b in s["buttons"] + s["row_actions"] if key in b["roles"]
                                and COMMIT_WORDS.search(b["label"]) and not NOT_COMMIT.search(b["label"])}
            out.append({"key": key, "name": info["name"], "users": sorted(info["users"])[:10],
                        "screens": list(dict.fromkeys(names.get(s, s) for s, _ in info["screens"].most_common(25)
                                                      if kinds.get(s) not in ("refused", "sign-in", "verification"))),
                        "performed": [{"flow": f, "times": n} for f, n in info["performed"].most_common()],
                        "offered_actions": sorted(offered)[:40],
                        "refused": sorted({f"{names.get(r['screen'], r['path'])}: {r['message'].rstrip('.')}"
                                           for r in self.refusals if r["role"] == key})[:10]})
        out.sort(key=lambda r: -sum(p["times"] for p in r["performed"]))
        return out

    def _navigation(self, screens: list[dict[str, Any]]) -> dict[str, Any]:
        names = {s["id"]: s["name"] for s in screens}
        menu = []
        if self.pages_logged_in >= 3:
            menu = [{"label": lbl, "href": self.links_seen.get(lbl)} for lbl, n in self.link_pages.most_common(20)
                    if n >= 0.6 * self.pages_logged_in]
        edges = [{"from": names.get(e["from"], e["from"]), "to": names.get(e["to"], e["to"]), "from_id": e["from"],
                  "to_id": e["to"], "via": e["via"].most_common(1)[0][0] if e["via"] else None, "count": e["count"]}
                 for e in sorted(self.edges.values(), key=lambda e: -e["count"])][:200]
        return {"menu": menu, "edges": edges}

    def _entities(self, screens: list[dict[str, Any]], flows: list[dict[str, Any]],
                  lifecycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ents: dict[str, dict[str, Any]] = {}
        for s in screens:
            if s["entity"] and s["type"] not in ("sign-in", "verification"):
                e = ents.setdefault(s["entity"], {"name": s["entity"], "screens": [], "flows": [], "fields": [],
                                                  "statuses": [], "key_field": None})
                e["screens"].append(s["name"])
        for f in flows:
            if f.get("entity"):
                e = ents.setdefault(f["entity"], {"name": f["entity"], "screens": [], "flows": [], "fields": [],
                                                  "statuses": [], "key_field": None})
                e["flows"].append(f["title"])
                if f["kind"] == "form" and f.get("operation") == "create":
                    e["fields"] = [p["field"] for p in f.get("parameters") or []]
                if f.get("key_param"):
                    e["key_field"] = next((p["field"] for p in f.get("parameters") or [] if p["name"] == f["key_param"]),
                                          e["key_field"])
        for lc in lifecycles:
            e = ents.setdefault(lc["entity"], {"name": lc["entity"], "screens": [], "flows": [], "fields": [],
                                               "statuses": [], "key_field": None})
            e["statuses"] = lc["statuses"]
        return sorted(ents.values(), key=lambda e: -(len(e["flows"]) * 3 + len(e["screens"])))


# ---- the engine: digest the journal, keep the model, promote stable findings to knowledge ----------------------------

class Understanding:
    def __init__(self, kb: Any, journal: Any, llm: Any = None) -> None:
        self.kb = kb
        self.journal = journal
        self.llm = llm

    def model(self, app_id: str) -> dict[str, Any] | None:
        row = self.kb.store.query_one("SELECT data, upto, updated_at FROM app_model WHERE application_id = ?",
                                      (self.kb.application(app_id)["id"],))
        if not row:
            return None
        d = loads(row["data"])
        d["_upto"], d["_updated_at"] = row["upto"], row["updated_at"]
        return d

    def pending(self, app_id: str) -> int:
        """Observations recorded since the model was last built."""
        row = self.kb.store.query_one("SELECT upto FROM app_model WHERE application_id = ?",
                                      (self.kb.application(app_id)["id"],))
        upto = int((row or {}).get("upto") or 0)
        r = self.kb.store.query_one("SELECT COUNT(*) AS n FROM observations WHERE application_id = ? AND id > ?",
                                    (self.kb.application(app_id)["id"], upto))
        return int((r or {}).get("n") or 0)

    def digest(self, app_id: str, *, by: str | None = "passive-learning", promote: bool = True,
               use_ai: bool = True) -> dict[str, Any]:
        app = self.kb.application(app_id)
        entries = self.journal.entries(app["id"])
        model = Miner(app, app.get("profile") or {}).run(entries)
        row = self.kb.store.query_one("SELECT promoted FROM app_model WHERE application_id = ?", (app["id"],))
        promoted = loads((row or {}).get("promoted"), {})
        if use_ai and self.llm is not None:
            self._ai_purposes(model, promoted)
        changes = self._promote(app, model, promoted, by=by) if promote else []
        model["last_changes"] = changes
        upto = entries[-1]["_id"] if entries else 0
        now = now_iso()
        self.kb.store.execute("INSERT INTO app_model (application_id, data, upto, promoted, updated_at) VALUES "
                              "(?,?,?,?,?) ON CONFLICT(application_id) DO UPDATE SET data = excluded.data, "
                              "upto = excluded.upto, promoted = excluded.promoted, updated_at = excluded.updated_at",
                              (app["id"], dumps(model), upto, dumps(promoted), now))
        if changes:
            self.kb.audit.record("learning.understood", actor=by, target=app["id"],
                                 details={"changes": len(changes), "flows": len(model["flows"]),
                                          "screens": len(model["screens"])})
        return {"model": self.model(app["id"]), "changes": changes}

    def _ai_purposes(self, model: dict[str, Any], promoted: dict[str, Any]) -> None:
        """Optional: one short business purpose per flow from the local model (cached; at most 4 calls a digest)."""
        cache = promoted.setdefault("_purposes", {})
        calls = 0
        for f in model["flows"]:
            ck = digest([f["title"], f.get("screen_name"), [p["field"] for p in f.get("parameters") or []]])[:16]
            if ck in cache:
                f["purpose"] = cache[ck]
                continue
            if calls >= 4:
                continue
            calls += 1
            block, _ = wrap_untrusted(dumps({"task": f["title"], "screen": f.get("screen_name"),
                                             "fields": [p["field"] for p in f.get("parameters") or []],
                                             "messages": f.get("success_messages")[:2]}), 1200)
            try:
                reply = self.llm.chat([{"role": "system", "content": SYSTEM_RULES + ' Return JSON {"purpose": "..."}: '
                                        "what this task achieves for the business, under 18 words."},
                                       {"role": "user", "content": block}], max_tokens=80, json_mode=True)
                data = extract_json(reply)
                purpose = str((data or {}).get("purpose") or "")[:140] if isinstance(data, dict) else ""
            except Exception:
                break  # model offline: names stay deterministic
            if purpose:
                cache[ck] = purpose
                f["purpose"] = purpose

    def _promote(self, app: dict[str, Any], model: dict[str, Any], promoted: dict[str, Any], *,
                 by: str | None) -> list[dict[str, Any]]:
        """Turn stable findings into versioned knowledge, once per change (never re-proposes the same finding)."""
        changes: list[dict[str, Any]] = []

        def propose(kind: str, key: str, data: dict[str, Any], state: str, confidence: float, note: str) -> None:
            key = safe_key(key)
            h = digest(data)
            pk = f"{kind}:{key}"
            if promoted.get(pk) == h:
                return
            latest = self.kb.latest(app["id"], kind, key)
            if latest and latest["state"] == "rejected" and promoted.get(pk + ":rejected") == h:
                return
            try:
                entry = self.kb.propose(app["id"], kind, key, data, source="passive_learning", state=state,
                                        confidence=confidence, by=by, note=note)
            except Exception:
                return
            promoted[pk] = h
            if entry.get("state") == "rejected":
                promoted[pk + ":rejected"] = h
            changes.append({"kind": kind, "key": key, "entry": entry["id"], "state": entry["state"],
                            "version": entry["version"]})

        profile = dict(app.get("profile") or {})
        changed_profile = False
        if model.get("login") and not (profile.get("login") or {}).get("username"):
            login = {k: v for k, v in model["login"].items() if v}
            if login.get("url"):
                login["url"] = urljoin(app["base_url"], login["url"])
            profile["login"] = {**(profile.get("login") or {}), **login}
            changed_profile = True
        if model.get("identity") and not (profile.get("identity") or {}).get("pattern"):
            profile["identity"] = model["identity"]
            changed_profile = True
        if changed_profile:
            self.kb.set_profile(app["id"], profile, by=by)
            changes.append({"kind": "profile", "key": "sign-in", "state": "learned"})

        for s in model["screens"]:
            if s["type"] in ("sign-in", "verification"):
                continue
            data = {"url_path": s["path"], "heading": s["name"], "type": s["type"], "entity": s["entity"],
                    "fields": [{k: f.get(k) for k in ("label", "type", "tag", "required", "options", "secret", "section")}
                               for f in s["fields"]],
                    "buttons": [{"label": b["label"]} for b in s["buttons"]],
                    "row_actions": [b["label"] for b in s["row_actions"]],
                    "tables": [{"headers": h} for h in s["tables"]]}
            propose("screen", f"{s['name']} {s['path']}", data, "discovered", 0.6, "seen while you browsed")
        for f in model["flows"]:
            if not f.get("usable"):
                continue
            consistent = f["successes"] >= 2 and (f["kind"] == "row_action" or f.get("consistency", 0) >= 0.6)
            wf = {k: f[k] for k in ("title", "operation", "entity", "parameters", "steps", "verify", "start_url")
                  if k in f}
            wf["parameters"] = [{k: v for k, v in p.items() if k != "values_seen"} for p in wf.get("parameters") or []]
            wf.update({"commits": True, "roles": f["roles"], "learned_from": "passive learning",
                       "evidence": {"successes": min(f["successes"], 5), "failures": min(f["failures"], 5)}})
            for k in ("exists_check", "bookings_source", "target_kind", "target", "expected_status"):
                if f.get(k):
                    wf[k] = f[k]
            if f.get("purpose"):
                wf["purpose"] = f["purpose"]
            propose("workflow", f["title"], wf, "awaiting_approval" if consistent else "discovered",
                    0.85 if consistent else 0.55,
                    f"learned by watching: completed {f['successes']} time(s)"
                    + (f", rejected by the application {f['failures']} time(s)" if f["failures"] else ""))
        for r in model["rules"]:
            from .rules import validate_rule

            rule = {k: v for k, v in r["rule"].items() if v is not None}
            if validate_rule(rule):
                continue
            rule["description"] = r["text"]
            propose("rule", r["key"], rule, "discovered" if r["source"] == "application message" else "ai_suggested",
                    r["confidence"], f"{r['source']}" + (f": {r['evidence'][0]}" if r["evidence"] else ""))
        for role in model["roles"]:
            if role["key"].startswith("user "):
                continue
            propose("role", role["key"], {"name": role["name"], "screens": role["screens"],
                                          "performs": [p["flow"] for p in role["performed"]],
                                          "offered_actions": role["offered_actions"], "refused": role["refused"]},
                    "discovered", 0.7, "seen while you browsed")
        for lc in model["lifecycles"]:
            if len(lc["statuses"]) < 2:
                continue
            propose("lifecycle", lc["entity"] or "record", {k: lc[k] for k in ("entity", "status_column", "statuses",
                                                                                "initial", "transitions")},
                    "discovered", 0.7, "status changes seen while you browsed")
        return changes
