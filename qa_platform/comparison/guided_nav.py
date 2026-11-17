"""23.10.0 Integration Comparison: Guided Browser Automation for "Add Applications -> Connect to Application".

Replaces typing navigation steps by hand. The tester presses Start Guided Recording, a maximized browser opens on the
application (through the same live session the filters use), they sign in and go to the report as they always do:
menus, the report, its filters, the next pages, a detail page. Stop & Save turns what they did into:

* steps        the navigation the extractor replays (menus, tabs, links), in the step format it already runs
* filters      the controls the tester set on the report page before the data showed (kept apart from their values,
               so the values can be changed later in the Filters panel without recording again)
* pagination   seen, but not replayed: the extractor reads every page itself (Next, numbers, Load more, scrolling),
               with duplicate and loop protection, and says when not every page could be read
* detail       a click from a table row to a detail page becomes a parameterized address (``/orders/{key}``) taken
               from the row's own key value, so the detail of every record is read, not only the one clicked
* sign-in      never recorded: password fields arrive as "[secret]" and the sign-in page's steps are dropped; at
               replay the saved login or the tester signs in (MFA, CAPTCHA are never bypassed)

Pure conversion (``events_to_recording``) is separate from the browser part so it is tested on its own. Read-only:
a click whose label looks like a change (Save, Delete, Approve ...) is never kept as a step.
"""

from __future__ import annotations

import datetime as _dt
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..errors import QAError

AGENT_JS = Path(__file__).resolve().parents[2] / "skyguard_agent" / "js" / "agent_dom.js"
PAGER = re.compile(r"^\s*(next|previous|prev|first|last|more|load more|show more|›|»|‹|«|>|<|>>|<<|\d{1,4}|"
                   r"next page|previous page|page \d+|go to page \d+)\s*$", re.I)
APPLY = re.compile(r"\b(search|apply|filter|go|show|view|refresh|run|find|submit|load|display|generate)\b", re.I)
CHANGE = re.compile(r"\b(save|delete|remove|approve|authori[sz]e|reject|cancel|update|create|add|new|submit order|"
                    r"post|pay|confirm|send|upload|import|edit)\b", re.I)
SORT_HEADER = re.compile(r"^(sort|↑|↓|▲|▼)", re.I)
MAX_EVENTS = 2000


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _path(url: str) -> str:
    p = urlsplit(url or "")
    return p.path or "/"


def _target(el: dict[str, Any]) -> dict[str, Any]:
    """How the extractor finds the control again: a stable id or test id first, then its label or visible text."""
    out: dict[str, Any] = {}
    if el.get("test_id"):
        out["selector"] = f"[data-testid=\"{el['test_id']}\"]" if '"' not in el["test_id"] else None
    elif el.get("id") and re.fullmatch(r"[A-Za-z][\w-]{0,80}", el["id"]):
        out["selector"] = f"#{el['id']}"
    label = el.get("label") or el.get("name") or el.get("placeholder")
    if el.get("tag") in ("input", "select", "textarea"):
        if label:
            out["label"] = label[:120]
        elif el.get("name_attr") and re.fullmatch(r"[\w.\-\[\]]{1,80}", el["name_attr"]):
            out.setdefault("selector", f"[name=\"{el['name_attr']}\"]")
    else:
        text = el.get("name") or el.get("text")
        if text:
            out["text"] = text[:120]
    if not out.get("selector"):
        out.pop("selector", None)
    return out


def _detail_template(event: dict[str, Any], next_url: str | None) -> dict[str, Any] | None:
    """A link in a table row that opened another page: the address with the row's key value as a parameter."""
    el = event.get("element") or {}
    table = el.get("table") if isinstance(el.get("table"), dict) else None
    if not table:
        return None
    href = el.get("href") or ""
    target = next_url or ""
    if href and not href.startswith(("http://", "https://")):
        base = urlsplit(event.get("url") or "")
        target = urlunsplit((base.scheme, base.netloc, href if href.startswith("/") else
                             base.path.rsplit("/", 1)[0] + "/" + href, "", "")) if not next_url else next_url
    elif href:
        target = next_url or href
    if not target:
        return None
    cells = [str(c).strip() for c in table.get("cells") or str(table.get("row") or "").split("|")]
    headers = [str(h).strip() for h in table.get("headers") or []]
    for i, cell in enumerate(cells):
        if not cell or len(cell) > 60 or not re.search(r"\w", cell):
            continue
        if cell in target and re.search(rf"(?<![\w-]){re.escape(cell)}(?![\w-])", target):
            column = headers[i] if i < len(headers) and headers[i] else (table.get("column") or f"Column {i + 1}")
            tpl = re.sub(rf"(?<![\w-]){re.escape(cell)}(?![\w-])", "{key}", target, count=1)
            return {"url_template": tpl, "key_column": column, "example_key": cell,
                    "example_url": target, "link_text": (el.get("name") or el.get("text") or "")[:80]}
    return None


def events_to_recording(events: list[dict[str, Any]], *, start_url: str = "") -> dict[str, Any]:
    """What the tester did in the browser -> steps, filters, pagination, detail and what was left out (and why)."""
    evs = [e for e in events if isinstance(e, dict) and e.get("kind") in ("click", "fill", "select", "check",
                                                                          "submit", "navigate")][:MAX_EVENTS]
    # 1. the sign-in page: everything on a page where a secret was typed, until the next page
    login_urls = {_path(e.get("url", "")) for e in evs if e.get("secret") or (e.get("element") or {}).get("secret")}
    login_url = next((e.get("url") for e in evs if e.get("secret") or (e.get("element") or {}).get("secret")), None)
    dropped: list[dict[str, str]] = []
    kept: list[dict[str, Any]] = []
    for e in evs:
        if _path(e.get("url", "")) in login_urls:
            dropped.append({"why": "sign-in (never recorded; the saved login or you sign in at replay)",
                            "what": e.get("kind", "")})
            continue
        kept.append(e)
    # 2. pagination and sorting: read by the extractor itself
    pagination = False
    steps_src: list[dict[str, Any]] = []
    for e in kept:
        el = e.get("element") or {}
        text = (el.get("name") or el.get("text") or "").strip()
        if e["kind"] == "click" and (PAGER.match(text) or "pag" in " ".join(el.get("classes") or []).lower()
                                     or (el.get("section") or "").lower().startswith("pagination")):
            pagination = True
            dropped.append({"why": "pagination (every page is read automatically)", "what": text})
            continue
        if e["kind"] == "click" and el.get("tag") == "th":
            dropped.append({"why": "column sorting (the order does not change the comparison)", "what": text})
            continue
        steps_src.append(e)
    # 3. the report page: the last page the tester stayed on before any detail page
    detail = None
    detail_at = None
    for i, e in enumerate(steps_src):
        if e["kind"] != "click":
            continue
        nxt = next((x for x in steps_src[i + 1:] if _path(x.get("url", "")) != _path(e.get("url", ""))), None)
        tpl = _detail_template(e, nxt.get("url") if nxt else None)
        if tpl:
            detail, detail_at = tpl, i
            break
    master = steps_src[:detail_at] if detail_at is not None else steps_src
    detail_events = steps_src[detail_at + 1:] if detail_at is not None else []
    report_path = _path(master[-1]["url"]) if master else _path(start_url)
    # 4. steps and filters
    steps: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    detail_filters: list[dict[str, Any]] = []
    for e in master:
        el = e.get("element") or {}
        tgt = _target(el)
        if not tgt:
            dropped.append({"why": "the control has no label, text or stable id", "what": e["kind"]})
            continue
        on_report = _path(e.get("url", "")) == report_path
        if e["kind"] == "click":
            text = tgt.get("text") or ""
            if CHANGE.search(text) and not APPLY.search(text):
                dropped.append({"why": "looks like a change in the application (comparison is read-only)",
                                "what": text})
                continue
            step = {"action": "click", **tgt}
            if on_report and APPLY.search(text) and filters:
                step["apply_filters"] = True
            steps.append(step)
        elif e["kind"] in ("fill", "select", "check"):
            value = e.get("value")
            if value == "[secret]":
                continue
            name = tgt.get("label") or tgt.get("text") or tgt.get("selector")
            step = {"action": e["kind"], **tgt, "value": value if e["kind"] != "check" else value in (True, "true")}
            if on_report:
                step["filter"] = name
                filters.append({"label": name, "control": e["kind"], "value": step["value"],
                                "type": (el.get("type") or el.get("tag") or "")})
            steps.append(step)
        elif e["kind"] == "submit":
            continue
    for e in detail_events:
        el = e.get("element") or {}
        if e["kind"] in ("fill", "select", "check") and e.get("value") != "[secret]":
            tgt = _target(el)
            if tgt:
                detail_filters.append({"action": e["kind"], **tgt, "value": e.get("value"),
                                       "filter": tgt.get("label") or tgt.get("text")})
    # a value typed twice into the same control: keep the last one
    seen: dict[str, int] = {}
    compact: list[dict[str, Any]] = []
    for s in steps:
        key = f"{s['action']}|{s.get('label') or s.get('selector') or ''}" if s["action"] != "click" else None
        if key and key in seen:
            compact[seen[key]] = s
            continue
        if key:
            seen[key] = len(compact)
        compact.append(s)
    last_filter = {f["label"]: f for f in filters}
    if detail:
        detail["filters"] = detail_filters
    return {"steps": compact, "filters": list(last_filter.values()), "pagination_seen": pagination,
            "detail": detail, "report_url_path": report_path, "login_url": login_url, "dropped": dropped,
            "events": len(evs), "recorded_at": _now()}


def steps_text(steps: list[dict[str, Any]]) -> str:
    """The recorded steps as the lines the tester can edit (same syntax as the manual Advanced box)."""
    out = []
    for s in steps:
        target = s.get("label") or s.get("text") or s.get("selector") or ""
        if s["action"] == "click":
            out.append(f"click {target}")
        elif s["action"] in ("fill", "select"):
            out.append(f"{s['action']} {target} = {s.get('value', '')}")
        elif s["action"] == "check":
            out.append(f"check {target} = {'yes' if s.get('value') else 'no'}")
        elif s["action"] == "goto":
            out.append(f"open {s.get('url')}")
    return "\n".join(out)


def parse_steps_text(text: str, previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Edited lines back to steps; a line that did not change keeps its recorded selector and filter name."""
    prev = {steps_text([s]): s for s in previous or []}
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line in prev:
            out.append(prev[line])
            continue
        m = re.match(r"^(fill|select)\s+(.+?)\s*=\s*(.*)$", line, re.I)
        if m:
            out.append({"action": m.group(1).lower(), "label": m.group(2), "value": m.group(3),
                        "filter": m.group(2)})
            continue
        m = re.match(r"^check\s+(.+?)\s*=\s*(yes|no|true|false|on|off)$", line, re.I)
        if m:
            out.append({"action": "check", "label": m.group(1),
                        "value": m.group(2).lower() in ("yes", "true", "on"), "filter": m.group(1)})
            continue
        m = re.match(r"^(?:open|go to)\s+(https?://\S+)$", line, re.I)
        if m:
            out.append({"action": "goto", "url": m.group(1)})
            continue
        m = re.match(r"^click\s+(.+)$", line, re.I)
        text = m.group(1) if m else line
        if CHANGE.search(text) and not APPLY.search(text):
            raise QAError("WS_STEP_CHANGES_DATA", f"'{text}' looks like it changes data in the application. The "
                          "comparison only reads; remove this step.", status=422)
        out.append({"action": "click", "text": text})
    if len(out) > 200:
        raise QAError("WS_STEPS_TOO_MANY", "At most 200 steps.", status=422)
    return out


class Recorder:
    """The live part: one recording per application, inside its live browser session (FilterSession)."""

    def __init__(self, app_id: str) -> None:
        self.app_id = app_id
        self.events: list[dict[str, Any]] = []
        self.active = False
        self.started_at = _now()
        self.start_url = ""
        self.lock = threading.Lock()

    def on_event(self, _source: Any, event: Any) -> None:
        if self.active and isinstance(event, dict) and len(self.events) < MAX_EVENTS:
            with self.lock:
                self.events.append(event)

    def install(self, ex: Any) -> dict[str, Any]:
        """On the session's thread: observe every page of this browser context (new pages and navigations too)."""
        ctx = ex.page.context
        if not getattr(ctx, "_sg_guided", False):
            ctx.expose_binding("__sgGuided", self.on_event)
            ctx.add_init_script(AGENT_JS.read_text(encoding="utf-8") +
                                "\n;try{window.__sgAgent.observe(e => window.__sgGuided && window.__sgGuided(e));}"
                                "catch(e){}")
            ctx._sg_guided = True
        for frame in ex.page.frames:
            try:
                frame.evaluate(AGENT_JS.read_text(encoding="utf-8") +
                               "\n;window.__sgAgent.observe(e => window.__sgGuided && window.__sgGuided(e)); true")
            except Exception:
                pass
        try:
            ex.page.evaluate("t => window.__sgAgent && window.__sgAgent.banner(t)",
                             "SkyGuard is recording your navigation. Sign in, open the report, set its filters, "
                             "then press Stop & Save in SkyGuard.")
        except Exception:
            pass
        self.start_url = ex.page.url
        self.active = True
        return {"url": ex.page.url}

    def pump(self, ex: Any) -> dict[str, Any]:
        """Let Playwright deliver the page's events (the sync API delivers them during a call)."""
        try:
            ex.page.wait_for_timeout(60)
        except Exception:
            pass
        with self.lock:
            last = self.events[-1] if self.events else None
        return {"events": len(self.events), "url": ex.page.url,
                "last": {"kind": last.get("kind"), "what": ((last.get("element") or {}).get("name")
                                                            or (last.get("element") or {}).get("label") or "")[:60]}
                if last else None}

    def finish(self, ex: Any) -> dict[str, Any]:
        self.pump(ex)
        self.active = False
        try:
            ex.page.evaluate("() => window.__sgAgent && (window.__sgAgent.banner(''), window.__sgAgent.stopObserving())")
        except Exception:
            pass
        with self.lock:
            events = list(self.events)
        rec = events_to_recording(events, start_url=self.start_url)
        rec["final_url"] = ex.page.url
        # the tables on the page the tester ended on (View Captured Tables)
        try:
            data = ex._table_page(None, all_tables=True)
            tables = []
            if data:
                tables.append({"headers": data.get("headers"), "rows": (data.get("rows") or [])[:5],
                               "row_count": data.get("rowCount") or len(data.get("rows") or []),
                               "method": data.get("method")})
                for t in data.get("others") or []:
                    tables.append({"headers": t.get("headers"), "rows": (t.get("rows") or [])[:5],
                                   "row_count": len(t.get("rows") or []), "method": t.get("method")})
            rec["tables"] = tables
        except Exception as exc:  # noqa: BLE001 - reading is best effort; the replay reads everything
            rec["tables"] = []
            rec["tables_error"] = str(exc).splitlines()[0][:200]
        return rec
