"""23.10.0: navigation recordings for the Integration Comparison, shared by SkyGuard (guided recording in its own
browser) and the browser extension's companion (recording in the person's Edge or Chrome). Standard library only.

What a person did in the browser (the agent_dom.js observer events) becomes: the navigation steps the extractor
replays, the filters set on the report page (kept apart from their values), whether pagination was used (every page
is read automatically, so it is not replayed), a detail page as a parameterized address, and what was left out and
why (sign-in, pagination, sorting, anything that looks like a change: the comparison only reads).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class NavRecordingError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


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
            raise NavRecordingError("WS_STEP_CHANGES_DATA", f"'{text}' looks like it changes data in the application. The "
                          "comparison only reads; remove this step.", status=422)
        out.append({"action": "click", "text": text})
    if len(out) > 200:
        raise NavRecordingError("WS_STEPS_TOO_MANY", "At most 200 steps.", status=422)
    return out


FORMAT = "skyguard-navigation-recording/1"
_STEP_KEYS = {"selector": 200, "label": 120, "text": 120, "filter": 120}


def _clean_step(s: Any) -> dict[str, Any]:
    if not isinstance(s, dict) or s.get("action") not in ("click", "fill", "select", "check", "goto"):
        raise NavRecordingError("WS_RECORDING_INVALID", "The recording contains a step SkyGuard does not run.")
    out: dict[str, Any] = {"action": s["action"]}
    for k, n in _STEP_KEYS.items():
        if isinstance(s.get(k), str) and s[k].strip():
            out[k] = s[k][:n]
    if s["action"] == "goto":
        url = str(s.get("url") or "")
        if not re.match(r"^https?://", url, re.I) or len(url) > 1000:
            raise NavRecordingError("WS_RECORDING_INVALID", "A recorded address is not an http(s) address.")
        out["url"] = url
    elif s["action"] == "check":
        out["value"] = bool(s.get("value"))
    elif s["action"] in ("fill", "select"):
        out["value"] = str(s.get("value") if s.get("value") is not None else "")[:500]
    if s.get("apply_filters") is True:
        out["apply_filters"] = True
    if s["action"] != "goto" and not ({"selector", "label", "text"} & out.keys()):
        raise NavRecordingError("WS_RECORDING_INVALID", "A recorded step has nothing to find on the page.")
    if s["action"] == "click":
        what = out.get("text") or out.get("label") or ""
        if CHANGE.search(what) and not APPLY.search(what):
            raise NavRecordingError("WS_STEP_CHANGES_DATA", f"'{what}' looks like it changes data in the application. "
                                    "The comparison only reads.")
    if out.get("value") == "[secret]":
        raise NavRecordingError("WS_RECORDING_INVALID", "A recording never contains passwords or codes.")
    return out


def sanitize_recording(data: Any) -> dict[str, Any]:
    """A recording made elsewhere (the browser extension) checked before SkyGuard keeps it: only the step actions the
    extractor runs, nothing that changes data, a detail address with exactly one {key}, bounded sizes."""
    if not isinstance(data, dict):
        raise NavRecordingError("WS_RECORDING_INVALID", "This is not a SkyGuard navigation recording.")
    if data.get("format") not in (None, FORMAT):
        raise NavRecordingError("WS_RECORDING_INVALID", f"Unsupported recording format {str(data.get('format'))[:60]!r}.")
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) > 200:
        raise NavRecordingError("WS_RECORDING_INVALID", "The recording has no steps list (or more than 200 steps).")
    out: dict[str, Any] = {"steps": [_clean_step(s) for s in steps]}
    filters = []
    for f in (data.get("filters") or [])[:50]:
        if isinstance(f, dict) and isinstance(f.get("label"), str):
            filters.append({"label": f["label"][:120], "control": str(f.get("control") or "fill")[:10],
                            "value": f.get("value") if isinstance(f.get("value"), bool) else str(f.get("value") or "")[:500],
                            "type": str(f.get("type") or "")[:20]})
    out["filters"] = filters
    out["pagination_seen"] = bool(data.get("pagination_seen"))
    d = data.get("detail")
    if isinstance(d, dict) and d.get("url_template"):
        tpl = str(d["url_template"])
        if not re.match(r"^https?://", tpl, re.I) or tpl.count("{key}") != 1 or len(tpl) > 1000:
            raise NavRecordingError("WS_RECORDING_INVALID", "The detail address must be http(s) with one {key}.")
        out["detail"] = {"url_template": tpl, "key_column": str(d.get("key_column") or "")[:120],
                         "example_key": str(d.get("example_key") or "")[:200],
                         "filters": [_clean_step(s) for s in (d.get("filters") or [])[:20]]}
    else:
        out["detail"] = None
    for k in ("report_url_path", "login_url", "final_url", "recorded_at"):
        out[k] = str(data.get(k) or "")[:1000] or None
    out["dropped"] = [{"why": str(x.get("why") or "")[:200], "what": str(x.get("what") or "")[:200]}
                      for x in (data.get("dropped") or [])[:100] if isinstance(x, dict)]
    out["events"] = int(data.get("events") or 0) if str(data.get("events") or "0").isdigit() else 0
    tables = []
    for t in (data.get("tables") or [])[:10]:
        if isinstance(t, dict) and isinstance(t.get("headers"), list):
            rows = [r for r in (t.get("rows") or []) if isinstance(r, list)][:5]
            tables.append({"headers": [str(h)[:120] for h in t["headers"][:60]],
                           "rows": [[str(c)[:200] for c in r[:60]] for r in rows],
                           "row_count": int(t.get("row_count") or len(rows)) if str(t.get("row_count") or "0").isdigit()
                           else len(rows), "method": str(t.get("method") or "")[:40]})
    out["tables"] = tables
    if "[secret]" in str(out):
        raise NavRecordingError("WS_RECORDING_INVALID", "A recording never contains passwords or codes.")
    return out
