"""Integration Comparison Workspace (23.8.0): read one screenshot into tables, labelled values and charts.

Input is the word list of the offline OCR engines in ``ocr.py`` (Windows OCR or Tesseract): every word with its box
and confidence. Nothing here guesses a value that is not on the screen:

* words are grouped into screen rows (by vertical position) and cells (by the horizontal gap between words);
* a TABLE is a text header row followed by two or more rows whose cells sit under the header's columns;
* a header row with exactly one row of values under it (KPI cards, a record form) gives LABELLED VALUES, as do
  "Label: value" texts and a label with its number on the next line;
* a CHART is a row of category labels with number labels printed above them (the data labels of a bar / column
  chart). A chart without readable data labels is reported with ``extracted: False`` so the comparison asks for a
  visual check instead of passing it;
* every cell keeps its OCR confidence and box, so the workspace can highlight what the tester should check.
"""

from __future__ import annotations

import re
from statistics import median
from typing import Any

from .normalize import parse_number

LOW = 0.80
NUM = re.compile(r"^[(\-+]?(?:[A-Z]{3}\s?|[$€£₹])?\s?\d[\d,.\s]*%?\)?$")
COLON = re.compile(r"^(?P<label>[A-Za-z][^:]{0,60}?)\s*:\s*(?P<value>\S.{0,80})$")
LABEL_NUM = re.compile(r"^(?P<label>[A-Za-z][A-Za-z %/&().#-]{1,60}?)\s+(?P<value>[(\-+]?(?:[A-Z]{3}\s?|[$€£₹])?"
                       r"\d[\d,.]*%?\)?)$")


def is_number(text: str) -> bool:
    t = (text or "").strip()
    return bool(NUM.match(t)) and parse_number(t) is not None


def _union(boxes: list[list[float]]) -> list[float]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return [x1, y1, x2 - x1, y2 - y1]


def _cx(box: list[float]) -> float:
    return box[0] + box[2] / 2


def rows_of(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Screen rows with their cells. A cell is a run of words separated by less than ~one character height."""
    words = [w for w in words if str(w.get("text", "")).strip() and w.get("box")]
    if not words:
        return []
    h = median(w["box"][3] for w in words) or 10
    # chart bars, borders and icons read as "I", "|", "l": one tall non-digit character is not text
    words = [w for w in words if not (len(str(w["text"]).strip()) == 1 and not str(w["text"]).strip().isdigit()
                                      and w["box"][3] > h * 1.8)]
    tol = max(3.0, h * 0.55)
    groups: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: w["box"][1] + w["box"][3] / 2):
        cy = w["box"][1] + w["box"][3] / 2
        if groups and abs(cy - groups[-1][0]["_cy"]) <= tol:
            groups[-1].append({**w, "_cy": groups[-1][0]["_cy"]})
        else:
            groups.append([{**w, "_cy": cy}])
    gap = max(8.0, h * 0.95)
    out = []
    for g in groups:
        g.sort(key=lambda w: w["box"][0])
        cells: list[dict[str, Any]] = []
        for w in g:
            conf = float(w.get("confidence", 1.0) or 0.0)
            if cells and w["box"][0] - (cells[-1]["box"][0] + cells[-1]["box"][2]) <= gap:
                c = cells[-1]
                c["text"] += " " + w["text"]
                c["box"] = _union([c["box"], w["box"]])
                c["confidence"] = min(c["confidence"], conf)
            else:
                cells.append({"text": w["text"], "box": list(w["box"]), "confidence": conf})
        for c in cells:
            c["text"] = c["text"].strip()
            c["numeric"] = is_number(c["text"])
        out.append({"y": g[0]["_cy"], "cells": cells, "box": _union([c["box"] for c in cells]), "used": False})
    return out


def _cell(c: dict[str, Any]) -> dict[str, Any]:
    return {"value": c["text"], "confidence": round(c["confidence"], 3), "box": c["box"], "method": "ocr"}


def _bands(head: list[dict[str, Any]]) -> list[tuple[float, float]]:
    bands = []
    for i, c in enumerate(head):
        left = -1e9 if i == 0 else (head[i - 1]["box"][0] + head[i - 1]["box"][2] + c["box"][0]) / 2
        right = 1e9 if i == len(head) - 1 else (c["box"][0] + c["box"][2] + head[i + 1]["box"][0]) / 2
        bands.append((left, right))
    return bands


def _column(bands: list[tuple[float, float]], cell: dict[str, Any]) -> int:
    x = _cx(cell["box"])
    return next((i for i, (lo, hi) in enumerate(bands) if lo <= x < hi), len(bands) - 1)


def _fits(row: dict[str, Any], bands: list[tuple[float, float]]) -> bool:
    if len(row["cells"]) < 2:
        return False
    cols = [_column(bands, c) for c in row["cells"]]
    return len(set(cols)) >= max(2, int(len(bands) * 0.5 + 0.5))


def _is_header(row: dict[str, Any]) -> bool:
    cells = row["cells"]
    return len(cells) >= 2 and sum(1 for c in cells if not c["numeric"]) >= max(2, len(cells) - 1) and \
        not any(c["numeric"] for c in cells[:1])


def read_screen(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """{tables, values, charts, texts, low_confidence} from an OCR result (``ocr.read``)."""
    words = [w for ln in ocr_result.get("lines") or [] for w in ln.get("words") or []]
    rows = rows_of(words)
    if not rows:
        return {"tables": [], "values": [], "charts": [], "texts": [], "low_confidence": 0}
    spacing = median([b["y"] - a["y"] for a, b in zip(rows, rows[1:])] or [20]) or 20
    charts = _charts(rows, spacing)
    tables, values = _tables(rows, spacing)
    values += _labelled(rows)
    texts = [" ".join(c["text"] for c in r["cells"]) for r in rows if not r["used"]]
    low = sum(1 for t in tables for r in t["rows"] for c in r.values() if c["confidence"] < LOW)
    low += sum(1 for v in values if v["confidence"] < LOW)
    low += sum(1 for ch in charts for p in ch["points"].values() if p["confidence"] < LOW)
    return {"tables": tables, "values": values, "charts": charts, "texts": texts[:60], "low_confidence": low}


def _tables(rows: list[dict[str, Any]], spacing: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables, values = [], []
    i = 0
    while i < len(rows):
        head = rows[i]
        if head["used"] or not _is_header(head):
            i += 1
            continue
        bands = _bands(head["cells"])
        body = []
        j = i + 1
        while j < len(rows) and not rows[j]["used"] and _fits(rows[j], bands) and \
                rows[j]["y"] - rows[j - 1]["y"] <= spacing * 2.6:
            if body and not any(c["numeric"] for c in rows[j]["cells"]) and \
                    any(c["numeric"] for c in body[-1]["cells"]) and len(body) == 1:
                break  # the label row of the next group of KPI cards
            body.append(rows[j])
            j += 1
        names = [c["text"] for c in head["cells"]]
        if len(body) >= 2:
            recs = []
            for r in body:
                rec: dict[str, Any] = {}
                for c in r["cells"]:
                    name = names[_column(bands, c)]
                    if name in rec:
                        rec[name]["value"] += " " + c["text"]
                        rec[name]["confidence"] = min(rec[name]["confidence"], round(c["confidence"], 3))
                        rec[name]["box"] = _union([rec[name]["box"], c["box"]])
                    else:
                        rec[name] = _cell(c)
                recs.append(rec)
            tables.append({"headers": names, "rows": recs,
                           "box": _union([head["box"]] + [r["box"] for r in body])})
            for r in [head, *body]:
                r["used"] = True
            i = j
            continue
        if len(body) == 1 and any(c["numeric"] for c in body[0]["cells"]) or \
                (len(body) == 1 and len(body[0]["cells"]) == len(head["cells"])):
            for c in body[0]["cells"]:
                k = _column(bands, c)
                label = head["cells"][k]
                values.append({"label": label["text"], **_cell(c),
                               "confidence": round(min(c["confidence"], label["confidence"]), 3),
                               "box": _union([label["box"], c["box"]])})
            head["used"] = body[0]["used"] = True
            i = j
            continue
        i += 1
    return tables, values


def _labelled(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """'Label: value', 'Label 1,234' and a label with its number on the next row."""
    out = []
    for i, r in enumerate(rows):
        if r["used"]:
            continue
        cells = r["cells"]
        took = False
        for k, c in enumerate(cells):
            m = COLON.match(c["text"]) or LABEL_NUM.match(c["text"])
            if m:
                out.append({"label": m.group("label").strip(), **_cell(c), "value": m.group("value").strip()})
                took = True
                continue
            if c["text"].endswith(":") and k + 1 < len(cells):
                nxt = cells[k + 1]
                out.append({"label": c["text"].rstrip(":").strip(), **_cell(nxt),
                            "confidence": round(min(c["confidence"], nxt["confidence"]), 3),
                            "box": _union([c["box"], nxt["box"]])})
                took = True
            elif not c["numeric"] and k + 1 < len(cells) and cells[k + 1]["numeric"] and len(cells) % 2 == 0 \
                    and k % 2 == 0:
                nxt = cells[k + 1]
                out.append({"label": c["text"], **_cell(nxt),
                            "confidence": round(min(c["confidence"], nxt["confidence"]), 3),
                            "box": _union([c["box"], nxt["box"]])})
                took = True
        if took:
            r["used"] = True
            continue
        if len(cells) == 1 and not cells[0]["numeric"] and i + 1 < len(rows) and not rows[i + 1]["used"]:
            nxt = rows[i + 1]["cells"]
            if len(nxt) == 1 and nxt[0]["numeric"] and abs(_cx(nxt[0]["box"]) - _cx(cells[0]["box"])) < \
                    max(cells[0]["box"][2], nxt[0]["box"][2]):
                out.append({"label": cells[0]["text"], **_cell(nxt[0]),
                            "confidence": round(min(cells[0]["confidence"], nxt[0]["confidence"]), 3),
                            "box": _union([cells[0]["box"], nxt[0]["box"]])})
                r["used"] = rows[i + 1]["used"] = True
    return out


def _charts(rows: list[dict[str, Any]], spacing: float) -> list[dict[str, Any]]:
    """Bar / column charts: a row of category labels with number labels above each category."""
    charts = []
    for i, r in enumerate(rows):
        cats = r["cells"]
        if r["used"] or len(cats) < 2 or any(c["numeric"] for c in cats) or i == 0:
            continue
        if any(len(c["text"]) > 30 for c in cats):
            continue
        if i + 1 < len(rows) and not rows[i + 1]["used"] and rows[i + 1]["y"] - r["y"] <= spacing * 2.6 and \
                _fits(rows[i + 1], _bands(cats)):
            continue  # a table header with its rows below, not chart categories
        left = cats[0]["box"][0] - (cats[1]["box"][0] - cats[0]["box"][0]) / 2
        pts: dict[str, dict[str, Any]] = {}
        used_rows: list[dict[str, Any]] = []
        axis = 0
        title = None
        for k in range(i - 1, -1, -1):
            up = rows[k]
            if up["used"]:
                break
            if any(not c["numeric"] for c in up["cells"]):
                if len(up["cells"]) == 1:
                    title = up
                break
            hit = False
            for c in up["cells"]:
                if _cx(c["box"]) < left:
                    axis += 1  # an axis tick on the left
                    hit = True
                    continue
                cat = min(cats, key=lambda x: abs(_cx(x["box"]) - _cx(c["box"])))
                if abs(_cx(cat["box"]) - _cx(c["box"])) <= max(cat["box"][2], c["box"][2]):
                    if cat["text"] not in pts:
                        pts[cat["text"]] = _cell(c)
                        hit = True
            if hit:
                used_rows.append(up)
            if r["y"] - up["y"] > spacing * 25:
                break
        # a chart needs data labels on most categories, or at least an axis (then values are not readable)
        if len(pts) >= max(2, (len(cats) + 1) // 2) or (axis >= 2 and title is not None):
            extracted = len(pts) == len(cats)
            charts.append({
                "title": title["cells"][0]["text"] if title else "Chart",
                "categories": [c["text"] for c in cats],
                "points": pts,
                "extracted": extracted,
                "note": None if extracted else ("Some bars have no readable value label; check this chart visually."
                                                if pts else "The chart has no value labels; values cannot be read "
                                                            "exactly from the picture. Check it visually."),
                "box": _union([r["box"]] + [u["box"] for u in used_rows] + ([title["box"]] if title else [])),
            })
            r["used"] = True
            for u in used_rows:
                u["used"] = True
            if title:
                title["used"] = True
    return charts
