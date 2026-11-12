"""Reports of the Data Comparison Agent: HTML (printed to PDF), Excel (.xlsx, written without extra libraries) and
an evidence package (zip with a SHA-256 manifest).

Every value shown is escaped; Excel cells are written as inline strings or numbers, never as formulas, so a value
like ``=HYPERLINK(...)`` taken from an application stays text.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
from typing import Any
from xml.sax.saxutils import escape

RESULT_COLORS = {"PASS": "#1f9d55", "PASS_EQUIVALENT": "#2b8a3e", "FAIL": "#d64545", "REVIEW": "#d9822b",
                 "INCOMPLETE": "#6b7280", "CONFIG_MISMATCH": "#7c3aed"}


def _e(v: Any) -> str:
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    return html.escape("" if v is None else str(v))


def _badge(result: str | None) -> str:
    c = RESULT_COLORS.get(result or "", "#6b7280")
    return f'<span class="badge" style="background:{c}">{_e(result)}</span>'


def html_report(project: dict[str, Any], execution: dict[str, Any], *, defects: list[dict[str, Any]] | None = None,
                names: dict[str, str] | None = None, brand: str = "SkyGuard QA") -> str:
    names = names or {"source": "Application A", "target": "Application B"}
    d = execution.get("detail") or {}
    s = d.get("summary") or {}
    agg = d.get("aggregates") or {}
    rows_summary = "".join(f"<tr><td>{_e(k.replace('_', ' ').capitalize())}</td><td>{_e(v)}</td></tr>"
                           for k, v in s.items())
    cov = d.get("coverage") or {}
    cov_rows = "".join(
        f"<tr><td>{_e(names.get(side, side))}</td><td>{_e((c or {}).get('records'))}</td>"
        f"<td>{_e((c or {}).get('expected_total'))}</td><td>{_e((c or {}).get('pages_visited'))}</td>"
        f"<td>{'yes' if (c or {}).get('complete', True) else 'NO'}</td><td>{_e((c or {}).get('method'))}</td></tr>"
        for side, c in cov.items())
    disc_rows = "".join(
        f"<tr><td>{_e(x.get('id'))}</td><td>{_e(x.get('category'))}</td><td>{_e(x.get('record_key'))}</td>"
        f"<td>{_e(x.get('source_field'))}</td><td>{_e(x.get('source_value', x.get('expected')))}</td>"
        f"<td>{_e(x.get('target_value', x.get('displayed')))}</td><td>{_e(x.get('difference'))}</td>"
        f"<td>{_e(x.get('note') or x.get('how'))}</td></tr>" for x in (d.get("discrepancies") or [])[:3000])
    agg_rows = "".join(
        f"<tr><td>{_e(r.get('metric'))}</td><td>{_e(r.get('expected'))}</td><td>{_e(r.get('displayed'))}</td>"
        f"<td>{_e(r.get('difference'))}</td><td>{_badge(r.get('result'))}</td><td>{_e(r.get('reason'))}</td></tr>"
        for r in agg.get("rows") or [])
    eq_rows = "".join(
        f"<tr><td>{_e(x.get('record_key'))}</td><td>{_e(x.get('field'))}</td><td>{_e(x.get('source'))}</td>"
        f"<td>{_e(x.get('target'))}</td><td>{_e('; '.join((x.get('steps') or {}).get('source', []) + (x.get('steps') or {}).get('target', [])))}</td></tr>"
        for x in (d.get("equivalences") or [])[:300])
    defect_rows = "".join(
        f"<tr><td>{_e(x.get('id'))}</td><td>{_e(x.get('title'))}</td><td>{_e(x.get('status'))}</td>"
        f"<td>{_e(x.get('tfs_id'))}</td></tr>" for x in defects or [])
    nd = d.get("no_defect") or {}
    filters = d.get("filters") or {}
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Data comparison report</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;color:#1f2937;margin:28px;font-size:12px}}h1{{font-size:20px;margin:0}}
h2{{font-size:15px;margin:22px 0 8px;border-bottom:1px solid #e5e7eb;padding-bottom:4px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #e5e7eb;padding:4px 6px;text-align:left;vertical-align:top;word-break:break-word}}th{{background:#f3f4f6}}
.badge{{color:#fff;border-radius:4px;padding:2px 8px;font-weight:600}}.muted{{color:#6b7280}}</style></head><body>
<h1>{_e(brand)}: Application data comparison</h1>
<p class="muted">Project {_e(project.get('name'))} · Execution {_e(execution.get('id'))} · {_e(execution.get('started_at'))}
 · by {_e(execution.get('created_by'))}</p>
<p>Result: {_badge(execution.get('result'))} &nbsp; {_e(names['source'])} vs {_e(names['target'])} · mode {_e(execution.get('mode'))}</p>
<p class="muted">Results are decided by deterministic comparison rules. AI text, where shown, only explains them.</p>
<h2>Summary</h2><table>{rows_summary}</table>
<h2>Coverage</h2><table><tr><th>Application</th><th>Records read</th><th>Total shown by the application</th><th>Pages</th><th>Complete</th><th>Method</th></tr>{cov_rows}</table>
<h2>Filters</h2><p>{'Equivalent' if filters.get('equivalent', True) else 'NOT equivalent: ' + _e('; '.join(filters.get('issues') or []))}</p>
{f'<h2>Source-to-aggregate validation</h2><table><tr><th>Metric</th><th>Source calculated</th><th>Target displayed</th><th>Difference</th><th>Result</th><th>Reason</th></tr>{agg_rows}</table>' if agg_rows else ''}
<h2>Discrepancies ({len(d.get('discrepancies') or [])})</h2>
{'<table><tr><th>Id</th><th>Category</th><th>Record</th><th>Field</th><th>' + _e(names['source']) + '</th><th>' + _e(names['target']) + '</th><th>Difference</th><th>Note</th></tr>' + disc_rows + '</table>' if disc_rows else '<p>None.</p>'}
{f'<h2>Equal after normalization (examples)</h2><table><tr><th>Record</th><th>Field</th><th>{_e(names["source"])}</th><th>{_e(names["target"])}</th><th>Why equal</th></tr>{eq_rows}</table>' if eq_rows else ''}
{f'<h2>No-defect analysis</h2><p>{_e(nd.get("summary"))}</p>' + (f'<p>{_e(nd.get("ai_summary"))}</p>' if nd.get('ai_summary') else '') + ''.join(f'<p class="muted">Not covered: {_e(r)}</p>' for r in nd.get('residual_risks') or []) if nd else ''}
{f'<h2>Defects</h2><table><tr><th>Id</th><th>Title</th><th>Status</th><th>TFS</th></tr>{defect_rows}</table>' if defect_rows else ''}
</body></html>"""


# ---- xlsx -----------------------------------------------------------------------------------------------------------

def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell(ref: str, v: Any, bold: bool = False) -> str:
    style = ' s="1"' if bold else ""
    if isinstance(v, bool):
        v = "true" if v else "false"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"{style}><v>{v}</v></c>'
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    text = _ILLEGAL.sub("", "" if v is None else str(v))[:32000]
    return f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(text)}</t></is></c>'


def xlsx(sheets: list[tuple[str, list[str], list[list[Any]]]]) -> bytes:
    """[(sheet name, headers, rows)] -> .xlsx bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        names = []
        for i, (name, _h, _r) in enumerate(sheets):
            clean = re.sub(r"[\[\]:*?/\\]", " ", name)[:31] or f"Sheet{i + 1}"
            names.append(clean)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets))) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf fontId="0"/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')
        for i, (_n, headers, rows) in enumerate(sheets):
            parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
            parts.append('<row r="1">' + "".join(_cell(f"{_col(j)}1", h, True) for j, h in enumerate(headers)) + "</row>")
            for r, row in enumerate(rows[:1_000_000], start=2):
                parts.append(f'<row r="{r}">' + "".join(_cell(f"{_col(j)}{r}", v) for j, v in enumerate(row)) + "</row>")
            parts.append("</sheetData></worksheet>")
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", "".join(parts))
    return buf.getvalue()


def excel_report(execution: dict[str, Any], names: dict[str, str]) -> bytes:
    d = execution.get("detail") or {}
    s = d.get("summary") or {}
    discs = d.get("discrepancies") or []
    agg = (d.get("aggregates") or {}).get("rows") or []
    sheets = [
        ("Summary", ["Item", "Value"], [["Execution", execution.get("id")], ["Result", execution.get("result")],
                                        ["Source", names.get("source")], ["Target", names.get("target")],
                                        ["Started", execution.get("started_at")]] + [[k, v] for k, v in s.items()]),
        ("Discrepancies", ["Id", "Category", "Record", "Source field", "Target field", "Source value", "Target value",
                           "Source normalized", "Target normalized", "Difference", "Source page", "Target page",
                           "Confidence", "Note"],
         [[x.get("id"), x.get("category"), x.get("record_key"), x.get("source_field"), x.get("target_field"),
           x.get("source_value"), x.get("target_value"), x.get("source_normalized"), x.get("target_normalized"),
           x.get("difference"), x.get("source_page"), x.get("target_page"), x.get("confidence"),
           x.get("note") or x.get("how")] for x in discs]),
        ("Aggregates", ["Metric", "Source calculated", "Target displayed", "Difference", "Result", "Reason",
                        "Records used"],
         [[r.get("metric"), r.get("expected"), r.get("displayed"), r.get("difference"), r.get("result"),
           r.get("reason"), r.get("records_used")] for r in agg]),
        ("Equivalences", ["Record", "Field", "Source", "Target", "How", "Normalization"],
         [[x.get("record_key"), x.get("field"), x.get("source"), x.get("target"), x.get("how"),
           "; ".join((x.get("steps") or {}).get("source", []) + (x.get("steps") or {}).get("target", []))]
          for x in d.get("equivalences") or []]),
        ("Coverage", ["Application", "Records", "Expected total", "Pages", "Complete", "Reason"],
         [[names.get(k, k), (c or {}).get("records"), (c or {}).get("expected_total"), (c or {}).get("pages_visited"),
           (c or {}).get("complete"), (c or {}).get("reason")] for k, c in (d.get("coverage") or {}).items()]),
    ]
    return xlsx(sheets)


def evidence_package(report_html: str, execution: dict[str, Any], files: list[tuple[str, bytes]],
                     audit: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        entries = [("report.html", report_html.encode("utf-8")),
                   ("results.json", json.dumps(execution, ensure_ascii=False, default=str, indent=1).encode("utf-8")),
                   ("audit.json", json.dumps(audit, ensure_ascii=False, default=str, indent=1).encode("utf-8"))]
        entries += [(f"evidence/{n}", b) for n, b in files]
        for name, data in entries:
            z.writestr(name, data)
            manifest.append({"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=1))
    return buf.getvalue()
