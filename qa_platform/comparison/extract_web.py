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
(args) => {
  // 23.9.0: native tables (thead/tbody/tfoot, th row headers, colspan/rowspan, multi-level headers), ARIA grids,
  // div grids, open shadow roots and DataTables (all filtered rows through its API, not only the visible page).
  const sel = args && args.sel, all = args && args.all;
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const txt = (e) => ((e.innerText !== undefined ? e.innerText : e.textContent) || '').replace(/\s+/g, ' ').trim();
  const roots = [document];
  const walk = (root, depth) => { if (depth > 6) return; for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { roots.push(el.shadowRoot); walk(el.shadowRoot, depth + 1); } };
  try { walk(document, 0); } catch (e) {}
  const qsa = (q) => roots.flatMap(r => { try { return [...r.querySelectorAll(q)]; } catch (e) { return []; } });
  const TOTAL = /^(grand\s+total|sub\s*-?\s*total|totals?|sum|net\s+total)\b/i;
  const strip = (h) => { if (h == null) return ''; if (typeof h !== 'string') return String(h);
    if (h.indexOf('<') < 0) return h.replace(/\s+/g, ' ').trim();
    const d = new DOMParser().parseFromString(h, 'text/html'); return (d.body.textContent || '').replace(/\s+/g, ' ').trim(); };
  const cellVal = (c) => { const input = c.querySelector && c.querySelector('input:not([type=password]), select, textarea'); let v = txt(c);
    if (input && !v) v = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : (input.tagName === 'SELECT' ? ((input.selectedOptions[0] || {}).text || '') : (input.value || ''));
    return v; };
  const gridOf = (trs, cellsOf, header) => { const out = []; const carry = {};
    for (const tr of trs) { const row = []; let col = 0;
      const fill = () => { while (carry[col] && carry[col].left > 0) { row[col] = carry[col].value; carry[col].left--; col++; } };
      for (const c of cellsOf(tr)) { fill(); const span = Math.min(+c.colSpan || +c.getAttribute('aria-colspan') || 1, 50), rs = Math.min(+c.rowSpan || 1, 500), v = cellVal(c);
        for (let i = 0; i < span; i++) { row[col + i] = (header || i === 0) ? v : ''; if (rs > 1) carry[col + i] = {value: header ? v : (i === 0 ? v : ''), left: rs - 1}; }
        col += span; }
      fill(); for (const k of Object.keys(carry)) { const i = +k; if (i >= col && carry[i].left > 0) { row[i] = carry[i].value; carry[i].left--; } }
      out.push([...row].map(v => v === undefined ? '' : v)); }
    return out; };
  const headersOf = (hrows) => { if (!hrows.length) return []; const n = Math.max(...hrows.map(r => r.length));
    const out = []; for (let i = 0; i < n; i++) { const parts = []; for (const r of hrows) { const v = r[i] || ''; if (v && parts[parts.length - 1] !== v) parts.push(v); } out.push(parts.join(' ')); }
    return out; };
  const finish = (el, method, headers, body, extra) => {
    const rows = [], totals = [], rowIndex = [];
    body.forEach((r, k) => { if (!r.some(v => v)) return; const first = r.find(v => v) || '';
      if (TOTAL.test(first) || (extra && extra.footer && extra.footer[k])) { totals.push({label: first, kind: /grand/i.test(first) ? 'grand_total' : /sub/i.test(first) ? 'subtotal' : 'total', values: Object.fromEntries(headers.map((h, i) => [h || `Column ${i + 1}`, r[i] || '']).filter(([h, v]) => v && v !== first))}); return; }
      rows.push(r); rowIndex.push(extra && extra.rowIndex ? extra.rowIndex[k] : null); });
    return {el, method, headers, rows, totals, rowIndex, rowCount: extra && extra.rowCount || null, complete: extra && extra.complete || false, score: rows.length * Math.max(1, headers.length)};
  };
  const fromTable = (t) => {
    try { const $ = window.jQuery; if ($ && $.fn && $.fn.dataTable && $.fn.dataTable.isDataTable(t)) { const api = $(t).DataTable();
      const server = api.settings()[0].oFeatures.bServerSide; if (!server) {
        const headers = api.columns().header().toArray().map(h => txt(h)); const body = [];
        api.rows({search: 'applied'}).every(function () { const ri = this.index(); body.push(headers.map((_, ci) => strip(api.cell(ri, ci).render('display')))); });
        return finish(t, 'datatables api (all filtered rows)', headers, body, {complete: true}); } } } catch (e) {}
    const trs = [...t.rows].filter(r => r.closest('table') === t && (visible(r) || r.parentElement.tagName === 'TFOOT'));
    const head = [], body = [], footer = [];
    for (const r of trs) { const sec = r.parentElement.tagName; const cells = [...r.cells];
      if (sec === 'THEAD' || (!body.length && cells.length && cells.every(c => c.tagName === 'TH') && sec !== 'TFOOT')) head.push(r);
      else { body.push(r); footer.push(sec === 'TFOOT'); } }
    let hrows = gridOf(head, r => [...r.cells], true), brows = gridOf(body, r => [...r.cells], false);
    let headers = headersOf(hrows);
    if (!headers.length && brows.length) { headers = brows.shift(); footer.shift(); }
    return finish(t, 'html table', headers, brows, {footer});
  };
  const fromAria = (g) => {
    const own = (r) => r.closest('[role=grid],[role=treegrid],[role=table]') === g;
    const rowsEl = [...g.querySelectorAll('[role=row]')].filter(own).filter(visible);
    const head = rowsEl.filter(r => r.querySelector('[role=columnheader]') && !r.querySelector('[role=gridcell],[role=cell]'));
    const body = rowsEl.filter(r => !head.includes(r));
    const cellsOf = (r) => [...r.querySelectorAll('[role=columnheader],[role=gridcell],[role=cell],[role=rowheader]')].filter(c => c.closest('[role=row]') === r)
      .sort((a, b) => (+a.getAttribute('aria-colindex') || 0) - (+b.getAttribute('aria-colindex') || 0));
    let headers = headersOf(gridOf(head, cellsOf, true));
    if (!headers.length) headers = [...g.querySelectorAll('[role=columnheader]')].filter(c => own(c)).map(txt);
    const rowIndex = body.map(r => r.getAttribute('aria-rowindex'));
    return finish(g, 'aria grid', headers, gridOf(body, cellsOf, false), {rowIndex, rowCount: +g.getAttribute('aria-rowcount') || null});
  };
  const fromDivGrid = (box) => {
    const kids = [...box.children].filter(visible);
    const byClass = {}; for (const k of kids) { const c = (k.className && typeof k.className === 'string') ? k.className.split(/\s+/).filter(x => !/(odd|even|active|selected|hover)/i.test(x)).sort().join('.') : ''; (byClass[c] = byClass[c] || []).push(k); }
    let best = null; for (const [c, list] of Object.entries(byClass)) { if (list.length < 3) continue;
      const n = list.map(r => [...r.children].filter(visible).length); const k = n.sort((a, b) => a - b)[Math.floor(n.length / 2)];
      if (k >= 2 && list.filter(r => [...r.children].filter(visible).length === k).length >= 0.8 * list.length && (!best || list.length > best.list.length)) best = {list, k}; }
    if (!best) return null;
    const rows = best.list.map(r => [...r.children].filter(visible).map(cellVal));
    const prev = best.list[0].previousElementSibling; let headers = [];
    if (prev && [...prev.children].filter(visible).length === best.k) headers = [...prev.children].filter(visible).map(txt);
    else if (/head/i.test(best.list[0].className) || !rows[0].some(v => /\d/.test(v))) headers = rows.shift();
    return finish(box, 'div grid', headers, rows, {});
  };
  let cands = [];
  if (sel) { const el = qsa(sel)[0]; if (el) cands = [el]; }
  if (!cands.length) cands = qsa('table, [role=grid], [role=treegrid], [role=table], [data-qa-table]').filter(visible).filter(e => !(e.tagName === 'TABLE' && e.closest('[role=grid]') && e.closest('[role=grid]') !== e));
  const out = [];
  for (const el of cands) { try { const r = el.tagName === 'TABLE' ? fromTable(el) : el.getAttribute('role') ? fromAria(el) : fromDivGrid(el); if (r && (r.rows.length || r.headers.length)) out.push(r); } catch (e) {} }
  if (!out.length && !sel) {
    const boxes = qsa('div, section, ul, tbody').filter(visible).filter(b => b.children.length >= 3 && b.children.length <= 2000);
    for (const b of boxes) { const r = fromDivGrid(b); if (r && r.rows.length >= 2 && r.headers.length >= 2) out.push(r); }
    out.sort((a, b) => b.score - a.score); out.splice(3);
  }
  out.sort((a, b) => b.score - a.score);
  if (!out.length) return null;
  document.querySelectorAll('[data-qa-table-main]').forEach(e => e.removeAttribute('data-qa-table-main'));
  try { out[0].el.setAttribute('data-qa-table-main', '1'); } catch (e) {}
  const clean = (r) => ({headers: r.headers, rows: r.rows, totals: r.totals, method: r.method, rowIndex: r.rowIndex, rowCount: r.rowCount, complete: r.complete});
  const res = clean(out[0]);
  if (all) res.others = out.slice(1, 8).filter(r => r.rows.length).map(clean);
  return res;
}
"""

READY_JS = r"""
() => {
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  let rows = 0;
  for (const t of document.querySelectorAll('table, [role=grid], [role=table]')) if (visible(t)) rows = Math.max(rows, t.querySelectorAll('tbody tr td, [role=gridcell], [role=cell]').length);
  const busy = [...document.querySelectorAll('[aria-busy=true], .spinner, .loading, .loader, .skeleton, .dataTables_processing, .ag-overlay-loading-wrapper')].some(visible);
  const charts = document.querySelectorAll('canvas, svg [data-value], [_echarts_instance_], .highcharts-container, .js-plotly-plot, .apexcharts-canvas').length;
  return {rows, busy, charts, text: (document.body && document.body.innerText || '').length};
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
  const visible = (e) => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const near = (el) => { const f = el.closest('figure, .chart, .card, section, div'); const h = f && f.querySelector('figcaption, h1, h2, h3, h4, .title, .chart-title');
    return (el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'))) || (h && h.innerText.trim()) || el.id || ''; };
  try { const C = window.Chart; if (C) { const inst = C.instances ? Object.values(C.instances) : [];
    for (const c of inst) out.push({lib: 'chartjs', type: c.config.type, title: (c.options && c.options.plugins && c.options.plugins.title && c.options.plugins.title.text) || c.canvas.getAttribute('aria-label') || near(c.canvas),
      labels: c.data.labels, series: c.data.datasets.map(d => ({name: d.label, type: d.type, values: d.data}))}); } } catch (e) {}
  try { if (window.echarts) for (const el of document.querySelectorAll('[_echarts_instance_]')) { const o = echarts.getInstanceByDom(el).getOption();
    out.push({lib: 'echarts', title: (o.title && o.title[0] && o.title[0].text) || near(el), labels: (o.xAxis && o.xAxis[0] && o.xAxis[0].data) || (o.yAxis && o.yAxis[0] && o.yAxis[0].data) || null,
      series: o.series.map(s => ({name: s.name, type: s.type, values: s.data}))}); } } catch (e) {}
  try { if (window.Highcharts) for (const c of Highcharts.charts.filter(Boolean)) out.push({lib: 'highcharts', type: c.options.chart && c.options.chart.type,
      title: c.title && c.title.textStr, labels: null, series: c.series.map(s => ({name: s.name, type: s.type, values: s.points.map(p => ({name: p.name != null ? p.name : p.category, value: p.y}))}))}); } catch (e) {}
  try { for (const gd of document.querySelectorAll('.js-plotly-plot')) out.push({lib: 'plotly', title: gd.layout && gd.layout.title && (gd.layout.title.text || gd.layout.title) || near(gd),
      labels: null, series: gd.data.map(t => ({name: t.name, type: t.type, labels: t.labels || (t.orientation === 'h' ? t.y : t.x), values: t.values || (t.orientation === 'h' ? t.x : t.y)}))}); } catch (e) {}
  try { const A = window.Apex && window.Apex._chartInstances; if (A) for (const i of A) { const w = i.chart && i.chart.w; if (!w) continue; const cfg = w.config;
      const labels = (cfg.labels && cfg.labels.length ? cfg.labels : (cfg.xaxis && cfg.xaxis.categories)) || w.globals.labels || null;
      const series = Array.isArray(cfg.series) && typeof cfg.series[0] === 'number' ? [{name: null, type: cfg.chart.type, values: cfg.series}] : (cfg.series || []).map(s => ({name: s.name, type: s.type || cfg.chart.type, values: s.data}));
      out.push({lib: 'apexcharts', type: cfg.chart.type, title: (cfg.title && cfg.title.text) || near(i.el || document.body), labels, series}); } } catch (e) {}
  const seen = new Set();
  try { for (const svg of document.querySelectorAll('svg')) { if (!visible(svg) || seen.has(svg) || svg.closest('.highcharts-container, .apexcharts-canvas, .js-plotly-plot, [_echarts_instance_]')) continue;
      let marks = [...svg.querySelectorAll('[data-value]')].map(m => ({name: m.getAttribute('data-label') || m.getAttribute('aria-label'), value: m.getAttribute('data-value')}));
      let lib = 'svg';
      if (!marks.length) { // d3: the data bound to each mark
        const bound = [...svg.querySelectorAll('rect, path, circle')].filter(m => m.__data__ !== undefined && m.__data__ !== null);
        for (const m of bound) { let d = m.__data__; if (d && d.data && typeof d.data === 'object') d = d.data; if (typeof d !== 'object') continue;
          const ks = Object.keys(d); const lk = ks.find(k => typeof d[k] === 'string'); const vk = ks.find(k => typeof d[k] === 'number' && !/^(x|y|x0|y0|x1|y1|index|width|height|startAngle|endAngle|padAngle)$/.test(k));
          if (lk && vk) marks.push({name: d[lk], value: d[vk]}); }
        if (marks.length) lib = 'd3'; }
      if (!marks.length) { // accessible marks: aria-label "Dubai: 9,450.00"
        for (const m of svg.querySelectorAll('[aria-label]')) { const t = m.getAttribute('aria-label'); const mm = /^(.+?)[:,]\s*([-\d.,]+%?)\s*$/.exec(t || ''); if (mm && m !== svg) marks.push({name: mm[1].trim(), value: mm[2]}); }
        if (marks.length) lib = 'svg aria'; }
      if (marks.length >= 2) { seen.add(svg); const pie = !!svg.querySelector('path[d*="A"]') && !svg.querySelector('rect[height]');
        out.push({lib, type: svg.getAttribute('data-chart') || (pie ? 'pie' : 'bar'), title: svg.getAttribute('aria-label') || near(svg), labels: null, series: [{name: null, values: marks}]}); } } } catch (e) {}
  const canvases = [...document.querySelectorAll('canvas')].filter(visible).filter(c => c.width > 120 && c.height > 80).length;
  return {charts: out, canvases};
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


_RESOLVED: dict[str, bool] = {}


def private_host(host: str) -> bool:
    """Is this host on an internal network (loopback, private, link-local)? Literal addresses are checked directly;
    names are resolved once (cached) so that a public-looking name pointing inside is caught too."""
    host = (host or "").lower().strip("[]")
    if host in _RESOLVED:
        return _RESOLVED[host]
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".lan", ".corp", ".intranet")):
        _RESOLVED[host] = True
        return True
    addrs: list[str] = []
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            import socket

            addrs = [a[4][0] for a in socket.getaddrinfo(host, None)][:8]
        except OSError:
            addrs = []
    priv = False
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            priv = True
    _RESOLVED[host] = priv
    return priv


def site_of(host: str) -> str:
    """The registrable part of a host name, roughly (www.shop.example.com -> example.com, a.b.co.uk -> b.co.uk)."""
    parts = (host or "").lower().split(".")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if len(parts) >= 3 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


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
    """The record total the page states. 'Showing 1 to 10 of 250' wins over a bare '50 entries' (a page-size
    selector 'Show 10 25 50 entries' is not a total)."""
    showing, other = None, None
    for m in TOTAL_RE.finditer(text or ""):
        raw = m.group(2) or m.group(1)
        try:
            n = int(raw.replace(",", "").split(".")[0])
        except ValueError:
            continue
        if m.group(2):
            showing = n if showing is None else max(showing, n)
        elif not re.search(r"(?i)\bshow\s*$|\bper\s+page", (text or "")[max(0, m.start() - 30):m.start() + 1]):
            other = n if other is None else max(other, n)
    return showing if showing is not None else other


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
        self.redirected: list[str] = []
        self.sites = {site_of(h) for h in self.allowed}
        # an application on the intranet may load its own internal resources; a public one never reaches inside
        self.intranet = any(private_host(h) for h in self.allowed) or bool(config.get("allow_private_network"))
        self.strict = bool(config.get("strict_hosts"))
        self.pw = self.browser = self.context = self.page = None

    # ---- session -------------------------------------------------------------------------------------------------
    def log(self, step: str, **details: Any) -> None:
        self._log(step, {k: v for k, v in details.items() if v is not None})

    def __enter__(self) -> "WebExtractor":
        from ..automation import browser as bm

        self.pw = bm.start_playwright()
        self.browser = bm.launch(self.pw, self.browser_name, self.headless)
        opts: dict[str, Any] = {"accept_downloads": False}
        if self.config.get("auth_method") in ("basic", "windows", "ntlm") and self.username and self._password:
            # 23.9.0: the credentials are sent to the application's own origin only, never to a CDN or API host
            base = urlparse(self.config.get("base_url") or "")
            opts["http_credentials"] = {"username": self.username, "password": self._password,
                                        "origin": f"{base.scheme}://{base.netloc}"}
        try:
            self.context = bm.new_context(self.browser, self.headless, **opts)  # TLS checks stay on
        except TypeError:  # an older Playwright without the origin option
            opts.get("http_credentials", {}).pop("origin", None)
            self.context = bm.new_context(self.browser, self.headless, **opts)
        self.context.route("**/*", self._route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(int(self.config.get("timeout_ms") or 30000))
        return self

    def _route(self, route: Any) -> None:
        """23.9.0 request policy. Pages are only opened on the application's hosts (and a redirect within the same
        site, e.g. example.com -> www.example.com). The page may load its scripts, styles, fonts, images and data
        from public hosts (CDNs, its API), because a table drawn by JavaScript is empty without them (the 23.8.0
        cause of '0 records'). Internal network addresses and cloud metadata are never reached from a public
        application; 'strict_hosts' in the application's settings restores the old only-these-hosts rule."""
        req = route.request
        url = req.url
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if host_allowed(url, self.allowed):
            route.continue_()
            return
        reason = None
        if p.scheme not in ("http", "https", "data", "blob") or host in BLOCKED_HOSTS:
            reason = "blocked address"
        else:
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                    reason = "blocked address"
            except ValueError:
                pass
        main_nav = False
        try:
            main_nav = req.is_navigation_request() and req.frame == self.page.main_frame
        except Exception:
            main_nav = req.resource_type == "document"
        if reason is None and main_nav:
            if site_of(host) in self.sites and not (private_host(host) and not self.intranet):
                self.allowed.add(host)  # same site (www., login.): followed and logged
                self.redirected.append(host)
                self.log("followed a redirect within the application's site", host=host)
            else:
                reason = "page outside the application"
        if reason is None and not main_nav:
            if self.strict:
                reason = "not an approved host (strict hosts)"
            elif private_host(host) and not self.intranet:
                reason = "internal network address"
        if reason is None:
            route.continue_()
            return
        if len(self.blocked) < 50:
            self.blocked.append(f"{host or url[:40]} ({reason})")
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

    def wait_for_data(self, timeout: float | None = None) -> dict[str, Any]:
        """Wait until the page shows data: a table with cells, or a chart, and no loading indicator; the content
        must stop changing (JavaScript tables fill in after the page has 'loaded')."""
        limit = float(timeout if timeout is not None else (self.config.get("data_timeout_ms") or 15000) / 1000)
        end = time.monotonic() + limit
        last, stable, st = None, 0, {}
        while time.monotonic() < end:
            try:
                st = self.page.evaluate(READY_JS)
            except Exception:
                st = {}
            sig = (st.get("rows"), st.get("charts"), st.get("text"))
            stable = stable + 1 if sig == last and not st.get("busy") else 0
            last = sig
            if (st.get("rows") or st.get("charts")) and stable >= 2:
                break
            if not st.get("busy") and stable >= 6:
                break  # nothing more is coming
            time.sleep(0.4)
        self.log("waited for the data", rows=st.get("rows"), charts=st.get("charts"), busy=st.get("busy"))
        return st

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

    def _frames(self) -> list[Any]:
        """The page and its frames on approved hosts (a report inside an iframe of the same application)."""
        out = [self.page.main_frame]
        for f in self.page.frames:
            if f == self.page.main_frame:
                continue
            if f.url in ("", "about:blank") or host_allowed(f.url, self.allowed):
                out.append(f)
        return out

    def charts(self) -> list[dict[str, Any]]:
        charts: list[dict[str, Any]] = []
        self.canvases = 0
        for f in self._frames():
            try:
                res = f.evaluate(CHARTS_JS)
            except Exception:
                continue
            if isinstance(res, list):  # an older page script shape
                res = {"charts": res, "canvases": 0}
            charts += res.get("charts") or []
            self.canvases += int(res.get("canvases") or 0)
        for c in charts:
            c["series_values"] = chart_values(c)
            c["method"] = f"chart library ({c.get('lib')})"
            c["confidence"] = 1.0
        return charts

    def _table_page(self, selector: str | None, all_tables: bool = False) -> dict[str, Any] | None:
        """The main table of the page (or its frames): the biggest one, or the one of ``selector``."""
        frame = getattr(self, "_table_frame", None)
        frames = [frame] if frame is not None else self._frames()
        best = None
        for f in frames:
            try:
                data = f.evaluate(TABLE_JS, {"sel": selector or ("[data-qa-table-main]" if frame is not None else None),
                                             "all": all_tables})
                if data is None and frame is not None and not selector:
                    data = f.evaluate(TABLE_JS, {"sel": None, "all": all_tables})
            except Exception:
                continue
            if data and (best is None or len(data["rows"]) * max(1, len(data["headers"])) >
                         len(best[1]["rows"]) * max(1, len(best[1]["headers"]))):
                best = (f, data)
        if best is None:
            return None
        self._table_frame = best[0]
        return best[1]

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
        method, totals, row_keys = None, [], set()
        self.other_tables = []
        self._table_frame = None
        shown_total = self._shown_total(target)
        while True:
            if self.cancel():
                complete, reason = False, "stopped by the tester"
                break
            if target.get("scroll"):
                self._scroll_all(selector)
            data = self._table_page(selector, all_tables=pages == 0)
            if data is not None and pages == 0:
                self.other_tables = data.pop("others", None) or []
                if data.get("rowCount") and data["rowCount"] - 1 > len(data["rows"]):
                    data = self._virtual_rows(selector, data)
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
            method = method or data.get("method") or "dom"
            totals = totals or data.get("totals") or []
            png = self.screenshot() if screenshots else None
            fresh = []
            for row in data["rows"]:
                rk = json.dumps(row)
                if target.get("scroll") and rk in row_keys:
                    continue  # infinite scroll / virtual grid: the same row read twice
                row_keys.add(rk)
                fields = {h or f"Column {j + 1}": {"value": v, "method": "dom", "confidence": 1.0}
                          for j, (h, v) in enumerate(zip(headers, row))}
                records.append({"page": pages, "fields": fields})
                fresh.append(row)
            if on_page:
                on_page(pages, {**data, "rows": fresh, "headers": headers}, png)
            self.log(f"read table page {pages}", rows=len(data["rows"]), method=data.get("method"))
            if target.get("scroll") or target.get("single_page") or data.get("complete"):
                break  # DataTables gave every filtered row through its API
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
                 "failures": failures, "expected_total": shown_total, "method": method or "dom", "headers": headers,
                 "totals": totals, "blocked_requests": sorted(set(self.blocked))[:20]}
        if complete and shown_total is not None and shown_total != len(records):
            stats["complete"] = False
            stats["reason"] = f"read {len(records)} records but the application shows {shown_total}"
        return {"records": records, "stats": stats, "headers": headers}

    def _virtual_rows(self, selector: str | None, first: dict[str, Any]) -> dict[str, Any]:
        """A virtualized grid (only the visible rows exist in the page): scroll it and collect the rows by their
        row index until every row the grid announces (aria-rowcount) was seen or nothing new comes."""
        rows: dict[str, list[str]] = {}
        for i, r in zip(first.get("rowIndex") or [], first["rows"]):
            rows[str(i if i is not None else json.dumps(r))] = r
        stale = 0
        for _ in range(400):
            if self.cancel():
                break
            moved = self.page.evaluate("""() => { const g = document.querySelector('[data-qa-table-main]'); if (!g) return false;
                const boxes = [g, ...g.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 4 && getComputedStyle(e).overflowY !== 'visible');
                const b = boxes[0]; if (!b) { window.scrollBy(0, window.innerHeight * 0.8); return true; }
                const before = b.scrollTop; b.scrollTop = before + b.clientHeight * 0.8; return b.scrollTop !== before; }""")
            time.sleep(0.3)
            data = self._table_page(selector)
            if not data:
                break
            n = len(rows)
            for i, r in zip(data.get("rowIndex") or [None] * len(data["rows"]), data["rows"]):
                rows.setdefault(str(i if i is not None else json.dumps(r)), r)
            stale = stale + 1 if len(rows) == n else 0
            if (first.get("rowCount") and len(rows) >= first["rowCount"] - 1) or stale >= 4 or not moved and stale:
                break
        ordered = [rows[k] for k in sorted(rows, key=lambda k: (0, int(k)) if k.isdigit() else (1, k))]
        self.log("read a virtualized grid by scrolling", rows=len(ordered), announced=first.get("rowCount"))
        return {**first, "rows": ordered, "method": f"{first.get('method')} (virtualized, scrolled)",
                "complete": False}

    def _shown_total(self, target: dict[str, Any]) -> int | None:
        """The record total the application displays: from the configured element, else the page text."""
        if target.get("check_total") is False:
            return None
        sel = target.get("total_selector")
        try:
            text = self.page.locator(sel).first.inner_text() if sel else self.page.evaluate(
                "() => { const c = document.body.cloneNode(true); c.querySelectorAll('select, option, script, style')"
                ".forEach(e => e.remove()); return c.innerText || c.textContent || ''; }")
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
