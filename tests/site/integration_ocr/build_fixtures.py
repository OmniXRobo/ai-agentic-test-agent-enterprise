"""Builds the HTML pages behind the 23.9.0 OCR / chart regression screenshots.

The tests render these pages in Chromium, take a screenshot and give the PNG to the real offline OCR engine, exactly
as a tester uploads a screenshot. The numbers are computed here (Decimal), so the tests know the true values; nothing
in the application knows them.

Four applications over one set of orders, as in the tester's scenario:
  A  sales transactions (8 orders, a TOTAL row)                       light theme
  B  the same orders with Subtotal / Discount / Tax / Final Amount   dark theme, two-line "(AED)" headers
  C  bar chart "Total Sales by City" (one bar deliberately wrong)
  D  pie chart "Sales Distribution" with amounts and percentages
plus variants: an unlabeled bar chart with an axis, a horizontal bar chart, a donut chart, and a city summary table.

    python tests/site/integration_ocr/build_fixtures.py
"""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).parent
ORDERS = [
    ("ORD-1001", "Ahmed Khan", "Dubai", "Laptop", 2, "3500.00", "200.00"),
    ("ORD-1002", "Sara Ali", "Abu Dhabi", "Monitor", 3, "850.00", "0.00"),
    ("ORD-1003", "John Smith", "Sharjah", "Printer", 1, "1200.00", "50.00"),
    ("ORD-1004", "Fatima Noor", "Dubai", "Keyboard", 5, "150.00", "25.00"),
    ("ORD-1005", "Ravi Patel", "Ajman", "Laptop", 1, "3500.00", "100.00"),
    ("ORD-1006", "Maria Lopez", "Abu Dhabi", "Desk Chair", 4, "620.00", "80.00"),
    ("ORD-1007", "Omar Hassan", "Dubai", "Monitor", 2, "850.00", "0.00"),
    ("ORD-1008", "Li Wei", "Sharjah", "Tablet", 3, "1450.00", "150.00"),
]
TAX = Decimal("0.05")
C2 = Decimal("0.01")


def money(d: Decimal) -> str:
    return f"{d.quantize(C2, rounding=ROUND_HALF_UP):,.2f}"


def rows() -> list[dict]:
    out = []
    for oid, cust, city, prod, qty, price, disc in ORDERS:
        q, p, dc = Decimal(qty), Decimal(price), Decimal(disc)
        sub = q * p
        tax = ((sub - dc) * TAX).quantize(C2, rounding=ROUND_HALF_UP)
        out.append({"Order ID": oid, "Customer": cust, "City": city, "Product": prod, "Quantity": q,
                    "Unit Price": p, "Line Total": sub, "Subtotal": sub, "Discount": dc, "Tax": tax,
                    "Final Amount": sub - dc + tax})
    return out


def by_city(rs: list[dict], field: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for r in rs:
        out[r["City"]] = out.get(r["City"], Decimal(0)) + r[field]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


CSS_LIGHT = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#fff;color:#1f2937}
h2{font-size:20px;margin:0 0 14px}table{border-collapse:collapse;font-size:15px}
th,td{border:1px solid #d1d5db;padding:7px 12px;text-align:left}th{background:#f3f4f6}
td.n{text-align:right}tr.total td{font-weight:700;background:#f9fafb}"""
CSS_DARK = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#0b1220;color:#e5e7eb}
h2{font-size:20px;margin:0 0 14px;color:#93c5fd}table{border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid #1f2a44;padding:7px 10px;text-align:left}th{color:#93c5fd;font-weight:600;vertical-align:bottom}
td.n,th.n{text-align:right}tr:nth-child(even) td{background:#111a2e}"""


def page(title: str, css: str, body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{css}</style></head><body>{body}</body></html>"


def table_a(rs: list[dict]) -> str:
    cols = ["Order ID", "Customer", "City", "Product", "Quantity", "Unit Price", "Line Total"]
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = ""
    for r in rs:
        body += "<tr>" + "".join(
            f"<td class='n'>{money(r[c]) if c in ('Unit Price', 'Line Total') else r[c]}</td>" if c in
            ("Quantity", "Unit Price", "Line Total") else f"<td>{r[c]}</td>" for c in cols) + "</tr>"
    qty = sum(r["Quantity"] for r in rs)
    tot = sum(r["Line Total"] for r in rs)
    body += f"<tr class='total'><td>TOTAL</td><td></td><td></td><td></td><td class='n'>{qty}</td><td></td><td class='n'>{money(tot)}</td></tr>"
    return page("Sales Transactions", CSS_LIGHT, f"<h2>Sales Transactions</h2><table><tr>{head}</tr>{body}</table>")


def table_b(rs: list[dict]) -> str:
    heads = [("Order ID", ""), ("Customer", ""), ("City", ""), ("Product", ""), ("Quantity", ""),
             ("Unit Price", "(AED)"), ("Subtotal", "(AED)"), ("Discount", "(AED)"), ("Tax", "(AED)"),
             ("Final Amount", "(AED)")]
    head = "".join(f"<th class='{'n' if u else ''}'>{h}{'<br>' + u if u else ''}</th>" for h, u in heads)
    body = ""
    for r in rs:
        body += "<tr>" + "".join(
            f"<td class='n'>{money(r[h]) if u else r[h]}</td>" if (u or h == "Quantity") else f"<td>{r[h]}</td>"
            for h, u in heads) + "</tr>"
    fin = sum(r["Final Amount"] for r in rs)
    body += (f"<tr><td><b>GRAND TOTAL</b></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td>"
             f"<td class='n'><b>{money(fin)}</b></td></tr>")
    return page("Detailed Sales", CSS_DARK, f"<h2>Detailed Sales Records</h2><table><tr>{head}</tr>{body}</table>")


def bars(title: str, data: dict[str, Decimal], labels: bool = True, axis: bool = False) -> str:
    w, h, left, bottom, top = 760, 420, 90, 60, 50
    mx = max(data.values())
    scale_max = Decimal(math.ceil(float(mx) / 5000) * 5000)
    bw = 70
    gap = (w - left - 40 - bw * len(data)) / max(1, len(data))
    colors = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2"]
    svg = [f"<svg width='{w}' height='{h}' xmlns='http://www.w3.org/2000/svg' font-family='Arial' font-size='15'>",
           f"<text x='{w / 2}' y='28' text-anchor='middle' font-size='20' font-weight='bold'>{title}</text>"]
    base = h - bottom
    plot_h = base - top
    if axis:
        for i in range(6):
            v = scale_max * i / 5
            y = base - plot_h * i / 5
            svg.append(f"<line x1='{left}' x2='{w - 30}' y1='{y}' y2='{y}' stroke='#e5e7eb'/>")
            svg.append(f"<text x='{left - 10}' y='{y + 5}' text-anchor='end' fill='#374151'>{int(v):,}</text>")
    svg.append(f"<line x1='{left}' x2='{w - 30}' y1='{base}' y2='{base}' stroke='#6b7280'/>")
    for i, (cat, v) in enumerate(data.items()):
        x = left + gap / 2 + i * (bw + gap)
        bh = float(Decimal(plot_h) * v / scale_max)
        svg.append(f"<rect x='{x}' y='{base - bh}' width='{bw}' height='{bh}' fill='{colors[i % len(colors)]}'/>")
        svg.append(f"<text x='{x + bw / 2}' y='{base + 24}' text-anchor='middle' fill='#111827'>{cat}</text>")
        if labels:
            svg.append(f"<text x='{x + bw / 2}' y='{base - bh - 8}' text-anchor='middle' fill='#111827'>{money(v)}</text>")
    svg.append("</svg>")
    return page(title, CSS_LIGHT, "".join(svg))


def hbars(title: str, data: dict[str, Decimal]) -> str:
    w, h, left = 760, 330, 130
    mx = max(data.values())
    svg = [f"<svg width='{w}' height='{h}' xmlns='http://www.w3.org/2000/svg' font-family='Arial' font-size='15'>",
           f"<text x='20' y='28' font-size='20' font-weight='bold'>{title}</text>"]
    for i, (cat, v) in enumerate(data.items()):
        y = 60 + i * 62
        bw = float(Decimal(460) * v / mx)
        svg.append(f"<text x='{left - 12}' y='{y + 25}' text-anchor='end'>{cat}</text>")
        svg.append(f"<rect x='{left}' y='{y}' width='{bw}' height='38' fill='#0ea5e9'/>")
        svg.append(f"<text x='{left + bw + 10}' y='{y + 25}'>{money(v)}</text>")
    svg.append("</svg>")
    return page(title, CSS_LIGHT, "".join(svg))


def pie(title: str, data: dict[str, Decimal], donut: bool = False) -> str:
    total = sum(data.values())
    colors = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed"]
    cx, cy, r = 220, 240, 160
    svg = [f"<svg width='820' height='470' xmlns='http://www.w3.org/2000/svg' font-family='Arial' font-size='16'>",
           f"<text x='20' y='34' font-size='20' font-weight='bold'>{title}</text>"]
    a0 = -math.pi / 2
    for i, (cat, v) in enumerate(data.items()):
        a1 = a0 + 2 * math.pi * float(v / total)
        x0, y0 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if a1 - a0 > math.pi else 0
        svg.append(f"<path d='M{cx},{cy} L{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f} Z' fill='{colors[i]}'/>")
        a0 = a1
    if donut:
        svg.append(f"<circle cx='{cx}' cy='{cy}' r='{r * 0.55}' fill='#ffffff'/>")
        svg.append(f"<text x='{cx}' y='{cy - 4}' text-anchor='middle' font-size='14'>Total</text>")
        svg.append(f"<text x='{cx}' y='{cy + 18}' text-anchor='middle' font-size='15' font-weight='bold'>{money(total)}</text>")
    for i, (cat, v) in enumerate(data.items()):
        y = 120 + i * 48
        pct = (v / total * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        svg.append(f"<rect x='440' y='{y - 15}' width='18' height='18' fill='{colors[i]}'/>")
        svg.append(f"<text x='470' y='{y}'>{cat}</text>")
        svg.append(f"<text x='600' y='{y}'>{money(v)}</text>")
        svg.append(f"<text x='740' y='{y}'>{pct}%</text>")
    svg.append("</svg>")
    return page(title, CSS_LIGHT, "".join(svg))


def summary(data: dict[str, Decimal]) -> str:
    total = sum(data.values())
    body = "".join(f"<tr><td>{c}</td><td class='n'>{money(v)}</td><td class='n'>"
                   f"{(v / total * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%</td></tr>" for c, v in data.items())
    body += f"<tr class='total'><td>Grand Total</td><td class='n'>{money(total)}</td><td class='n'>100.00%</td></tr>"
    return page("Sales Summary", CSS_LIGHT, "<h2>Sales Summary by Territory</h2><table><tr><th>Territory</th>"
                f"<th>Sales Amount</th><th>Share %</th></tr>{body}</table>")


def main() -> None:
    rs = rows()
    city = by_city(rs, "Line Total")
    wrong = dict(city)
    first = next(iter(wrong))
    wrong[first] = wrong[first] - Decimal("500.00")  # application C shows one bar 500 too low
    pages = {"app_a_table.html": table_a(rs), "app_b_detailed.html": table_b(rs),
             "app_c_bar.html": bars("Total Sales by City", wrong), "app_d_pie.html": pie("Sales Distribution", city),
             "bar_unlabeled.html": bars("Sales by City", city, labels=False, axis=True),
             "hbar.html": hbars("Sales by City", city), "donut.html": pie("Sales Share", city, donut=True),
             "summary.html": summary(city)}
    for name, html in pages.items():
        (HERE / name).write_text(html, encoding="utf-8")
    truth = {"orders": [{k: str(v) for k, v in r.items()} for r in rs],
             "city_line_total": {k: str(v) for k, v in city.items()},
             "bar_shown": {k: str(v) for k, v in wrong.items()}, "wrong_bar": first}
    (HERE / "truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
