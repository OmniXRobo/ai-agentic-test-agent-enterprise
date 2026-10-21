"""Server-side SVG charts for reports (no scripts, so they print to PDF exactly as shown).

One measure per chart; stacked bars carry a legend, single-series charts are named by their title.
"""

from __future__ import annotations

import html
import math
from typing import Any

W, H, L, R, T, B = 460, 170, 44, 10, 8, 24


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(v))
    n = v / p
    return (1 if n <= 1 else 2 if n <= 2 else 5 if n <= 5 else 10) * p


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v / 1000:.0f}k" if v >= 10000 else f"{v / 1000:.1f}k"
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _duration(s: int) -> str:
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    return (f"{m}m {r}s" if r else f"{m}m") if m < 60 else f"{m // 60}h {m % 60}m"


def _grid(y_max: float) -> str:
    out = []
    for i in range(5):
        v = y_max / 4 * i
        y = T + (1 - v / y_max) * (H - T - B)
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y + 3.5:.1f}" text-anchor="end" class="axis">{_fmt(v)}</text>')
    return "".join(out)


def _x_labels(labels: list[str], xs: list[float]) -> str:
    """At most five evenly spaced labels so they never collide."""
    if not labels:
        return ""
    n = len(labels)
    picks = sorted({round(i * (n - 1) / 4) for i in range(5)}) if n > 5 else range(n)
    out, last = [], None
    for i in picks:
        if labels[i] == last:  # several runs on one day: label the day once
            continue
        last = labels[i]
        anchor = "start" if i == 0 and n > 1 else "end" if i == n - 1 and n > 1 else "middle"
        out.append(f'<text x="{xs[i]:.1f}" y="{H - 7}" text-anchor="{anchor}" class="axis">{html.escape(labels[i])}</text>')
    return "".join(out)


def _svg(label: str, body: str) -> str:
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(label)}">{body}</svg>'


def stacked_bars(trend: list[dict[str, Any]], series: list[tuple[str, str, str]]) -> str:
    """Daily stacked bars. trend = [{"date": "YYYY-MM-DD", key: n, ...}]; series = [(key, label, color)]."""
    if not trend:
        return '<p class="muted">No data.</p>'
    totals = [sum(float(t.get(k) or 0) for k, _l, _c in series) for t in trend]
    y_max = float(max(4, math.ceil(max(totals) / 4) * 4))
    slot = (W - L - R) / len(trend)
    bw = max(2.0, min(28.0, slot - 2))
    xs, body = [], [_grid(y_max)]
    for i, t in enumerate(trend):
        cx = L + slot * (i + 0.5)
        xs.append(cx)
        base = H - B
        tip = [t["date"]]
        for key, lbl, color in series:
            v = float(t.get(key) or 0)
            if v <= 0:
                continue
            h = v / y_max * (H - T - B)
            # 2px surface gap between stacked segments
            body.append(f'<rect x="{cx - bw / 2:.1f}" y="{base - h:.1f}" width="{bw:.1f}" height="{max(0.0, h - 1):.1f}" '
                        f'rx="2" fill="{color}"><title>{html.escape(t["date"])}: {_fmt(v)} {html.escape(lbl.lower())}</title></rect>')
            base -= h
            tip.append(f"{_fmt(v)} {lbl.lower()}")
    body.append(_x_labels([t["date"][5:] for t in trend], xs))
    legend = '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{html.escape(lbl)}</span>'
                                              for _k, lbl, c in series) + "</div>"
    return _svg("Stacked daily totals: " + ", ".join(lbl for _k, lbl, _c in series), "".join(body)) + legend


def _line(points: list[tuple[float, float]], labels: list[str], title: str, color: str, x_max: float,
          tips: list[str]) -> str:
    ys = [p[1] for p in points]
    y_max = _nice_max(max(ys) if ys else 0)
    x = (lambda v: L + (v / x_max) * (W - L - R)) if x_max else (lambda v: L + (W - L - R) / 2)
    y = lambda v: T + (1 - v / y_max) * (H - T - B)  # noqa: E731
    xs = [x(p[0]) for p in points]
    d = "".join(f"{'L' if i else 'M'}{xs[i]:.1f},{y(p[1]):.1f}" for i, p in enumerate(points))
    body = [_grid(y_max), f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>']
    if len(points) <= 40:
        body += [f'<circle cx="{xs[i]:.1f}" cy="{y(p[1]):.1f}" r="4" fill="{color}" stroke="#fff" stroke-width="2">'
                 f'<title>{html.escape(tips[i])}</title></circle>' for i, p in enumerate(points)]
    body.append(_x_labels(labels, xs))
    return (f'<div class="chart"><div class="chart-title">{html.escape(title)}</div>'
            + _svg(title, "".join(body)) + "</div>")


def run_chart(runs: list[dict[str, Any]], key: str, title: str, color: str) -> str:
    """One point per run, in run order, labelled with the local run date."""
    pts = [(i, float(r[key])) for i, r in enumerate(runs) if r.get(key) is not None]
    if not pts:
        return f'<div class="chart"><div class="chart-title">{html.escape(title)}</div><p class="muted">No data.</p></div>'
    idx = [i for i, _ in pts]
    labels = [(runs[i].get("started_at") or "")[5:10] for i in idx]
    tips = [f"{runs[i].get('name', '')} {(runs[i].get('started_at') or '')[:16].replace('T', ' ')}: {_fmt(v)}" for i, v in pts]
    return _line([(n, v) for n, (_i, v) in enumerate(pts)], labels, title, color, max(1, len(pts) - 1), tips)


def timeline_chart(timeline: list[dict[str, Any]], key: str, title: str, color: str) -> str:
    """A measure over elapsed run time."""
    if not timeline:
        return f'<div class="chart"><div class="chart-title">{html.escape(title)}</div><p class="muted">No data.</p></div>'
    pts = [(float(p["t"]), float(p.get(key) or 0)) for p in timeline]
    labels = [_duration(int(p[0])) for p in pts]
    tips = [f"{_duration(int(t))}: {_fmt(v)}" for t, v in pts]
    return _line(pts, labels, title, color, max(1.0, pts[-1][0]), tips)
