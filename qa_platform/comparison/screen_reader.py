"""Integration Comparison Workspace: read one screenshot into tables, labelled values and charts.

23.9.0 rewrite of the 23.8.0 reader. What went wrong before, and what this module does instead:

* 23.8.0 decided a table's columns from the HEADER row alone, with each header cell made of words closer than one
  character height. Two header words of neighbouring columns ("Unit Price" "Subtotal") became one column, so the
  values of both columns were joined into one string, and the same text appeared again and again in a row. Columns
  now come from the BODY: the x-ranges the data cells of the rows occupy (columns are the bands of the page where
  cells line up vertically). Header words, also on two or three lines ("Unit Price" over "(AED)"), are then put into
  those columns. "(AED)" is the column's unit, not a column of its own.
* A row with one empty cell (no discount) ended the table and the rows after it were lost. Rows now belong to the
  table while they line up with its columns; an empty cell is an empty value.
* TOTAL / GRAND TOTAL / SUBTOTAL rows were read as ordinary records. They are kept apart as summary rows, and each
  number in them is checked against the sum of the records above it.
* A screenshot was "read" as soon as anything was found. Every table and chart now has an extraction status:
  COMPLETE (every visible row read and validated), PARTIAL (rows or cells missing), NEEDS_REVIEW (low-confidence or
  ambiguous values, a total that does not add up, chart values estimated from the drawing), FAILED (nothing usable).
  The status is about the VISIBLE rows only: a screenshot cannot prove that the application has no more records;
  the tester's "screenshots show all records" is a declaration of scope and is reported as such.
* Charts: bars and pie slices are found as coloured shapes (``ocr_worker.graphics``), so a chart is read from its
  data labels when it has them (exact), and from its axis or slice angles when it has none (APPROXIMATE, never a
  PASS on its own). Pie legends give category, amount and percentage per slice; percentages are checked to add up.

Nothing here guesses a value that is not on the screen; every cell keeps the OCR text, its confidence and its box.
"""

from __future__ import annotations

import re
from decimal import Decimal
from statistics import median
from typing import Any

from .normalize import parse_number

LOW = 0.80
NUM = re.compile(r"^[(\-+]?(?:[A-Z]{3}\s?|[$€£₹])?\s?\d[\d,.\s]*%?\)?(?:\s?[A-Z]{3})?$")
PCT = re.compile(r"^[(\-+]?\d[\d,.]*\s?%\)?$")
COLON = re.compile(r"^(?P<label>[A-Za-z][^:]{0,60}?)\s*:\s*(?P<value>\S.{0,80})$")
LABEL_NUM = re.compile(r"^(?P<label>[A-Za-z][A-Za-z %/&().#-]{1,60}?)\s+(?P<value>[(\-+]?(?:[A-Z]{3}\s?|[$€£₹])?"
                       r"\d[\d,.]*%?\)?)$")
TOTAL = re.compile(r"(?i)^\s*(?P<kind>grand\s*total|sub\s*-?\s*total|total(?:s)?|overall(?:\s+total)?|net\s+total|"
                   r"sum)\b")
UNIT = re.compile(r"^\(?\s*(?P<u>[A-Z]{3}|%|[$€£₹]|in\s+[A-Z]{3}|qty|pcs|units?|kg|km)\s*\)?$", re.I)
STATUSES = ("COMPLETE", "PARTIAL", "NEEDS_REVIEW", "FAILED")


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


def _cy(box: list[float]) -> float:
    return box[1] + box[3] / 2


def _worst(statuses: list[str]) -> str:
    order = {"FAILED": 0, "PARTIAL": 1, "NEEDS_REVIEW": 2, "COMPLETE": 3}
    return min(statuses, key=lambda s: order.get(s, 0)) if statuses else "FAILED"


# ---- lines and cells --------------------------------------------------------------------------------------------

def _clean_words(words: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    words = [dict(w) for w in words if str(w.get("text", "")).strip() and w.get("box")]
    if not words:
        return [], 10.0
    h = median(w["box"][3] for w in words) or 10.0
    # chart bars, borders and icons read as "I", "|", "l": one tall non-digit character is not text
    words = [w for w in words if not (len(str(w["text"]).strip()) == 1 and not str(w["text"]).strip().isdigit()
                                      and w["box"][3] > h * 1.8)]
    # legend swatches and bullets read as "■", "●", "•": a word without a letter or digit is decoration
    words = [w for w in words if re.search(r"[A-Za-z0-9\u0600-\u06FF%]", str(w["text"]))]
    for w in words:
        w["text"] = str(w["text"]).strip()
        w["confidence"] = float(w.get("confidence", 1.0) or 0.0)
    return words, h


def _off_shapes(words: list[dict[str, Any]], g: dict[str, Any], h: float) -> list[dict[str, Any]]:
    """Words the OCR 'read' inside a chart drawing (a pie read as 'O', a bar as 'I') that are mostly covered by a
    filled shape and are not numbers are not text. Labels printed on a bar or a slice are kept (numbers, or a word
    with good confidence)."""
    shapes = [r["box"] for r in g.get("rects") or [] if r["box"][2] > h * 1.2 and r["box"][3] > h * 1.2]
    shapes += [p["box"] for p in g.get("pies") or []]

    def covered(b: list[float]) -> float:
        area = max(1.0, b[2] * b[3])
        best = 0.0
        for s in shapes:
            ix = max(0.0, min(b[0] + b[2], s[0] + s[2]) - max(b[0], s[0]))
            iy = max(0.0, min(b[1] + b[3], s[1] + s[3]) - max(b[1], s[1]))
            best = max(best, ix * iy / area)
        return best
    return [w for w in words if not (covered(w["box"]) > 0.6 and w["confidence"] < 0.7 and not is_number(w["text"])
                                     and len(w["text"]) <= 2)]


def lines_of(words: list[dict[str, Any]], h: float) -> list[dict[str, Any]]:
    """Visual lines: words whose vertical centres are within about half a text height of each other."""
    tol = max(3.0, h * 0.5)
    groups: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: _cy(w["box"])):
        cy = _cy(w["box"])
        if groups and abs(cy - groups[-1][0]["_cy"]) <= tol:
            groups[-1].append({**w, "_cy": groups[-1][0]["_cy"]})
        else:
            groups.append([{**w, "_cy": cy}])
    out = []
    for g in groups:
        g.sort(key=lambda w: w["box"][0])
        out.append({"y": median(_cy(w["box"]) for w in g), "words": g, "box": _union([w["box"] for w in g]),
                    "cells": _cells(g, h), "used": False})
    out.sort(key=lambda ln: ln["y"])
    return out


def _cells(words: list[dict[str, Any]], h: float, gap: float | None = None) -> list[dict[str, Any]]:
    """Words of one line joined into cells: a cell's words are separated by about one space."""
    gap = gap if gap is not None else max(6.0, h * 0.7)
    cells: list[dict[str, Any]] = []
    for w in words:
        if cells and w["box"][0] - (cells[-1]["box"][0] + cells[-1]["box"][2]) <= gap and \
                not (is_number(w["text"]) and is_number(cells[-1]["text"])):
            c = cells[-1]
            c["text"] += " " + w["text"]
            c["box"] = _union([c["box"], w["box"]])
            c["confidence"] = min(c["confidence"], w["confidence"])
            c["words"].append(w)
        else:
            cells.append({"text": w["text"], "box": list(w["box"]), "confidence": w["confidence"], "words": [w]})
    for c in cells:
        c["numeric"] = is_number(c["text"])
    return cells


def rows_of(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kept for callers of 23.8.0: lines with cells."""
    ws, h = _clean_words(words)
    return lines_of(ws, h)


def _cell(c: dict[str, Any]) -> dict[str, Any]:
    return {"value": c["text"], "confidence": round(float(c["confidence"]), 3), "box": [round(v, 1) for v in c["box"]],
            "method": "ocr"}


# ---- tables -----------------------------------------------------------------------------------------------------

def _columns(lines: list[dict[str, Any]], h: float) -> list[list[float]]:
    """Column bands from the WORDS of the body lines: an x-position belongs to a column when some row has text
    there; a gutter is a strip where no row (or almost none) has text. Word boxes are shrunk a little first because
    engines pad them, so two neighbouring columns never touch."""
    if not lines:
        return []
    x_lo = int(min(w["box"][0] for ln in lines for w in ln["words"])) - 2
    x_hi = int(max(w["box"][0] + w["box"][2] for ln in lines for w in ln["words"])) + 2
    width = max(1, x_hi - x_lo)
    cover = [0] * (width + 1)
    for ln in lines:
        seen = [False] * (width + 1)
        for w in ln["words"]:
            pad = min(h * 0.15, w["box"][2] * 0.2)
            a = int(w["box"][0] + pad) - x_lo
            b = int(w["box"][0] + w["box"][2] - pad) - x_lo
            for x in range(max(0, a), min(width, b) + 1):
                seen[x] = True
        for x, s_ in enumerate(seen):
            if s_:
                cover[x] += 1
    limit = 0 if len(lines) < 10 else int(len(lines) * 0.1)
    min_gap = max(3.0, h * 0.3)
    bands: list[list[float]] = []
    start = None
    gap_run = 0
    for x in range(width + 1):
        if cover[x] > limit:
            if start is None:
                start = x
            elif gap_run and gap_run < min_gap:
                pass  # a narrow dip inside one column (a space between words)
            elif gap_run:
                bands.append([start + x_lo, x - gap_run + x_lo])
                start = x
            gap_run = 0
        elif start is not None:
            gap_run += 1
    if start is not None:
        end = width - gap_run if gap_run else width
        bands.append([start + x_lo, end + x_lo])
    return bands


def _column_of(bands: list[list[float]], box: list[float]) -> int:
    x1, x2 = box[0], box[0] + box[2]
    best, best_ov = -1, 0.0
    for i, (b1, b2) in enumerate(bands):
        ov = min(x2, b2) - max(x1, b1)
        if ov > best_ov:
            best, best_ov = i, ov
    if best >= 0:
        return best
    cx = (x1 + x2) / 2
    return min(range(len(bands)), key=lambda i: min(abs(cx - bands[i][0]), abs(cx - bands[i][1])))


def _split(line: dict[str, Any], bands: list[list[float]]) -> dict[int, dict[str, Any]]:
    """The line's words, column by column (a cell OCR read across two columns is split by its words)."""
    out: dict[int, dict[str, Any]] = {}
    for w in line["words"]:
        j = _column_of(bands, w["box"])
        if j in out:
            c = out[j]
            c["text"] += " " + w["text"]
            c["box"] = _union([c["box"], w["box"]])
            c["confidence"] = min(c["confidence"], w["confidence"])
        else:
            out[j] = {"text": w["text"], "box": list(w["box"]), "confidence": w["confidence"]}
    for c in out.values():
        c["numeric"] = is_number(c["text"])
    return out


def _fits(line: dict[str, Any], bands: list[list[float]], h: float) -> bool:
    """Does the line's text sit inside the table's columns (and not across the gutters between them)?"""
    if not line["cells"]:
        return False
    inside = 0
    for c in line["cells"]:
        x1, x2 = c["box"][0], c["box"][0] + c["box"][2]
        if any(min(x2, b2) - max(x1, b1) >= 0.6 * (x2 - x1) for b1, b2 in bands):
            inside += 1
    return inside >= max(1, 0.75 * len(line["cells"]))


def _header_name(parts: list[str]) -> tuple[str, str | None]:
    """'Unit Price' + '(AED)' -> ('Unit Price', 'AED'); 'Share %' stays a name with unit %."""
    unit = None
    keep = []
    for p in parts:
        m = UNIT.match(p.strip())
        if m and keep:
            u = m.group("u").upper().replace("IN ", "")
            unit = u
            continue
        m2 = re.search(r"\((?P<u>[A-Z]{3}|%)\)\s*$", p)
        if m2:
            unit = m2.group("u")
            p = p[: m2.start()].strip()
        keep.append(p)
    name = " ".join(k for k in keep if k).strip() or (f"({unit})" if unit else "")
    if unit is None and name.endswith("%"):
        unit = "%"
    return name, unit


def _tables(lines: list[dict[str, Any]], h: float, pitch: float) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i]["used"] or len(lines[i]["cells"]) < 2:
            i += 1
            continue
        # a run of neighbouring lines with two or more cells (single-cell lines may continue a wrapped cell)
        j = i + 1
        while j < n and not lines[j]["used"] and lines[j]["y"] - lines[j - 1]["y"] <= pitch * 2.4 and \
                (len(lines[j]["cells"]) >= 2 or (j + 1 < n and len(lines[j + 1]["cells"]) >= 2 and
                                                 lines[j + 1]["y"] - lines[j]["y"] <= pitch * 2.4)):
            j += 1
        run = lines[i:j]
        made = False
        for seg in _segments(run, pitch):
            t = _table_from_run(seg, h, pitch)
            if t is None:
                continue
            tables.append(t)
            made = True
            for ln in t.pop("_lines"):
                ln["used"] = True
        i = j if made else i + 1
    return tables


def _numeric(ln: dict[str, Any]) -> bool:
    return any(c["numeric"] for c in ln["cells"])


def _segments(run: list[dict[str, Any]], pitch: float) -> list[list[dict[str, Any]]]:
    """Blocks that stand on their own: a text-only line after a line with numbers starts a new block (the header of
    the next table, the labels of the next row of KPI cards) unless it is laid out like the rows above it, and a
    single text line that is not a wrapped cell (a section title such as 'Monthly Sales') ends a block."""
    segs: list[list[dict[str, Any]]] = [[]]
    for k, ln in enumerate(run):
        cur = segs[-1]
        prev = cur[-1] if cur else None
        if prev is not None and not _numeric(ln):
            gap = ln["y"] - prev["y"]
            single = len(ln["cells"]) == 1
            wrapped = gap < pitch * 0.85
            if single and not wrapped:
                segs.append([ln])
                continue
            last_num = next((x for x in reversed(cur) if _numeric(x)), None)
            if last_num is not None and _numeric(prev) and not wrapped and \
                    abs(len(ln["cells"]) - len(last_num["cells"])) >= 1:
                segs.append([ln])
                continue
        cur.append(ln)
    return [s for s in segs if s]


def _table_from_run(run: list[dict[str, Any]], h: float, pitch: float) -> dict[str, Any] | None:
    # header lines: text-only lines at the top, while the body has numbers (or the first line of a text table)
    numeric_lines = [k for k, ln in enumerate(run) if any(c["numeric"] for c in ln["cells"])]
    if numeric_lines:
        first_data = numeric_lines[0]
        head_idx = [k for k in range(first_data) if not any(c["numeric"] for c in run[k]["cells"])]
        head_idx = head_idx[-3:]  # at most three header lines
        if not head_idx:
            return None
        # header lines must be close together and close to the first data line
        def line_h(ln: dict[str, Any]) -> float:
            return median(w["box"][3] for w in ln["words"]) if ln.get("words") else h

        while len(head_idx) > 1 and (run[head_idx[1]]["y"] - run[head_idx[0]]["y"] > pitch * 1.6 or
                                     (len(run[head_idx[0]]["cells"]) == 1 and
                                      run[head_idx[1]]["y"] - run[head_idx[0]]["y"] >= pitch * 0.85) or
                                     # a wrapped header ("Unit Price" / "(AED)") is set tight; a heading above
                                     # the table is larger and has a margin
                                     run[head_idx[1]]["y"] - run[head_idx[0]]["y"] > h * 1.9 or
                                     line_h(run[head_idx[0]]) > line_h(run[head_idx[1]]) * 1.25):
            head_idx = head_idx[1:]  # a title above the table, not a header line
    else:
        head_idx = [0]
        first_data = 1
    body = run[head_idx[-1] + 1:]
    if len(body) < 2:
        return None  # labels with one row of values: KPI cards or a record form (read as labelled values)
    bands = _columns([ln for ln in body if not TOTAL.match(ln["cells"][0]["text"])] or body, h)
    if len(bands) < 2:
        return None
    # stop the body where lines no longer sit in the columns (a second table, a chart, a footer)
    kept = []
    for ln in body:
        if not _fits(ln, bands, h) and kept:
            break
        kept.append(ln)
    body = kept
    if len([ln for ln in body if not TOTAL.match(ln["cells"][0]["text"])]) < 2:
        return None
    # header words into columns, top to bottom
    head_parts: dict[int, list[str]] = {}
    head_boxes: dict[int, list[list[float]]] = {}
    head_conf: dict[int, float] = {}
    span_warnings = []
    for k in head_idx:
        prev_w, prev_j = None, None
        for w in run[k]["words"]:
            x1, x2 = w["box"][0], w["box"][0] + w["box"][2]
            hits = [b for b, (b1, b2) in enumerate(bands) if min(x2, b2) - max(x1, b1) > 0]
            if hits:
                j = _column_of(bands, w["box"])
            elif prev_w is not None and x1 - (prev_w["box"][0] + prev_w["box"][2]) <= h * 0.6:
                j = prev_j  # the next word of the same header text ("Completed" "Orders"), wider than the values
            else:
                j = _column_of(bands, w["box"])
            if len(hits) > 1 and len(w["text"]) > 3:
                span_warnings.append(w["text"])
            head_parts.setdefault(j, [])
            # words of the same header line are joined, lines are separate parts
            if head_parts[j] and head_boxes[j] and abs(_cy(head_boxes[j][-1]) - _cy(w["box"])) < h * 0.5:
                head_parts[j][-1] += " " + w["text"]
            else:
                head_parts[j].append(w["text"])
            head_boxes.setdefault(j, []).append(w["box"])
            head_conf[j] = min(head_conf.get(j, 1.0), w["confidence"])
            prev_w, prev_j = w, j
    headers, units = [], {}
    for b in range(len(bands)):
        name, unit = _header_name(head_parts.get(b, []))
        if not name:
            name = f"Column {b + 1}"
        base, k = name, 2
        while name in headers:
            name, k = f"{base} {k}", k + 1
        headers.append(name)
        if unit:
            units[name] = unit
    # body lines into rows: a line without a value in the first (key) column that sits closer than a row pitch to
    # the previous row continues its text cells (a wrapped cell)
    rows: list[dict[int, dict[str, Any]]] = []
    kinds: list[str | None] = []
    row_lines: list[list[dict[str, Any]]] = []
    for ln in body:
        cells = _split(ln, bands)
        first = cells.get(min(cells)) if cells else None
        m = TOTAL.match(first["text"]) if first else None
        cont = (rows and 0 not in cells and not any(c["numeric"] for c in cells.values()) and kinds[-1] is None and
                ln["y"] - row_lines[-1][-1]["y"] < pitch * 0.85)
        if cont:
            for j, c in cells.items():
                if j in rows[-1]:
                    p = rows[-1][j]
                    p["text"] += " " + c["text"]
                    p["box"] = _union([p["box"], c["box"]])
                    p["confidence"] = min(p["confidence"], c["confidence"])
                    p["multiline"] = True
                else:
                    rows[-1][j] = c
            row_lines[-1].append(ln)
            continue
        rows.append(cells)
        kinds.append(re.sub(r"\s+", " ", m.group("kind").lower()).replace("sub -", "sub").replace("sub-", "sub")
                     if m else None)
        row_lines.append([ln])
    records, totals = [], []
    for cells, kind in zip(rows, kinds):
        rec = {headers[j]: _cell(c) | ({"multiline": True} if c.get("multiline") else {}) for j, c in cells.items()}
        if kind:
            totals.append({"kind": "grand_total" if "grand" in kind else "subtotal" if "sub" in kind else "total",
                           "label": cells[min(cells)]["text"], "values": rec})
        else:
            records.append(rec)
    if not records:
        return None
    lines_used = [*[run[k] for k in head_idx], *[ln for group in row_lines for ln in group]]
    table = {"headers": headers, "units": units, "rows": records, "totals": totals,
             "box": _union([ln["box"] for ln in lines_used]), "header_confidence": head_conf,
             "_lines": lines_used}
    table.update(_validate(table, span_warnings))
    return table


def _decimals(text: str) -> int:
    t = str(text or "").strip()
    m = re.search(r"[.,](\d+)\s*%?\s*\)?$", t)
    if not m:
        return 0
    if t[m.start()] == "," and len(m.group(1)) == 3 and "." not in t:
        return 0  # 10,000: a thousands separator, not decimals
    return len(m.group(1))


def _validate(t: dict[str, Any], span_warnings: list[str]) -> dict[str, Any]:
    headers, recs = t["headers"], t["rows"]
    issues: list[str] = []
    review: list[str] = []
    filled = {h: sum(1 for r in recs if str((r.get(h) or {}).get("value") or "").strip()) for h in headers}
    numeric = {h: sum(1 for r in recs if is_number((r.get(h) or {}).get("value") or "")) for h in headers}
    num_cols = [h for h in headers if filled[h] and numeric[h] >= 0.7 * filled[h]]
    missing = 0
    for r in recs:
        for h in headers:
            if filled[h] >= 0.8 * len(recs) and not str((r.get(h) or {}).get("value") or "").strip():
                missing += 1
    if missing:
        issues.append(f"{missing} cell(s) are empty in columns that are filled elsewhere")
    bad_num = [(h, (r.get(h) or {}).get("value")) for h in num_cols for r in recs
               if (r.get(h) or {}).get("value") and not is_number(r[h]["value"])]
    if bad_num:
        review.append(f"{len(bad_num)} value(s) are not numbers in number columns (e.g. {bad_num[0][0]}: "
                      f"'{bad_num[0][1]}')")
    low = sum(1 for r in recs for c in r.values() if c.get("confidence", 1) < LOW)
    if low:
        review.append(f"{low} value(s) were read with low confidence")
    key = next((h for h in headers if filled[h] == len(recs) and len(recs) >= 2 and
                len({str(r[h]["value"]).strip().lower() for r in recs}) == len(recs)), None)
    checks = []
    for tot in t["totals"]:
        for h, c in tot["values"].items():
            if h not in num_cols or not is_number(c.get("value") or ""):
                continue
            shown = parse_number(c["value"])
            s = sum((parse_number(r[h]["value"]) for r in recs if is_number((r.get(h) or {}).get("value") or "")),
                    Decimal(0))
            places = _decimals(c["value"])
            ok = shown is not None and abs(s.quantize(Decimal(1).scaleb(-places)) - shown) <= Decimal(1).scaleb(-places) / 2
            checks.append({"row": tot["label"], "column": h, "shown": c["value"], "sum_of_rows": format(s, "f"),
                           "ok": ok})
            if not ok:
                review.append(f"{tot['label']} › {h} shows {c['value']} but the rows add up to {format(s, 'f')}")
    if span_warnings:
        review.append("header text spans more than one column: " + ", ".join(sorted(set(span_warnings))[:4]))
    if len(headers) and sum(1 for h in headers if h.startswith("Column ")) > len(headers) / 2:
        issues.append("most columns have no readable header")
    status = "COMPLETE"
    if review:
        status = "NEEDS_REVIEW"
    if issues:
        status = "PARTIAL"
    return {"status": status, "issues": issues + review, "key_column": key, "numeric_columns": num_cols,
            "total_checks": checks, "visible_rows": len(recs)}


# ---- labelled values (KPI cards, forms) ---------------------------------------------------------------------------

def _labelled(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """'Label: value', 'Label 1,234', a row of labels with a row of values under them, and a label with its number
    on the next line."""
    out = []
    for i, r in enumerate(lines):
        if r["used"]:
            continue
        cells = r["cells"]
        took = False
        nxt_line = lines[i + 1] if i + 1 < len(lines) and not lines[i + 1]["used"] else None
        # a row of labels with a row of numbers right under it (KPI cards side by side)
        if nxt_line and len(cells) >= 2 and not any(c["numeric"] for c in cells) and \
                all(c["numeric"] for c in nxt_line["cells"]) and len(nxt_line["cells"]) >= 2 and \
                nxt_line["y"] - r["y"] < r["box"][3] * 3.5:
            for v in nxt_line["cells"]:
                lab = min(cells, key=lambda c: abs(_cx(c["box"]) - _cx(v["box"])))
                if abs(_cx(lab["box"]) - _cx(v["box"])) <= max(lab["box"][2], v["box"][2]):
                    out.append({"label": lab["text"], **_cell(v),
                                "confidence": round(min(lab["confidence"], v["confidence"]), 3),
                                "box": _union([lab["box"], v["box"]])})
            r["used"] = nxt_line["used"] = True
            continue
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
        if len(cells) == 1 and not cells[0]["numeric"] and nxt_line is not None:
            nxt = nxt_line["cells"]
            if len(nxt) == 1 and nxt[0]["numeric"] and abs(_cx(nxt[0]["box"]) - _cx(cells[0]["box"])) < \
                    max(cells[0]["box"][2], nxt[0]["box"][2]):
                out.append({"label": cells[0]["text"], **_cell(nxt[0]),
                            "confidence": round(min(cells[0]["confidence"], nxt[0]["confidence"]), 3),
                            "box": _union([cells[0]["box"], nxt[0]["box"]])})
                r["used"] = nxt_line["used"] = True
    for v in out:
        v["label"] = re.sub(r"\s+", " ", v["label"]).strip()
        if TOTAL.match(v["label"]):
            v["summary"] = True
    return out


# ---- charts -------------------------------------------------------------------------------------------------------

def _all_cells(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for ln in lines for c in ln["cells"]]


def _title_above(lines: list[dict[str, Any]], box: list[float], h: float) -> dict[str, Any] | None:
    best = None
    for ln in lines:
        if ln["box"][1] + ln["box"][3] <= box[1] + 2 and box[1] - (ln["box"][1] + ln["box"][3]) < h * 6 and \
                not any(c["numeric"] for c in ln["cells"]) and len(ln["cells"]) <= 2:
            if best is None or ln["y"] > best["y"]:
                best = ln
    return best


def _color_dist(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b))


def _legend(cells: list[dict[str, Any]], rects: list[dict[str, Any]], h: float) -> list[dict[str, Any]]:
    """Legend entries: a small square swatch with its label (and numbers) on the same line to its right."""
    out = []
    for r in rects:
        x, y, w, hh = r["box"]
        if not (5 <= w <= 40 and 5 <= hh <= 40 and abs(w - hh) <= 0.35 * max(w, hh)):
            continue
        cy = y + hh / 2
        row = sorted([c for c in cells if abs(_cy(c["box"]) - cy) <= max(hh, h) * 0.7 and c["box"][0] > x + w - 2
                      and c["box"][0] - (x + w) < 600], key=lambda c: c["box"][0])
        if not row or row[0]["numeric"] or row[0]["box"][0] - (x + w) > h * 3:
            continue
        label = row[0]
        nums = [c for c in row[1:] if c["numeric"]]
        # stop at the next swatch on the same line
        nxt = [o for o in rects if o is not r and abs((o["box"][1] + o["box"][3] / 2) - cy) < hh and
               o["box"][0] > label["box"][0]]
        if nxt:
            limit = min(o["box"][0] for o in nxt)
            nums = [c for c in nums if c["box"][0] < limit]
        out.append({"color": r["color"], "swatch": r, "label": label, "numbers": nums})
    return out


def _axis_fit(ticks: list[dict[str, Any]]) -> tuple[float, float, float] | None:
    """value = a * y + b from axis tick labels (y = the label's centre); (a, b, worst residual share)."""
    pts = [(_cy(t["box"]), float(parse_number(t["text"]))) for t in ticks if parse_number(t["text"]) is not None]
    pts = sorted(set(pts))
    if len(pts) < 3:
        return None
    n = len(pts)
    my = sum(p[0] for p in pts) / n
    mv = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - my) ** 2 for p in pts)
    if sxx == 0:
        return None
    a = sum((p[0] - my) * (p[1] - mv) for p in pts) / sxx
    b = mv - a * my
    span = max(p[1] for p in pts) - min(p[1] for p in pts) or 1
    worst = max(abs(a * y + b - v) for y, v in pts) / span
    return (a, b, worst) if a < 0 and worst < 0.02 else None


def _bar_charts(lines: list[dict[str, Any]], g: dict[str, Any], h: float) -> list[dict[str, Any]]:
    cells = [c for ln in lines if not ln["used"] for c in ln["cells"]]
    rects = [r for r in g.get("rects") or [] if r["box"][2] >= 6 and r["box"][3] >= 6]
    swatches = [r for r in rects if r["box"][2] <= 40 and r["box"][3] <= 40]
    bars = [r for r in rects if r not in swatches or r["box"][3] > 40 or r["box"][2] > 40]
    charts = []
    used_cells: set[int] = set()
    # vertical bars: bottoms on one baseline
    by_base: dict[int, list[dict[str, Any]]] = {}
    for r in bars:
        base = int(round((r["box"][1] + r["box"][3]) / 4.0))
        key = next((k for k in by_base if abs(k - base) <= 1), base)
        by_base.setdefault(key, []).append(r)
    groups = [sorted(v, key=lambda r: r["box"][0]) for v in by_base.values() if len(v) >= 2]
    for grp in groups:
        widths = [r["box"][2] for r in grp]
        if max(widths) > 3 * min(widths) or max(r["box"][3] for r in grp) < 2 * h:
            continue
        base_y = max(r["box"][1] + r["box"][3] for r in grp)
        x_lo, x_hi = min(r["box"][0] for r in grp), max(r["box"][0] + r["box"][2] for r in grp)
        legend = _legend(cells, swatches, h)
        colors = {tuple(r["color"]) for r in grp}
        multi = len(colors) > 1 and legend and all(any(_color_dist(list(c), e["color"]) < 60 for e in legend)
                                                   for c in colors)
        # category labels under the bars
        under = [c for c in cells if 0 <= c["box"][1] - base_y <= h * 3 and not c["numeric"]
                 and x_lo - 40 <= _cx(c["box"]) <= x_hi + 40]
        ticks = [c for c in cells if c["numeric"] and c["box"][0] + c["box"][2] <= x_lo + 2 and
                 base_y + h >= _cy(c["box"]) >= min(r["box"][1] for r in grp) - h * 3]
        fit = _axis_fit(ticks)
        pts: dict[str, dict[str, Any]] = {}
        series_pts: dict[str, dict[str, dict[str, Any]]] = {}
        approx = False
        cats: list[str] = []
        for r in grp:
            x1, x2 = r["box"][0], r["box"][0] + r["box"][2]
            if multi:
                # the category label sits under the group of bars; use the nearest one
                cat_cell = min(under, key=lambda c: abs(_cx(c["box"]) - (x1 + x2) / 2)) if under else None
            else:
                cand = [c for c in under if x1 - r["box"][2] * 0.6 <= _cx(c["box"]) <= x2 + r["box"][2] * 0.6]
                cat_cell = min(cand, key=lambda c: abs(_cx(c["box"]) - (x1 + x2) / 2)) if cand else None
            if cat_cell is None:
                continue
            cat = cat_cell["text"]
            used_cells.add(id(cat_cell))
            top = r["box"][1]
            lab = [c for c in cells if c["numeric"] and 0 <= top - (c["box"][1] + c["box"][3]) <= h * 2.2 and
                   x1 - 4 <= _cx(c["box"]) <= x2 + 4]
            if lab:
                v = min(lab, key=lambda c: top - c["box"][1])
                used_cells.add(id(v))
                cell = _cell(v)
                if fit:  # the label must agree with the bar's height
                    drawn = fit[0] * top + fit[1] - (fit[0] * base_y + fit[1])
                    shown = float(parse_number(v["text"]) or 0)
                    if abs(drawn - shown) > max(abs(shown) * 0.06, abs(fit[0]) * 4):
                        cell["note"] = f"the bar is drawn at about {drawn:,.0f}, not {v['text']}"
                        cell["confidence"] = min(cell["confidence"], 0.6)
            elif fit:
                est = fit[0] * top + fit[1] - (fit[0] * base_y + fit[1])
                places = max((_decimals(t["text"]) for t in ticks), default=0)
                cell = {"value": f"{est:,.{places}f}", "confidence": 0.5, "box": r["box"], "method": "bar height",
                        "approximate": True, "note": "estimated from the axis scale (no value label)"}
                approx = True
            else:
                cell = {"value": None, "confidence": 0.0, "box": r["box"], "method": "bar",
                        "note": "no value label and no readable axis"}
            if multi:
                sname = next((e["label"]["text"] for e in legend if _color_dist(e["color"], r["color"]) < 60), "Series")
                series_pts.setdefault(sname, {})[cat] = cell
            else:
                pts[cat] = cell
            if cat not in cats:
                cats.append(cat)
        if len(cats) < 2:
            continue
        for t in ticks:
            used_cells.add(id(t))
        box = _union([r["box"] for r in grp] + [c["box"] for c in under])
        title = _title_above([ln for ln in lines if not ln["used"]], box, h)
        if title:
            title["used"] = True
        if multi:
            for sname, sp in series_pts.items():
                charts.append(_chart(f"{title['cells'][0]['text'] if title else 'Chart'} — {sname}", "bar", cats, sp,
                                     box, series=sname, approximate=approx))
        else:
            charts.append(_chart(title["cells"][0]["text"] if title else "Chart", "bar", cats, pts, box,
                                 approximate=approx, axis=bool(fit)))
    # horizontal bars: left edges on one line, label to the left, value to the right
    by_left: dict[int, list[dict[str, Any]]] = {}
    for r in bars:
        left = int(round(r["box"][0] / 4.0))
        key = next((k for k in by_left if abs(k - left) <= 1), left)
        by_left.setdefault(key, []).append(r)
    for grp in [sorted(v, key=lambda r: r["box"][1]) for v in by_left.values() if len(v) >= 2]:
        heights = [r["box"][3] for r in grp]
        if max(heights) > 3 * min(heights) or any(r in g2 for g2 in groups for r in grp):
            continue
        pts, cats = {}, []
        for r in grp:
            cy = r["box"][1] + r["box"][3] / 2
            left_c = [c for c in cells if abs(_cy(c["box"]) - cy) <= r["box"][3] * 0.6 and
                      c["box"][0] + c["box"][2] <= r["box"][0] + 2 and not c["numeric"]]
            right_c = [c for c in cells if abs(_cy(c["box"]) - cy) <= r["box"][3] * 0.6 and
                       c["box"][0] >= r["box"][0] + r["box"][2] - 4 and c["numeric"]]
            if not left_c:
                continue
            cat_cell = max(left_c, key=lambda c: c["box"][0])
            used_cells.add(id(cat_cell))
            if right_c:
                v = min(right_c, key=lambda c: c["box"][0])
                used_cells.add(id(v))
                pts[cat_cell["text"]] = _cell(v)
            else:
                pts[cat_cell["text"]] = {"value": None, "confidence": 0.0, "box": r["box"], "method": "bar",
                                         "note": "no value label"}
            cats.append(cat_cell["text"])
        if len(cats) >= 2:
            box = _union([r["box"] for r in grp])
            title = _title_above([ln for ln in lines if not ln["used"]], box, h)
            if title:
                title["used"] = True
            charts.append(_chart(title["cells"][0]["text"] if title else "Chart", "horizontal bar", cats, pts, box))
    _mark(lines, used_cells)
    return charts


def _pie_charts(lines: list[dict[str, Any]], g: dict[str, Any], h: float) -> list[dict[str, Any]]:
    cells = [c for ln in lines if not ln["used"] for c in ln["cells"]]
    swatches = [r for r in g.get("rects") or [] if r["box"][2] <= 40 and r["box"][3] <= 40]
    charts = []
    used_cells: set[int] = set()
    for pie in g.get("pies") or []:
        legend = _legend(cells, swatches, h)
        pts, pcts, cats = {}, {}, []
        approx = False
        for e in legend:
            used_cells.add(id(e["label"]))
            cat = e["label"]["text"]
            amount = next((c for c in e["numbers"] if not PCT.match(c["text"].strip())), None)
            pct = next((c for c in e["numbers"] if PCT.match(c["text"].strip())), None)
            for c in e["numbers"]:
                used_cells.add(id(c))
            sl = min(pie["slices"], key=lambda s: _color_dist(s["color"], e["color"]))
            if _color_dist(sl["color"], e["color"]) > 60:
                sl = None
            if amount:
                pts[cat] = _cell(amount)
            if pct:
                pcts[cat] = _cell(pct)
            elif sl:
                pcts[cat] = {"value": f"{sl['share'] * 100:.1f}%", "confidence": 0.5, "method": "slice angle",
                             "approximate": True, "box": sl["box"], "note": "estimated from the slice size"}
                approx = True
            if sl:
                (pcts.get(cat) or {}).setdefault("drawn_share", round(sl["share"] * 100, 2))
            cats.append(cat)
        # labels printed around the pie: "Dubai 40.2%" / "Dubai: AED 9,450 (40.2%)"
        if not cats:
            x, y, w, hh = pie["box"]
            for c in cells:
                if not (x - 300 <= _cx(c["box"]) <= x + w + 300 and y - 80 <= _cy(c["box"]) <= y + hh + 80):
                    continue
                m = re.match(r"^(?P<cat>[A-Za-z][^:\d%]{0,40}?)[:\s]+(?P<rest>.*\d.*)$", c["text"])
                if not m:
                    continue
                rest = m.group("rest")
                pm = re.search(r"\(?(\d[\d.,]*\s?%)\)?", rest)
                am = re.search(r"((?:[A-Z]{3}\s?)?\d[\d,]*(?:\.\d+)?)(?!\s?%)", rest)
                cat = m.group("cat").strip()
                cats.append(cat)
                used_cells.add(id(c))
                if pm:
                    pcts[cat] = {**_cell(c), "value": pm.group(1).replace(" ", "")}
                if am and (not pm or am.start() != pm.start()):
                    pts[cat] = {**_cell(c), "value": am.group(1)}
        if len(cats) < 2:
            continue
        box = _union([pie["box"]] + [e["label"]["box"] for e in legend])
        title = _title_above([ln for ln in lines if not ln["used"]], box, h)
        if title:
            title["used"] = True
        centre_total = None
        if pie.get("donut"):
            cx, cy, r = pie["center"][0], pie["center"][1], pie["radius"]
            inner = [c for c in cells if abs(_cx(c["box"]) - cx) < r * 0.5 and abs(_cy(c["box"]) - cy) < r * 0.5
                     and c["numeric"]]
            if inner:
                centre_total = _cell(inner[0])
                used_cells.add(id(inner[0]))
        ch = _chart(title["cells"][0]["text"] if title else "Chart", "donut" if pie.get("donut") else "pie", cats,
                    pts if pts else pcts, box, approximate=approx and not pts)
        ch["percentages"] = pcts
        ch["value_kind"] = "amount" if pts else "percent"
        if centre_total:
            ch["total"] = centre_total
        ch.update(_check_pie(ch))
        charts.append(ch)
    _mark(lines, used_cells)
    return charts


def _check_pie(ch: dict[str, Any]) -> dict[str, Any]:
    """Pie checks: percentages add up to 100 (within rounding); each percentage = amount / total of amounts."""
    issues = []
    pcts = {k: parse_number(v.get("value") or "") for k, v in ch.get("percentages", {}).items()}
    pcts = {k: v for k, v in pcts.items() if v is not None}
    if pcts and len(pcts) == len(ch["categories"]):
        s = sum(pcts.values())
        places = max(_decimals(v.get("value") or "") for v in ch["percentages"].values())
        allowed = Decimal(len(pcts)) * Decimal(1).scaleb(-places) / 2 + Decimal("0.0001")
        if abs(s - 100) > allowed:
            issues.append(f"the percentages add up to {s}, not 100")
    amounts = {k: parse_number(v.get("value") or "") for k, v in ch["points"].items()} \
        if ch.get("value_kind") == "amount" else {}
    amounts = {k: v for k, v in amounts.items() if v is not None}
    checks = []
    if amounts and pcts:
        tot = sum(amounts.values())
        for k, a in amounts.items():
            if k in pcts and tot:
                exp = a / tot * 100
                places = _decimals(ch["percentages"][k].get("value") or "")
                ok = abs(exp.quantize(Decimal(1).scaleb(-places)) - pcts[k]) <= Decimal(1).scaleb(-places) / 2
                checks.append({"category": k, "amount": format(a, "f"), "shown": ch["percentages"][k]["value"],
                               "expected": format(exp.quantize(Decimal("0.01")), "f"), "ok": ok})
                if not ok:
                    issues.append(f"{k}: {ch['percentages'][k]['value']} shown, {a} / {tot} = "
                                  f"{exp.quantize(Decimal('0.01'))}%")
    out: dict[str, Any] = {"pie_checks": checks}
    if issues:
        out["issues"] = (ch.get("issues") or []) + issues
        if ch["status"] == "COMPLETE":
            out["status"] = "NEEDS_REVIEW"
    return out


def _chart(title: str, ctype: str, cats: list[str], pts: dict[str, Any], box: list[float], *,
           series: str | None = None, approximate: bool = False, axis: bool = False) -> dict[str, Any]:
    missing = [c for c in cats if c not in pts or pts[c].get("value") in (None, "")]
    low = [c for c in cats if c in pts and pts[c].get("confidence", 1) < LOW and not pts[c].get("approximate")]
    issues = []
    if missing:
        issues.append(f"no readable value for {', '.join(missing[:5])}")
    if approximate:
        issues.append("values estimated from the drawing (no data labels): approximate")
    if low:
        issues.append(f"{len(low)} value(s) read with low confidence or not matching the bar height")
    status = "PARTIAL" if missing and len(missing) < len(cats) else "FAILED" if missing else \
        "NEEDS_REVIEW" if (approximate or low) else "COMPLETE"
    unit = None
    m = re.search(r"\((?P<u>[A-Z]{3}|%)\)|\b(?P<u2>AED|USD|EUR|GBP|SAR|INR)\b", title)
    if m:
        unit = m.group("u") or m.group("u2")
    return {"title": title, "type": ctype, "categories": cats, "points": pts, "series": series,
            "extracted": not missing and not approximate, "approximate": approximate, "unit": unit,
            "axis": axis, "status": status, "issues": issues,
            "note": "; ".join(issues) if issues else None, "box": [round(v, 1) for v in box]}


def _mark(lines: list[dict[str, Any]], used: set[int]) -> None:
    for ln in lines:
        if ln["used"]:
            continue
        cs = ln["cells"]
        if cs and all(id(c) in used for c in cs):
            ln["used"] = True
        elif cs and any(id(c) in used for c in cs):
            ln["cells"] = [c for c in cs if id(c) not in used]


def _text_charts(lines: list[dict[str, Any]], pitch: float) -> list[dict[str, Any]]:
    """Without shape analysis (Windows OCR / Tesseract only): column charts whose number labels sit above their
    category labels, and legends 'Category  amount  pct%' whose percentages add up to 100."""
    charts = []
    for i, r in enumerate(lines):
        cats = r["cells"]
        if r["used"] or len(cats) < 2 or any(c["numeric"] for c in cats) or i == 0:
            continue
        if any(len(c["text"]) > 30 for c in cats):
            continue
        pts: dict[str, dict[str, Any]] = {}
        used_rows = []
        title = None
        for k in range(i - 1, -1, -1):
            up = lines[k]
            if up["used"]:
                break
            if any(not c["numeric"] for c in up["cells"]):
                if len(up["cells"]) == 1:
                    title = up
                break
            hit = False
            for c in up["cells"]:
                cat = min(cats, key=lambda x: abs(_cx(x["box"]) - _cx(c["box"])))
                if abs(_cx(cat["box"]) - _cx(c["box"])) <= max(cat["box"][2], c["box"][2]) and cat["text"] not in pts:
                    pts[cat["text"]] = _cell(c)
                    hit = True
            if hit:
                used_rows.append(up)
            if r["y"] - up["y"] > pitch * 25:
                break
        if len(pts) >= max(2, (len(cats) + 1) // 2):
            box = _union([r["box"]] + [u["box"] for u in used_rows] + ([title["box"]] if title else []))
            charts.append(_chart(title["cells"][0]["text"] if title else "Chart", "bar", [c["text"] for c in cats],
                                 pts, box))
            r["used"] = True
            for u in used_rows:
                u["used"] = True
            if title:
                title["used"] = True
    # legends: consecutive lines of [label, amount?, pct%]
    run: list[dict[str, Any]] = []

    def flush() -> None:
        if len(run) >= 2:
            pcts = [parse_number(ln["cells"][-1]["text"]) for ln in run]
            if all(p is not None for p in pcts) and abs(sum(pcts) - 100) <= Decimal("0.5") * len(run) / 2 + 1:
                cats = [ln["cells"][0]["text"] for ln in run]
                pts = {ln["cells"][0]["text"]: _cell(ln["cells"][1]) for ln in run if len(ln["cells"]) >= 3}
                pc = {ln["cells"][0]["text"]: _cell(ln["cells"][-1]) for ln in run}
                box = _union([ln["box"] for ln in run])
                title = _title_above([x for x in lines if not x["used"]], box, run[0]["box"][3])
                ch = _chart(title["cells"][0]["text"] if title else "Distribution", "pie (legend)", cats,
                            pts if len(pts) == len(cats) else pc, box)
                ch["percentages"] = pc
                ch["value_kind"] = "amount" if len(pts) == len(cats) else "percent"
                ch.update(_check_pie(ch))
                charts.append(ch)
                for ln in run:
                    ln["used"] = True
                if title:
                    title["used"] = True
        run.clear()

    for ln in lines:
        cs = ln["cells"]
        if not ln["used"] and 2 <= len(cs) <= 3 and not cs[0]["numeric"] and PCT.match(cs[-1]["text"]) and \
                all(c["numeric"] for c in cs[1:]):
            run.append(ln)
        else:
            flush()
    flush()
    return charts


# ---- the whole screen ---------------------------------------------------------------------------------------------

def read_screen(ocr_result: dict[str, Any], graphics: dict[str, Any] | None = None) -> dict[str, Any]:
    """{tables, values, charts, texts, low_confidence, status, issues} from an OCR result (``ocr.read``) and, when
    available, the chart shapes of the same screenshot (``ocr.graphics``)."""
    words = [w for ln in ocr_result.get("lines") or [] for w in ln.get("words") or []]
    words, h = _clean_words(words)
    if not words:
        return {"tables": [], "values": [], "charts": [], "texts": [], "low_confidence": 0, "status": "FAILED",
                "issues": ["no text was found on the screenshot"]}
    lines = lines_of(words, h)
    gaps = [b["y"] - a["y"] for a, b in zip(lines, lines[1:]) if b["y"] - a["y"] > h * 0.6]
    pitch = median(gaps) if gaps else h * 1.8
    g = graphics or ocr_result.get("graphics") or {}
    if g.get("error"):
        g = {}
    if g:
        words = _off_shapes(words, g, h)
        lines = lines_of(words, h)
    charts = []
    if g:
        charts += _pie_charts(lines, g, h)
        charts += _bar_charts(lines, g, h)
    tables = _tables(lines, h, pitch)
    if not g:
        charts += _text_charts(lines, pitch)
    values = _labelled(lines)
    texts = [" ".join(c["text"] for c in r["cells"]) for r in lines if not r["used"] and r["cells"]]
    low = sum(1 for t in tables for r in t["rows"] for c in r.values() if c["confidence"] < LOW)
    low += sum(1 for v in values if v["confidence"] < LOW)
    low += sum(1 for ch in charts for p in ch["points"].values() if (p.get("confidence") or 0) < LOW)
    for t in tables:
        t.pop("header_confidence", None)
    parts = [t["status"] for t in tables] + [c["status"] for c in charts]
    if values:
        parts.append("NEEDS_REVIEW" if any(v["confidence"] < LOW for v in values) else "COMPLETE")
    status = _worst(parts) if parts else "FAILED"
    issues = [f"table {i + 1}: {x}" for i, t in enumerate(tables) for x in t.get("issues") or []]
    issues += [f"chart '{c['title']}': {x}" for c in charts for x in c.get("issues") or []]
    if not parts:
        issues.append("text was found but no table, labelled value or chart could be recognised")
    return {"tables": tables, "values": values, "charts": charts, "texts": texts[:60], "low_confidence": low,
            "status": status, "issues": issues, "graphics": bool(g)}
