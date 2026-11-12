"""Web extraction for the Data Comparison Agent (Playwright, the browser chosen in Preferences).

Security:
  * Only the registered application's own hosts (and hosts the tester listed for it) are reached. Every request to
    any other host is blocked, and cloud metadata / link-local addresses are always blocked (SSRF protection).
  * TLS certificates are always checked (``ignore_https_errors`` is never set).
  * The password is typed into the password field only. It is never logged, never in a navigation step, never in a
    prompt; password fields are masked in every screenshot.
  * Nothing on the page is changed: the agent reads, clicks Next / tabs / filters the tester configured, and never
    submits a form other than the login and configured filter steps.

Completeness: every page of a paginated table is read until there is no enabled Next control; a page that does not
change after Next stops the loop (loop guard) and marks the extraction incomplete. The total the application shows
("Showing 1-20 of 250") is compared with the records read. An incomplete extraction is never compared as complete.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from ..clock import now_iso
from ..errors import QAError

BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata", "100.100.100.200", "fd00:ec2::254"}
STEP_ACTIONS = ("goto", "click", "fill", "select", "check", "press", "wait", "wait_for", "tab")

TABLE_JS = r"""
(sel) => {
  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  let t = sel ? document.querySelector(sel) : null;
  if (!t) {
    const c = [...document.querySelectorAll('table, [role=grid], [role=table]')].filter(visible);
    c.sort((a, b) => b.querySelectorAll('tr,[role=row]').length - a.querySelectorAll('tr,[role=row]').length);
    t = c[0];
  }
  if (!t) return null;
  const own = (r) => r.closest('table,[role=grid],[role=table]') === t;
  const rowsEl = [...t.querySelectorAll('tr, [role=row]')].filter(own).filter(visible);
  let headers = []; const rows = [];
  for (const r of rowsEl) {
    const ths = [...r.querySelectorAll('th, [role=columnheader]')];
    const tds = [...r.querySelectorAll('td, [role=gridcell], [role=cell]')];
    if (ths.length && !tds.length) { if (!headers.length) headers = ths.map(c => c.innerText.replace(/\s+/g, ' ').trim()); continue; }
    if (!tds.length) continue;
    rows.push(tds.map(c => {
      const input = c.querySelector('input:not([type=password]), select, textarea');
      let v = c.innerText;
      if (input && !v.trim()) v = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : (input.value || '');
      return v.replace(/\s+/g, ' ').trim();
    }));
  }
  if (!headers.length && rows.length) headers = rows.shift();
  return {headers, rows};
}
"""

NEXT_JS = r"""
(sel) => {
  document.querySelectorAll('[data-qa-next]').forEach(e => e.removeAttribute('data-qa-next'));
  const disabled = (e) => e.disabled || e.getAttribute('aria-disabled') === 'true' ||
      /(^|\s)(disabled|is-disabled)(\s|$)/.test(e.className || '') || (e.parentElement && /(^|\s)disabled(\s|$)/.test(e.parentElement.className || ''));
  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  let cands = sel ? [...document.querySelectorAll(sel)] :
    [...document.querySelectorAll('button, a, [role=button], li > a, input[type=button]')].filter(e => {
      const t = ((e.innerText || e.value || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.title || '')).trim();
      return /^(next( page)?|›|»|>|>>|next ›|next »)$/i.test((e.innerText || e.value || '').trim()) || /\bnext( page)?\b/i.test(e.getAttribute('aria-label') || '') || /\bnext\b/i.test(e.title || '');
    });
  cands = cands.filter(visible);
  const e = cands.find(c => !disabled(c));
  if (!e) return cands.length ? 'disabled' : 'none';
  e.setAttribute('data-qa-next', '1');
  return 'ok';
}
"""

CHARTS_JS = r"""
() => {
  const out = [];
  try { const C = window.Chart; if (C) { const inst = C.instances ? Object.values(C.instances) : [];
    for (const c of inst) out.push({lib: 'chartjs', type: c.config.type, title: (c.options && c.options.plugins && c.options.plugins.title && c.options.plugins.title.text) || c.canvas.getAttribute('aria-label') || c.canvas.id,
      labels: c.data.labels, series: c.data.datasets.map(d => ({name: d.label, values: d.data}))}); } } catch (e) {}
  try { if (window.echarts) for (const el of document.querySelectorAll('[_echarts_instance_]')) { const o = echarts.getInstanceByDom(el).getOption();
    out.push({lib: 'echarts', title: (o.title && o.title[0] && o.title[0].text) || el.id, labels: (o.xAxis && o.xAxis[0] && o.xAxis[0].data) || null,
      series: o.series.map(s => ({name: s.name, type: s.type, values: s.data}))}); } } catch (e) {}
  try { if (window.Highcharts) for (const c of Highcharts.charts.filter(Boolean)) out.push({lib: 'highcharts', type: c.options.chart && c.options.chart.type,
      title: c.title && c.title.textStr, labels: null, series: c.series.map(s => ({name: s.name, values: s.points.map(p => ({name: p.name != null ? p.name : p.category, value: p.y}))}))}); } catch (e) {}
  try { for (const gd of document.querySelectorAll('.js-plotly-plot')) out.push({lib: 'plotly', title: gd.layout && gd.layout.title && (gd.layout.title.text || gd.layout.title) || gd.id,
      labels: null, series: gd.data.map(t => ({name: t.name, type: t.type, labels: t.labels || t.x, values: t.values || t.y}))}); } catch (e) {}
  try { for (const svg of document.querySelectorAll('svg[data-chart], figure svg')) {
      const marks = [...svg.querySelectorAll('[data-value]')].map(m => ({name: m.getAttribute('data-label') || m.getAttribute('aria-label'), value: m.getAttribute('data-value')}));
      if (marks.length) out.push({lib: 'svg', title: svg.getAttribute('aria-label') || svg.id, labels: null, series: [{name: null, values: marks}]}); } } catch (e) {}
  return out;
}
"""

KPI_JS = r"""
() => {
  const out = [];
  const num = /^[(\-+]?(?:[A-Z]{3}\s?|[$€£₹])?[\d][\d.,]*\s*%?\)?(?:\s?[A-Z]{3})?$/;
  const cards = document.querySelectorAll('[data-kpi], .kpi, .kpi-card, .stat, .stat-card, .metric, .tile, .card, .summary-card');
  for (const c of cards) {
    if (c.querySelector('table')) continue;
    const lines = c.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length < 2 || lines.length > 6) continue;
    const v = lines.find(l => num.test(l)); const label = c.getAttribute('data-kpi') || lines.find(l => !num.test(l));
    if (v && label) out.push({label, value: v, method: 'dom', confidence: 1});
  }
  return out;
}
"""

DISCOVER_JS = r"""
() => {
  const visible = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const labelOf = (e) => { if (e.id) { const l = document.querySelector(`label[for="${CSS.escape(e.id)}"]`); if (l) return l.innerText.trim(); }
    const p = e.closest('label'); if (p) return p.innerText.trim(); return e.getAttribute('aria-label') || e.placeholder || e.name || e.id || ''; };
  const tables = [...document.querySelectorAll('table, [role=grid]')].filter(visible).map((t, i) => ({
    index: i, id: t.id || null, headers: [...t.querySelectorAll('th, [role=columnheader]')].map(h => h.innerText.trim()).slice(0, 60),
    rows: t.querySelectorAll('tbody tr, [role=row]').length}));
  const fields = [...document.querySelectorAll('input, select, textarea')].filter(visible)
    .filter(e => e.type !== 'password' && e.type !== 'hidden').map(e => ({label: labelOf(e).slice(0, 80), type: e.tagName.toLowerCase() === 'select' ? 'select' : (e.type || 'text'),
      name: e.name || e.id || null, options: e.tagName.toLowerCase() === 'select' ? [...e.options].map(o => o.text).slice(0, 50) : undefined}));
  const links = [...document.querySelectorAll('nav a, [role=navigation] a, aside a, .menu a, .sidebar a')].filter(visible)
    .map(a => ({text: a.innerText.trim().slice(0, 60), href: a.getAttribute('href')})).filter(l => l.text).slice(0, 100);
  const tabs = [...document.querySelectorAll('[role=tab]')].map(t => t.innerText.trim()).filter(Boolean).slice(0, 40);
  return {title: document.title, headings: [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim()).filter(Boolean).slice(0, 30),
    tables, fields, links, tabs, has_password: !!document.querySelector('input[type=password]'),
    charts: document.querySelectorAll('canvas, [_echarts_instance_], .highcharts-container, .js-plotly-plot, svg[data-chart]').length};
}
"""

TOTAL_RE = re.compile(r"(?i)(?:\bof\s+|total[:\s]+|\b)([\d][\d,\.]*)\s+(?:records|entries|items|rows|results|invoices|orders)\b"
                      r"|showing\s+[\d,]+\s*(?:-|to|–)\s*[\d,]+\s+of\s+([\d,]+)")


def host_allowed(url: str, allowed: set[str]) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme in ("data", "blob", "about"):
        return True
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host or host in BLOCKED_HOSTS:
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            return False
    except ValueError:
        pass
    return host in allowed


def allowed_hosts(config: dict[str, Any]) -> set[str]:
    hosts = set()
    for url in [config.get("base_url"), config.get("login_url")]:
        if url:
            h = (urlparse(url).hostname or "").lower()
            if h:
                hosts.add(h)
    for h in config.get("allowed_hosts") or []:
        h = str(h).strip().lower()
        if h and h not in BLOCKED_HOSTS:
            hosts.add(h)
    return hosts


def expected_total(text: str) -> int | None:
    best = None
    for m in TOTAL_RE.finditer(text or ""):
        raw = m.group(2) or m.group(1)
        try:
            n = int(raw.replace(",", "").split(".")[0])
        except ValueError:
            continue
        best = n if best is None else max(best, n)
    return best


def chart_values(chart: dict[str, Any]) -> list[dict[str, Any]]:
    """Each series as {name, values: {category: value}} (the shape the aggregate engine compares)."""
    out = []
    labels = chart.get("labels") or []
    for s in chart.get("series") or []:
        vals: dict[str, Any] = {}
        data = s.get("values") or []
        s_labels = s.get("labels") or labels
        for i, v in enumerate(data):
            if isinstance(v, dict):
                name = v.get("name") if v.get("name") is not None else (s_labels[i] if i < len(s_labels) else str(i))
                vals[str(name)] = v.get("value", v.get("y"))
            elif isinstance(v, (list, tuple)) and len(v) >= 2:
                vals[str(v[0])] = v[1]
            else:
                vals[str(s_labels[i]) if i < len(s_labels) else str(i)] = v
        out.append({"name": s.get("name"), "type": s.get("type") or chart.get("type"), "values": vals})
    return out


class WebExtractor:
    """One browser session for one application. ``log(step, details)`` receives the navigation log."""

    def __init__(self, config: dict[str, Any], *, username: str | None = None, password: str | None = None,
                 log: Callable[[str, dict[str, Any]], None] | None = None, headless: bool = True,
                 browser: str | None = None, cancel: Callable[[], bool] | None = None) -> None:
        self.config = config
        self.username, self._password = username, password
        self.allowed = allowed_hosts(config)
        if not self.allowed:
            raise QAError("CMP_URL_REQUIRED", "The application has no URL.", status=422)
        self._log = log or (lambda step, details: None)
        self.headless = headless
        self.browser_name = browser
        self.cancel = cancel or (lambda: False)
        self.blocked: list[str] = []
        self.pw = self.browser = self.context = self.page = None

    # ---- session -------------------------------------------------------------------------------------------------
    def log(self, step: str, **details: Any) -> None:
        self._log(step, {k: v for k, v in details.items() if v is not None})

    def __enter__(self) -> "WebExtractor":
        from ..automation import browser as bm

        self.pw = bm.start_playwright()
        self.browser = bm.launch(self.pw, self.browser_name, self.headless)
        opts = bm.context_options(self.headless, None, accept_downloads=False)
        if self.config.get("auth_method") in ("basic", "windows", "ntlm") and self.username and self._password:
            opts["http_credentials"] = {"username": self.username, "password": self._password}
        self.context = self.browser.new_context(**opts)  # TLS checks stay on
        self.context.route("**/*", self._route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(int(self.config.get("timeout_ms") or 30000))
        return self

    def _route(self, route: Any) -> None:
        url = route.request.url
        if host_allowed(url, self.allowed):
            route.continue_()
        else:
            if len(self.blocked) < 50:
                self.blocked.append(urlparse(url).hostname or url[:60])
            route.abort("blockedbyclient")

    def __exit__(self, *exc: Any) -> None:
        for closer in (self.context, self.browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:
                pass
        try:
            if self.pw is not None:
                self.pw.stop()
        except Exception:
            pass

    def goto(self, url: str) -> None:
        full = urljoin(self.config.get("base_url") or "", url)
        if not host_allowed(full, self.allowed):
            raise QAError("CMP_HOST_BLOCKED", f"'{urlparse(full).hostname}' is not an approved host of this application.",
                          status=403)
        self.page.goto(full, wait_until="domcontentloaded")
        self._settle()
        self.log("open page", url=_safe_url(full))

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

    def screenshot(self) -> bytes:
        return self.page.screenshot(full_page=True, mask=[self.page.locator("input[type=password]")])

    # ---- login ---------------------------------------------------------------------------------------------------
    def login(self) -> dict[str, Any]:
        cfg = self.config
        method = cfg.get("auth_method") or "none"
        if method in ("none", "basic", "windows", "ntlm"):
            self.goto(cfg.get("base_url"))
            return {"method": method, "ok": True}
        if method == "manual":
            raise QAError("CMP_MANUAL_LOGIN", "This application signs in manually (SSO/MFA): use a headed session.",
                          status=422)
        if not self.username or not self._password:
            raise QAError("CMP_CREDENTIALS_MISSING", "Save a username and password for this application first "
                          "(they are kept in Windows Credential Manager).", status=422)
        self.goto(cfg.get("login_url") or cfg.get("base_url"))
        page = self.page
        user_sel = cfg.get("username_selector") or ("input[type=email], input[name*=user i], input[id*=user i], "
                                                    "input[name*=login i], input[name*=email i], input[type=text]")
        pass_sel = cfg.get("password_selector") or "input[type=password]"
        submit_sel = cfg.get("submit_selector") or ("button[type=submit], input[type=submit], "
                                                    "button:has-text('Sign in'), button:has-text('Log in'), "
                                                    "button:has-text('Login')")
        page.locator(user_sel).first.fill(self.username)
        page.locator(pass_sel).first.fill(self._password)
        self.log("login: typed username and password", username=self.username)
        page.locator(submit_sel).first.click()
        self._settle()
        still = page.locator(pass_sel).count() and page.locator(pass_sel).first.is_visible()
        if still:
            raise QAError("CMP_LOGIN_FAILED", "The sign-in page is still shown after signing in: check the username, "
                          "password and sign-in method.", status=422)
        self.log("login: signed in", url=_safe_url(page.url))
        return {"method": method, "ok": True}

    # ---- navigation steps ----------------------------------------------------------------------------------------
    def run_steps(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        """Configured navigation and filter steps. Returns the filters that were applied ({label: value})."""
        filters: dict[str, Any] = {}
        for i, s in enumerate(steps or []):
            if self.cancel():
                raise QAError("CMP_CANCELLED", "Stopped by the tester.", status=409)
            action = s.get("action")
            if action not in STEP_ACTIONS:
                raise QAError("CMP_STEP_INVALID", f"Step {i + 1}: unknown action '{action}'.", status=422)
            sel = s.get("selector")
            loc = None
            if sel:
                loc = self.page.locator(sel).first
            elif s.get("text"):
                loc = self.page.get_by_text(s["text"], exact=bool(s.get("exact"))).first
            elif s.get("label"):
                loc = self.page.get_by_label(s["label"]).first
            if action == "goto":
                self.goto(s.get("url") or "")
                continue
            if action == "wait":
                time.sleep(min(float(s.get("seconds") or 1), 30))
                continue
            if loc is None:
                raise QAError("CMP_STEP_INVALID", f"Step {i + 1}: a selector, text or label is needed.", status=422)
            if action in ("click", "tab"):
                loc.click()
            elif action == "fill":
                loc.fill(str(s.get("value") or ""))
            elif action == "select":
                loc.select_option(label=str(s.get("value"))) if s.get("by") != "value" else loc.select_option(str(s.get("value")))
            elif action == "check":
                loc.set_checked(bool(s.get("value", True)))
            elif action == "press":
                loc.press(str(s.get("key") or "Enter"))
            elif action == "wait_for":
                loc.wait_for(state="visible")
            self._settle()
            if s.get("filter"):
                filters[str(s["filter"])] = s.get("value")
            self.log(f"step {i + 1}: {action}", target=sel or s.get("text") or s.get("label"),
                     filter=s.get("filter"), value=s.get("value") if s.get("filter") else None)
        return filters

    # ---- reading -------------------------------------------------------------------------------------------------
    def discover(self) -> dict[str, Any]:
        info = self.page.evaluate(DISCOVER_JS)
        info["url"] = _safe_url(self.page.url)
        return info

    def kpis(self) -> list[dict[str, Any]]:
        return self.page.evaluate(KPI_JS)

    def charts(self) -> list[dict[str, Any]]:
        charts = self.page.evaluate(CHARTS_JS)
        for c in charts:
            c["series_values"] = chart_values(c)
            c["method"] = f"chart library ({c.get('lib')})"
            c["confidence"] = 1.0
        return charts

    def _table_page(self, selector: str | None) -> dict[str, Any] | None:
        return self.page.evaluate(TABLE_JS, selector)

    def table(self, target: dict[str, Any], *, on_page: Callable[[int, dict[str, Any], bytes | None], None] | None = None,
              screenshots: bool = True) -> dict[str, Any]:
        """Every page of the table. ``on_page(page_no, data, png)`` is called per page (evidence, progress)."""
        selector = target.get("selector")
        max_pages = max(1, min(int(target.get("max_pages") or 500), 5000))
        records: list[dict[str, Any]] = []
        headers: list[str] = []
        seen: set[str] = set()
        failures: list[str] = []
        complete, reason, pages = True, None, 0
        shown_total = self._shown_total(target)
        while True:
            if self.cancel():
                complete, reason = False, "stopped by the tester"
                break
            if target.get("scroll"):
                self._scroll_all(selector)
            data = self._table_page(selector)
            if data is None:
                failures.append(f"page {pages + 1}: no table found")
                complete, reason = False, f"no table on page {pages + 1}"
                break
            sig = hashlib.sha1(json.dumps(data["rows"]).encode()).hexdigest()
            if sig in seen and data["rows"]:
                complete, reason = False, f"page {pages + 1} repeats an earlier page (Next did not move on)"
                failures.append(reason)
                break
            seen.add(sig)
            pages += 1
            headers = headers or data["headers"]
            png = self.screenshot() if screenshots else None
            for row in data["rows"]:
                fields = {h or f"Column {j + 1}": {"value": v, "method": "dom", "confidence": 1.0}
                          for j, (h, v) in enumerate(zip(headers, row))}
                records.append({"page": pages, "fields": fields})
            if on_page:
                on_page(pages, data, png)
            self.log(f"read table page {pages}", rows=len(data["rows"]))
            if target.get("scroll") or target.get("single_page"):
                break
            if pages >= max_pages:
                state = self.page.evaluate(NEXT_JS, target.get("next_selector"))
                if state == "ok":
                    complete, reason = False, f"stopped at the page limit ({max_pages})"
                break
            state = self.page.evaluate(NEXT_JS, target.get("next_selector"))
            if state != "ok":
                break
            before = sig
            self.page.locator("[data-qa-next]").first.click()
            self._settle()
            self._wait_change(selector, before)
            shown_total = shown_total or self._shown_total(target)
        stats = {"pages_visited": pages, "records": len(records), "complete": complete, "reason": reason,
                 "failures": failures, "expected_total": shown_total, "method": "dom", "headers": headers,
                 "blocked_requests": sorted(set(self.blocked))[:20]}
        if complete and shown_total is not None and shown_total != len(records):
            stats["complete"] = False
            stats["reason"] = f"read {len(records)} records but the application shows {shown_total}"
        return {"records": records, "stats": stats, "headers": headers}

    def _shown_total(self, target: dict[str, Any]) -> int | None:
        """The record total the application displays: from the configured element, else the page text."""
        if target.get("check_total") is False:
            return None
        sel = target.get("total_selector")
        try:
            text = self.page.locator(sel).first.inner_text() if sel else self.page.inner_text("body")
        except Exception:
            return None
        if sel:
            digits = re.findall(r"[\d][\d,]*", text)
            return expected_total(text) or (int(digits[-1].replace(",", "")) if digits else None)
        return expected_total(text)

    def _wait_change(self, selector: str | None, before: str, timeout: float = 10.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data = self._table_page(selector)
            if data and hashlib.sha1(json.dumps(data["rows"]).encode()).hexdigest() != before:
                return
            time.sleep(0.2)

    def _scroll_all(self, selector: str | None, rounds: int = 200) -> None:
        """Infinite scroll: scroll the table (or page) to the end until the row count stops growing."""
        stable, last = 0, -1
        for _ in range(rounds):
            count = self.page.evaluate("""(sel) => { const t = sel ? document.querySelector(sel) : null;
                const box = t ? (t.closest('[style*=overflow], .scroll, .grid-body') || t.parentElement) : null;
                if (box && box.scrollHeight > box.clientHeight) box.scrollTop = box.scrollHeight; else window.scrollTo(0, document.body.scrollHeight);
                return (t || document).querySelectorAll('tr, [role=row]').length; }""", selector)
            time.sleep(0.35)
            stable = stable + 1 if count == last else 0
            last = count
            if stable >= 3:
                return


def _safe_url(url: str) -> str:
    """URL without query string values (they can hold tokens)."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}" + ("?…" if p.query else "")


def stamp() -> str:
    return now_iso()
