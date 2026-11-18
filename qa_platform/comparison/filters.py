"""23.9.0 Filter discovery, configuration, cross-application filter mapping and filtered fetch (Part 14).

A tester clicks "Discover & Configure Filters" for an application: the page is opened in the application's own
browser session and its filter controls are found from the page itself (labels, roles and accessibility names first,
then the DOM): dropdowns, multi-selects, dates and date ranges, search boxes, check boxes and radio groups, custom
JavaScript dropdowns, autocomplete boxes and the Apply / Search / Reset buttons. Not every dropdown is a filter: a
page-size selector ("Show 10 entries"), a language switcher, navigation and sign-in fields are recognised and left
out, and every control carries the reason it was (or was not) taken as a filter. The tester can correct that.

"Apply Filters & Fetch Data" sets the values through the real user interface (semantic locators: label, role and
name; parents before the dropdowns that depend on them), applies, waits for the data, checks that every value is
really set, reads the full filtered dataset (every page, scroll position or DataTables row, deduplicated), and saves
it as a snapshot with the filters, the time, the counts reported by the application and fetched, the method and
completeness. When something needs a person (a sign-in, an MFA prompt or CAPTCHA, a control that cannot be set) the
session pauses with the browser open and resumes where it stopped after the tester has helped. Nothing is bypassed.

Equivalent filters across applications are suggested from the meaning of their labels and options, never from the
name alone; the tester approves or corrects every link. "Sync Equivalent Filters" proposes the values for the other
applications; an application without that filter can be filtered locally on a field of its data instead.

Security: each application has its own browser session (cookies and credentials never cross); credentials are typed
by the extractor only and are never part of a filter, a log, a prompt or a report; page text is data, never an
instruction.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any, Callable

from ..clock import now_iso
from ..errors import QAError
from . import semantics
from .normalize import clean_text

FILTERS_JS = r"""
() => {
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const txt = (e) => ((e && (e.innerText !== undefined ? e.innerText : e.textContent)) || '').replace(/\s+/g, ' ').trim();
  const labelOf = (e) => {
    if (e.getAttribute('aria-label')) return e.getAttribute('aria-label').trim();
    const lb = e.getAttribute('aria-labelledby'); if (lb) { const t = lb.split(/\s+/).map(id => txt(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return txt(l); }
    const p = e.closest('label'); if (p) { const c = p.cloneNode(true); c.querySelectorAll('select,option,input').forEach(x => x.remove()); const t = txt(c); if (t) return t; }
    const prev = e.previousElementSibling; if (prev && /^(LABEL|SPAN|B|STRONG|DIV)$/.test(prev.tagName) && txt(prev).length < 40) return txt(prev);
    return e.getAttribute('placeholder') || e.getAttribute('title') || e.name || e.id || '';
  };
  const css = (e) => { if (e.id && /^[A-Za-z][\w-]*$/.test(e.id)) return '#' + e.id; if (e.name) return `${e.tagName.toLowerCase()}[name="${e.name}"]`;
    const path = []; let n = e; while (n && n.nodeType === 1 && path.length < 5) { let s = n.tagName.toLowerCase(); const sib = n.parentElement ? [...n.parentElement.children].filter(c => c.tagName === n.tagName) : [];
      if (sib.length > 1) s += `:nth-of-type(${sib.indexOf(n) + 1})`; path.unshift(s); n = n.parentElement; } return path.join(' > '); };
  const table = document.querySelector('table, [role=grid], [role=table]');
  const tableTop = table ? table.getBoundingClientRect().top + scrollY : null;
  const zone = (e) => { const z = e.closest('form, [role=search], [class*=filter i], [id*=filter i], [class*=toolbar i], [class*=search i], [class*=criteria i], fieldset'); return z ? (z.getAttribute('role') === 'search' ? 'search region' : (z.id || z.className || z.tagName).toString().slice(0, 40)) : null; };
  const inNav = (e) => !!e.closest('nav, header [role=navigation], [role=navigation], [role=menu], [role=menubar], footer');
  const out = [];
  const add = (e, kind, extra) => {
    const r = e.getBoundingClientRect();
    out.push(Object.assign({kind, label: labelOf(e).slice(0, 80), css: css(e), tag: e.tagName.toLowerCase(), name: e.name || null,
      id: e.id || null, role: e.getAttribute('role'), zone: zone(e), nav: inNav(e), above_table: tableTop !== null ? (r.top + scrollY <= tableTop + 5) : null,
      in_table: !!(table && table.contains(e)), form_login: !!(e.form && e.form.querySelector('input[type=password]')), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true'}, extra || {}));
  };
  for (const s of document.querySelectorAll('select')) { if (!visible(s)) continue;
    add(s, s.multiple ? 'multi_select' : 'select', {options: [...s.options].map(o => o.text.trim()).slice(0, 200), value: s.multiple ? [...s.selectedOptions].map(o => o.text.trim()) : ((s.selectedOptions[0] || {}).text || '').trim()}); }
  for (const i of document.querySelectorAll('input')) { if (!visible(i) || ['hidden', 'password', 'submit', 'button', 'image', 'file', 'reset'].includes(i.type)) continue;
    if (i.type === 'checkbox') { add(i, 'checkbox', {value: i.checked}); continue; }
    if (i.type === 'radio') continue;
    if (['date', 'datetime-local', 'month', 'week'].includes(i.type) || /date|from|to\b|period|start|end/i.test(labelOf(i) + ' ' + (i.placeholder || '')) && /\d{1,4}[-/.]\d/.test(i.value || i.placeholder || 'dd/mm/yyyy')) { add(i, 'date', {value: i.value, input_type: i.type}); continue; }
    const auto = i.getAttribute('list') || i.getAttribute('aria-autocomplete') || i.getAttribute('role') === 'combobox';
    add(i, auto ? 'autocomplete' : (i.type === 'search' ? 'search' : 'text'), {value: i.value, input_type: i.type,
      options: i.getAttribute('list') && document.getElementById(i.getAttribute('list')) ? [...document.getElementById(i.getAttribute('list')).options].map(o => o.value).slice(0, 200) : undefined}); }
  const groups = {}; for (const r of document.querySelectorAll('input[type=radio]')) if (visible(r) && r.name) (groups[r.name] = groups[r.name] || []).push(r);
  for (const [name, rs] of Object.entries(groups)) { const fs = rs[0].closest('fieldset, [role=radiogroup]');
    const lab = fs ? (txt(fs.querySelector('legend')) || fs.getAttribute('aria-label') || name) : name;
    const e = rs[0]; out.push({kind: 'radio', label: lab.slice(0, 80), css: `input[type=radio][name="${name}"]`, tag: 'input', name, id: null, role: 'radiogroup', zone: zone(e), nav: inNav(e),
      above_table: tableTop !== null ? e.getBoundingClientRect().top + scrollY <= tableTop + 5 : null, in_table: !!(table && table.contains(e)), form_login: false, disabled: false,
      options: rs.map(r => labelOf(r) || r.value), value: (rs.find(r => r.checked) ? (labelOf(rs.find(r => r.checked)) || rs.find(r => r.checked).value) : '')}); }
  for (const c of document.querySelectorAll('[role=combobox]:not(input):not(select), [aria-haspopup=listbox]:not(input):not(select)')) { if (!visible(c)) continue;
    add(c, 'custom_dropdown', {value: txt(c).slice(0, 80)}); }
  const btns = [...document.querySelectorAll('button, input[type=submit], input[type=button], input[type=reset], a[role=button], [role=button]')].filter(visible);
  const buttons = btns.map(b => ({text: (txt(b) || b.value || b.getAttribute('aria-label') || '').slice(0, 40), css: css(b), zone: zone(b)}))
    .filter(b => /^(apply|search|filter|go|submit|show|refresh|view|run|find|reset|clear|clear all|reset filters|apply filters)\b/i.test(b.text));
  const rowsNow = table ? table.querySelectorAll('tbody tr, [role=row]').length : 0;
  return {controls: out, buttons, table: !!table, rows: rowsNow};
}
"""

FILTER_WORDS = re.compile(r"(?i)\b(status|state|city|region|territory|branch|area|zone|country|emirate|location|date|from|"
                          r"to|period|month|year|quarter|week|start|end|category|type|product|customer|client|account|"
                          r"department|team|agent|employee|owner|channel|segment|search|filter|keyword|stage|priority|"
                          r"currency|store|outlet|warehouse|supplier|vendor|group|class)\b")
NOT_FILTER = re.compile(r"(?i)\b(show\s*\d*\s*entries|entries|per page|rows per page|page size|items per page|language|"
                        r"lang|theme|locale|username|user name|password|e-?mail address|remember me|subscribe|"
                        r"newsletter|comment|message|feedback|captcha)\b")
APPLY = re.compile(r"(?i)^(apply( filters?)?|search|filter|go|show|refresh|view|run|find|submit)\b")
RESET = re.compile(r"(?i)^(reset( filters?)?|clear( all| filters?)?)\b")
CAPTCHA = re.compile(r"(?i)captcha|i'?m not a robot|verify you are human|hcaptcha|recaptcha")


def classify(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Which discovered controls are filters, each with the reason. Date fields named from/to (or start/end) next to
    each other become one date range."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(raw.get("controls") or []):
        label = clean_text(c.get("label") or c.get("name") or "")
        reasons, score = [], 0.0
        is_filter = True
        if NOT_FILTER.search(label) or (c["kind"] in ("select",) and (c.get("options") or []) and
                                        all(re.fullmatch(r"\d+|all", o.strip(), re.I) for o in c["options"])):
            is_filter, why = False, "page size, language or form field, not a data filter"
            reasons.append(why)
        elif c.get("form_login"):
            is_filter = False
            reasons.append("part of the sign-in form")
        elif c.get("nav"):
            is_filter = False
            reasons.append("in the navigation or footer")
        elif c.get("in_table"):
            is_filter = False
            reasons.append("inside the table (a row editor, not a filter)")
        else:
            if FILTER_WORDS.search(label):
                score += 0.4
                reasons.append(f"label '{label}' names a business field")
            if c.get("zone"):
                score += 0.3
                reasons.append(f"in a filter/search area ({c['zone']})")
            if c.get("above_table"):
                score += 0.2
                reasons.append("placed above the data")
            if c["kind"] in ("date", "search", "multi_select", "custom_dropdown", "autocomplete"):
                score += 0.1
            is_filter = score >= 0.4
            if not is_filter:
                reasons.append("no sign that it filters the data")
        concept = semantics.concept(label, None, c.get("options") or []).get("concept")
        out.append({"id": f"F{i + 1}", "kind": c["kind"], "label": label or f"Filter {i + 1}", "is_filter": is_filter,
                    "confidence": round(min(0.95, score), 2) if is_filter else 0.0, "reason": "; ".join(reasons),
                    "locator": locator_of(c), "options": (c.get("options") or [])[:200], "value": c.get("value"),
                    "concept": concept, "disabled": bool(c.get("disabled")), "depends_on": None})
    # from / to dates side by side: one range
    dates = [c for c in out if c["kind"] == "date" and c["is_filter"]]
    for a, b in zip(dates, dates[1:]):
        if re.search(r"(?i)\b(from|start|begin)\b", a["label"]) and re.search(r"(?i)\b(to|end|until)\b", b["label"]):
            a["range"] = {"role": "from", "pair": b["id"]}
            b["range"] = {"role": "to", "pair": a["id"]}
    return out


def locator_of(c: dict[str, Any]) -> dict[str, Any]:
    """How to find the control again: by its accessible label and role first (survives layout changes), the CSS
    path last."""
    role = {"select": "combobox", "multi_select": "listbox", "checkbox": "checkbox", "radio": "radiogroup",
            "custom_dropdown": "combobox", "autocomplete": "combobox", "search": "searchbox", "text": "textbox",
            "date": "textbox"}.get(c["kind"])
    return {"label": c.get("label") or None, "role": c.get("role") or role, "css": c.get("css"), "name": c.get("name")}


def buttons(raw: dict[str, Any]) -> dict[str, Any]:
    btn = raw.get("buttons") or []
    apply_b = next((b for b in btn if APPLY.match(b["text"])), None)
    reset_b = next((b for b in btn if RESET.match(b["text"])), None)
    return {"apply": apply_b, "reset": reset_b}


# ---- applying values through the user interface ---------------------------------------------------------------------

def _find(page: Any, ctl: dict[str, Any]) -> Any:
    loc = ctl.get("locator") or {}
    cands = []
    if loc.get("label"):
        cands.append(lambda: page.get_by_label(loc["label"], exact=True))
    if loc.get("css"):
        cands.append(lambda: page.locator(loc["css"]))
    if loc.get("label") and loc.get("role") in ("combobox", "textbox", "searchbox", "listbox", "checkbox"):
        cands.append(lambda: page.get_by_role(loc["role"], name=loc["label"]))
    for make in cands:
        try:
            el = make()
            if el.count():
                return el.first
        except Exception:
            continue
    raise QAError("FILTER_NOT_FOUND", f"The filter '{ctl.get('label')}' was not found on the page.", status=409)


def set_value(page: Any, ctl: dict[str, Any], value: Any) -> None:
    kind = ctl["kind"]
    if kind == "radio":
        page.get_by_label(str(value), exact=True).first.check()
        return
    el = _find(page, ctl)
    if kind == "select":
        try:
            el.select_option(label=str(value))
        except Exception:
            el.select_option(str(value))
    elif kind == "multi_select":
        vals = value if isinstance(value, list) else [value]
        el.select_option(label=[str(v) for v in vals])
    elif kind == "checkbox":
        el.set_checked(bool(value) and str(value).lower() not in ("false", "0", "no", "off"))
    elif kind == "date":
        el.fill(str(value))
        el.dispatch_event("change")
    elif kind in ("custom_dropdown",):
        el.click()
        opt = page.get_by_role("option", name=str(value), exact=True)
        if not opt.count():
            opt = page.get_by_text(str(value), exact=True)
        opt.first.click()
    elif kind == "autocomplete":
        el.fill("")
        el.type(str(value), delay=40)
        opt = page.get_by_role("option", name=str(value), exact=True)
        if opt.count():
            opt.first.click()
        else:
            el.press("Enter")
    else:  # text / search
        el.fill(str(value))


def read_value(page: Any, ctl: dict[str, Any]) -> Any:
    try:
        if ctl["kind"] == "radio":
            return page.evaluate("(n) => { const r = document.querySelector(`input[type=radio][name=\"${n}\"]:checked`);"
                                 " if (!r) return ''; const l = r.id && document.querySelector(`label[for=\"${r.id}\"]`);"
                                 " return (l ? l.innerText : (r.closest('label') || {}).innerText || r.value || '').trim(); }",
                                 (ctl.get("locator") or {}).get("name") or "")
        el = _find(page, ctl)
        if ctl["kind"] in ("select", "multi_select"):
            vals = el.evaluate("(s) => [...s.selectedOptions].map(o => o.text.trim())")
            return vals if ctl["kind"] == "multi_select" else (vals[0] if vals else "")
        if ctl["kind"] == "checkbox":
            return el.is_checked()
        if ctl["kind"] == "custom_dropdown":
            return clean_text(el.inner_text())
        return el.input_value()
    except Exception:
        return None


def _same(shown: Any, wanted: Any) -> bool:
    if isinstance(wanted, list):
        return sorted(map(str, shown or [])) == sorted(map(str, wanted))
    if isinstance(wanted, bool) or str(wanted).lower() in ("true", "false"):
        return bool(shown) == (str(wanted).lower() == "true" if not isinstance(wanted, bool) else wanted)
    return clean_text(shown).lower() == clean_text(wanted).lower() or clean_text(wanted).lower() in clean_text(shown).lower()


def order_controls(controls: list[dict[str, Any]], values: dict[str, Any]) -> list[dict[str, Any]]:
    """Parents first: a dropdown that another depends on is set before it (Country before City)."""
    by_id = {c["id"]: c for c in controls}
    done: list[dict[str, Any]] = []

    def visit(c: dict[str, Any], depth: int = 0) -> None:
        if c in done or depth > 10:
            return
        if c.get("depends_on") and c["depends_on"] in by_id:
            visit(by_id[c["depends_on"]], depth + 1)
        done.append(c)
    for c in controls:
        if c["id"] in values:
            visit(c)
    return [c for c in done if c["id"] in values]


def apply(ex: Any, controls: list[dict[str, Any]], values: dict[str, Any], btns: dict[str, Any] | None = None,
          log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Set the values in the page, apply, wait for the data, and check every value. Raises FILTER_NEEDS_USER when a
    person has to help (the session then pauses)."""
    page = ex.page
    log = log or (lambda m: None)
    before = page.evaluate("() => { const t = document.querySelector('table, [role=grid]'); return t ? t.innerText.length + ':' + t.querySelectorAll('tr,[role=row]').length : ''; }")
    applied, problems = {}, []
    for ctl in order_controls(controls, values):
        v = values[ctl["id"]]
        if v in (None, "", []):
            continue
        try:
            set_value(page, ctl, v)
            ex._settle()
            applied[ctl["label"]] = v
            log(f"set {ctl['label']} = {v}")
        except QAError as exc:
            problems.append(exc.message)
        except Exception as exc:
            problems.append(f"'{ctl['label']}' could not be set to '{v}': {str(exc).splitlines()[0][:160]}")
    if problems:
        raise QAError("FILTER_NEEDS_USER", "; ".join(problems) + ". Set it in the browser window, then Resume.",
                      status=409, details={"problems": problems})
    b = (btns or {}).get("apply")
    if b:
        try:
            page.locator(b["css"]).first.click()
            log(f"clicked {b['text']}")
        except Exception:
            page.get_by_role("button", name=re.compile(r"(?i)^(apply|search|filter|go|show)")).first.click()
    ex._settle()
    # wait until the data changed (or settled) after applying
    end = time.monotonic() + 10
    while time.monotonic() < end:
        now = page.evaluate("() => { const t = document.querySelector('table, [role=grid]'); return t ? t.innerText.length + ':' + t.querySelectorAll('tr,[role=row]').length : ''; }")
        if now != before:
            break
        time.sleep(0.25)
    ex.wait_for_data(8)
    verified = {}
    for ctl in controls:
        if ctl["id"] in values and values[ctl["id"]] not in (None, "", []):
            shown = read_value(page, ctl)
            verified[ctl["label"]] = {"wanted": values[ctl["id"]], "shown": shown,
                                      "ok": _same(shown, values[ctl["id"]])}
    bad = [k for k, v in verified.items() if not v["ok"]]
    if bad:
        raise QAError("FILTER_NOT_VERIFIED", f"The page does not show the value set for: {', '.join(bad)}. Check the "
                      "filters in the browser window, then Resume.", status=409, details={"verified": verified})
    if CAPTCHA.search(page.inner_text("body")[:20000]):
        raise QAError("FILTER_NEEDS_USER", "The application shows a CAPTCHA / human check. It is never bypassed: "
                      "complete it in the browser window, then Resume.", status=409)
    return {"applied": applied, "verified": verified}


def dependencies(ex: Any, controls: list[dict[str, Any]]) -> None:
    """Which dropdowns change their options when another one changes (cascading filters), found by trying the
    first real option of each parent and restoring it. Read-only for the data: only the filter controls change."""
    page = ex.page
    selects = [c for c in controls if c["kind"] == "select" and c["is_filter"] and len(c.get("options") or []) > 1]
    for parent in selects[:6]:
        try:
            el = _find(page, parent)
            original = read_value(page, parent)
            snapshot = {c["id"]: tuple(_options(page, c)) for c in selects if c is not parent}
            option = next((o for o in parent["options"] if o and o != original and not re.match(r"(?i)^(all|any|-+|select)", o)), None)
            if option is None:
                continue
            el.select_option(label=option)
            ex._settle()
            for c in selects:
                if c is not parent and c["id"] in snapshot and tuple(_options(page, c)) != snapshot[c["id"]]:
                    c["depends_on"] = parent["id"]
                    c["reason"] += f"; its options change with '{parent['label']}' (cascading)"
            el.select_option(label=original) if original else None
            ex._settle()
        except Exception:
            continue


def _options(page: Any, ctl: dict[str, Any]) -> list[str]:
    try:
        return _find(page, ctl).evaluate("(s) => [...s.options].map(o => o.text.trim())")
    except Exception:
        return []


# ---- equivalent filters across applications -------------------------------------------------------------------------

def _norm(v: Any) -> str:
    return re.sub(r"[^a-z0-9%]+", " ", clean_text(v).lower()).strip()


def suggest_links(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equivalent filters across applications: same business meaning of the label (Region ~ Territory ~ City) and
    shared option values. A shared name alone is not enough: the options must agree too (or be absent, e.g. dates).
    Every link needs the tester's approval."""
    links: list[dict[str, Any]] = []
    if not apps:
        return links
    base = apps[0]
    for f in [c for c in base.get("filters") or [] if c["is_filter"]]:
        link = {"name": f["label"], "members": {base["id"]: {"filter": f["id"], "label": f["label"]}},
                "status": "suggested", "evidence": []}
        for other in apps[1:]:
            best = None
            for g in [c for c in other.get("filters") or [] if c["is_filter"]]:
                ca = semantics.concept(f["label"], None, f.get("options") or [])
                cb = semantics.concept(g["label"], None, g.get("options") or [])
                sm, why = semantics.same_meaning(ca, cb)
                oa = {_norm(o) for o in f.get("options") or [] if _norm(o) and not re.match(r"(all|any|select)", _norm(o))}
                ob = {_norm(o) for o in g.get("options") or [] if _norm(o) and not re.match(r"(all|any|select)", _norm(o))}
                ov = len(oa & ob) / max(1, min(len(oa), len(ob))) if oa and ob else None
                kinds_ok = (f["kind"] == "date") == (g["kind"] == "date")
                score = (0.5 * sm + (0.5 * ov if ov is not None else 0.25)) if kinds_ok else 0.0
                if _norm(f["label"]) == _norm(g["label"]) and ov == 0:
                    score = min(score, 0.4)  # same name, different values: not the same filter
                if f.get("range") and g.get("range") and f["range"]["role"] != g["range"]["role"]:
                    score = 0.0
                if best is None or score > best[0]:
                    best = (score, g, why, ov)
            if best and best[0] >= 0.55:
                score, g, why, ov = best
                link["members"][other["id"]] = {"filter": g["id"], "label": g["label"], "confidence": round(score, 2)}
                link["evidence"].append(f"{other['name']} › {g['label']}: {why}" +
                                        (f"; {int(ov * 100)}% of the options in common" if ov is not None else ""))
        links.append(link)
    return links


def map_value(value: Any, src: dict[str, Any], dst: dict[str, Any], value_map: dict[str, str] | None = None) -> Any:
    """The value of the target filter that means the same as ``value`` (exact option, then normalized, then an
    approved value map such as 'Dubai' -> 'DXB'). None when nothing matches: the tester decides."""
    if isinstance(value, list):
        return [m for m in (map_value(v, src, dst, value_map) for v in value) if m is not None]
    if dst.get("kind") in ("date", "text", "search", "checkbox", "autocomplete") and not dst.get("options"):
        return value
    if value_map and str(value) in value_map:
        return value_map[str(value)]
    opts = dst.get("options") or []
    if str(value) in opts:
        return str(value)
    nv = _norm(value)
    hits = [o for o in opts if _norm(o) == nv]
    if len(hits) == 1:
        return hits[0]
    from .workspace import STATUS_GROUPS

    for g in STATUS_GROUPS:
        if nv in g:
            hits = [o for o in opts if _norm(o) in g]
            if len(hits) == 1:
                return hits[0]
    return None


def sync_values(links: list[dict[str, Any]], apps: dict[str, dict[str, Any]], source_app: str,
                values: dict[str, Any]) -> dict[str, Any]:
    """For values set on ``source_app`` ({filter id: value}), the proposed values of every other application."""
    out: dict[str, dict[str, Any]] = {aid: {} for aid in apps if aid != source_app}
    unresolved = []
    for link in links:
        if link.get("status") not in ("approved", "suggested"):
            continue
        sm = link["members"].get(source_app)
        if not sm or sm["filter"] not in values:
            continue
        src = next((c for c in apps[source_app].get("filters") or [] if c["id"] == sm["filter"]), None)
        for aid, m in link["members"].items():
            if aid == source_app or aid not in out:
                continue
            if m.get("local_field"):
                out[aid][f"local:{m['local_field']}"] = values[sm["filter"]]
                continue
            dst = next((c for c in apps[aid].get("filters") or [] if c["id"] == m.get("filter")), None)
            if dst is None or src is None:
                continue
            v = map_value(values[sm["filter"]], src, dst, (link.get("value_map") or {}).get(aid))
            if v is None:
                unresolved.append({"app": aid, "filter": dst["label"], "value": values[sm["filter"]]})
            else:
                out[aid][dst["id"]] = v
    return {"values": out, "unresolved": unresolved}


def scope_check(data: list[dict[str, Any]], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Are the filtered datasets comparable? For every approved filter link, the applications' applied values must
    mean the same (an application filtered locally counts). Returns the problems found."""
    problems = []
    applied = {d["app_id"]: _applied(d) for d in data}
    for link in links:
        if link.get("status") != "approved":
            continue
        vals = {}
        for aid, m in link["members"].items():
            if aid not in applied:
                continue
            a = applied[aid]
            v = a.get(m.get("label")) if not m.get("local_field") else a.get(f"local:{m['local_field']}")
            vals[aid] = v
        present = {k: v for k, v in vals.items() if v not in (None, "", [])}
        if not present:
            continue
        if len(present) < len(vals):
            missing = [aid for aid in vals if aid not in present]
            problems.append({"filter": link["name"], "values": vals, "why": "filtered in some applications only",
                             "apps": missing})
            continue
        norm = {aid: sorted(_norm(x) for x in (v if isinstance(v, list) else [v])) for aid, v in present.items()}
        vm = link.get("value_map") or {}
        canon = {}
        for aid, vs in norm.items():
            inv = {_norm(b): _norm(a) for a, b in (vm.get(aid) or {}).items()}
            canon[aid] = sorted(inv.get(x, x) for x in vs)
        if len({tuple(v) for v in canon.values()}) > 1:
            problems.append({"filter": link["name"], "values": vals, "why": "different filter values"})
    return problems


def _applied(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in (d.get("coverage") or {}).get("inputs") or []:
        out.update(i.get("filters") or {})
    out.update(d.get("filters") or {})
    return out


def local_filter(d: dict[str, Any], field: str, value: Any) -> int:
    """Filter an application's records on one of its fields (its page has no such filter). Returns the records
    kept."""
    wanted = {_norm(v) for v in (value if isinstance(value, list) else [value])}
    kept = 0
    for t in d["tables"]:
        if field not in t["headers"]:
            continue
        t["records"] = [r for r in t["records"]
                        if _norm((r["fields"].get(field) or {}).get("value") if isinstance(r["fields"].get(field), dict)
                                 else r["fields"].get(field)) in wanted]
        kept += len(t["records"])
    return kept


# ---- the per-application browser session ------------------------------------------------------------------------

class FilterSession:
    """One live browser session for one application, owned by one thread (Playwright objects stay on the thread
    that made them). Commands are queued; the state says what the tester sees: starting, ready, busy, paused (a
    person must help: sign-in, MFA, CAPTCHA, a filter that could not be set), closed or failed."""

    IDLE_SECONDS = 900

    def __init__(self, make_extractor: Callable[[], Any], app_id: str, visible: bool) -> None:
        self.app_id = app_id
        self.visible = visible
        self.state = "starting"
        self.message = None
        self.paused: dict[str, Any] | None = None
        self._make = make_extractor
        self._q: queue.Queue = queue.Queue()
        self._resume = threading.Event()
        self.last_used = time.monotonic()
        self.thread = threading.Thread(target=self._main, daemon=True, name=f"cmp-filters-{app_id}")
        self.thread.start()

    def call(self, fn: Callable[[Any], Any], timeout: float = 600) -> Any:
        """Run ``fn(extractor)`` on the session's thread and return its result (or raise its error)."""
        if self.state in ("closed", "failed"):
            raise QAError("FILTER_SESSION_CLOSED", self.message or "The browser session has ended; start it again.",
                          status=409)
        done = threading.Event()
        box: dict[str, Any] = {}
        self._q.put((fn, box, done))
        if not done.wait(timeout):
            raise QAError("FILTER_TIMEOUT", "The browser did not finish in time.", status=504)
        self.last_used = time.monotonic()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def resume(self) -> None:
        self._resume.set()

    def close(self) -> None:
        self._q.put(None)

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "message": self.message, "paused": self.paused, "visible": self.visible}

    def _main(self) -> None:
        try:
            with self._make() as ex:
                try:
                    ex.login()
                except QAError as exc:
                    if exc.error_code in ("CMP_MANUAL_LOGIN", "CMP_LOGIN_FAILED") and self.visible:
                        self._pause(exc.error_code, "Sign in in the browser window (MFA and CAPTCHA are never bypassed), "
                                              "then click Resume.")
                    else:
                        raise
                ex.wait_for_data()
                self.state = "ready"
                while True:
                    try:
                        item = self._q.get(timeout=5)
                    except queue.Empty:
                        if time.monotonic() - self.last_used > self.IDLE_SECONDS:
                            break
                        continue
                    if item is None:
                        break
                    fn, box, done = item
                    self.state = "busy"
                    try:
                        box["result"] = self._run(fn, ex)
                    except Exception as exc:  # given back to the caller
                        box["error"] = exc if isinstance(exc, QAError) else QAError(
                            "FILTER_FAILED", str(exc).splitlines()[0][:300], status=500)
                    finally:
                        self.state = "ready" if self.state == "busy" else self.state
                        done.set()
            self.state, self.message = "closed", "The browser session was closed."
        except QAError as exc:
            self.state, self.message = "failed", exc.message
        except Exception as exc:  # the browser could not start
            self.state, self.message = "failed", str(exc).splitlines()[0][:300]

    def _run(self, fn: Callable[[Any], Any], ex: Any) -> Any:
        """Run a command; when it needs a person and the window is visible, pause, wait for Resume, retry once."""
        try:
            return fn(ex)
        except QAError as exc:
            if exc.error_code not in ("FILTER_NEEDS_USER", "FILTER_NOT_VERIFIED") or not self.visible:
                raise
            self._pause(exc.error_code, exc.message)
            return fn(ex)

    def _pause(self, code: str, message: str) -> None:
        self.state = "paused"
        self.paused = {"code": code, "message": message, "since": now_iso()}
        self._resume.clear()
        if not self._resume.wait(self.IDLE_SECONDS):
            raise QAError("FILTER_PAUSE_TIMEOUT", "Nobody resumed the paused session in time.", status=408)
        self.paused = None
        self.state = "busy"
