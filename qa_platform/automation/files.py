"""23.3.17: files for upload / download steps and data-driven runs.

* Excel (.xlsx / .xlsm) and CSV tables: read without extra packages (an .xlsx file is a zip of XML parts).
* The text of a downloaded file for checks: PDF (pypdf, shipped in qa_platform/vendor), Excel, CSV, Word (.docx),
  text, JSON, XML and HTML.
"""

from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..errors import QAError

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAX_TABLE_ROWS = 1000
MAX_COLUMNS = 100
_CELL = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")


# ---- Excel ------------------------------------------------------------------------------------------------


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col_letters(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _date_styles(zf: zipfile.ZipFile) -> set[int]:
    """Indexes of cell styles that show a number as a date (so 46329 reads as 03/11/2026)."""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = {}
    for nf in root.findall("m:numFmts/m:numFmt", NS):
        custom[int(nf.get("numFmtId", "0"))] = nf.get("formatCode", "")
    builtin_dates = set(range(14, 23)) | {45, 46, 47}
    out = set()
    for i, xf in enumerate(root.findall("m:cellXfs/m:xf", NS)):
        fid = int(xf.get("numFmtId", "0"))
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", custom.get(fid, "")).lower()
        if fid in builtin_dates or (code and re.search(r"[dy]|mmm|h:mm", code)):
            out.add(i)
    return out


def _excel_date(serial: float) -> str:
    when = datetime(1899, 12, 30) + timedelta(days=serial)
    return when.strftime("%d/%m/%Y %H:%M") if when.hour or when.minute else when.strftime("%d/%m/%Y")


def read_xlsx(content: bytes) -> dict[str, list[list[str]]]:
    """Every sheet as rows of cell text, in workbook order."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise QAError("FILE_UNREADABLE", "This is not an Excel workbook (.xlsx). Save it as .xlsx or .csv.",
                      status=422) from exc
    with zf:
        try:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
        except KeyError as exc:
            raise QAError("FILE_UNREADABLE", "This is not an Excel workbook (.xlsx).", status=422) from exc
        rels = {}
        try:
            for r in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")).findall("rel:Relationship", NS):
                target = r.get("Target", "")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                rels[r.get("Id")] = target
        except KeyError:
            pass
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        dates = _date_styles(zf)
        sheets: dict[str, list[list[str]]] = {}
        for n, sh in enumerate(wb.findall("m:sheets/m:sheet", NS), start=1):
            name = sh.get("name") or f"Sheet{n}"
            part = rels.get(sh.get(f"{{{NS['r']}}}id") or "", f"xl/worksheets/sheet{n}.xml")
            try:
                root = ET.fromstring(zf.read(part))
            except KeyError:
                continue
            rows: list[list[str]] = []
            for row in root.iter(f"{{{NS['m']}}}row"):
                r_index = int(row.get("r", len(rows) + 1)) - 1
                if r_index >= MAX_TABLE_ROWS * 5:
                    break
                while len(rows) < r_index:
                    rows.append([])
                cells: list[str] = []
                for c in row.findall("m:c", NS):
                    ref = _CELL.match(c.get("r", ""))
                    ci = _col_index(ref.group(1)) if ref else len(cells)
                    if ci >= MAX_COLUMNS:
                        continue
                    t = c.get("t")
                    v = c.find("m:v", NS)
                    if t == "s" and v is not None:
                        text = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                    elif t == "inlineStr":
                        text = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
                    elif t == "b":
                        text = "TRUE" if (v is not None and v.text == "1") else "FALSE"
                    else:
                        text = v.text if v is not None and v.text is not None else ""
                        if text and t in (None, "n"):
                            try:
                                num = float(text)
                                if int(c.get("s", "0")) in dates:
                                    text = _excel_date(num)
                                elif num.is_integer() and "e" not in text.lower():
                                    text = str(int(num))
                            except ValueError:
                                pass
                    while len(cells) < ci:
                        cells.append("")
                    cells.append(text)
                rows.append(cells)
            sheets[name] = rows
        if not sheets:
            raise QAError("FILE_UNREADABLE", "The workbook has no sheets.", status=422)
        return sheets


def excel_cell(content: bytes, ref: str, sheet: str | None = None) -> str:
    m = _CELL.match(ref.strip().upper())
    if not m:
        raise QAError("STEP_VALUE_INVALID", f"'{ref}' is not a cell such as B2.", status=422)
    sheets = read_xlsx(content)
    name = sheet or next(iter(sheets))
    match = next((k for k in sheets if k.lower() == name.lower()), None)
    if match is None:
        raise QAError("STEP_VALUE_INVALID", f"The downloaded workbook has no sheet '{name}' "
                                            f"(it has {', '.join(sheets)}).", status=422)
    rows = sheets[match]
    r, c = int(m.group(2)) - 1, _col_index(m.group(1))
    return rows[r][c] if r < len(rows) and c < len(rows[r]) else ""


# ---- tables for data-driven runs ------------------------------------------------------------------------


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", "replace")


def read_table(file_name: str, content: bytes, sheet: str | None = None) -> dict[str, Any]:
    """The first row is the column names; every following non-empty row is one data row."""
    ext = Path(file_name).suffix.lower()
    sheets: list[str] = []
    if ext in (".xlsx", ".xlsm"):
        book = read_xlsx(content)
        sheets = list(book)
        chosen = next((k for k in book if sheet and k.lower() == sheet.lower()), None) or sheets[0]
        rows = book[chosen]
    elif ext in (".csv", ".txt"):
        text = _decode(content)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
        chosen = None
    elif ext == ".xls":
        raise QAError("FILE_UNSUPPORTED", "Old .xls workbooks are not read. In Excel use Save As > Excel Workbook "
                                          "(.xlsx) or CSV, then import that file.", status=422)
    else:
        raise QAError("FILE_UNSUPPORTED", "Import an Excel workbook (.xlsx) or a CSV file.", status=422)
    rows = [[str(c).strip() for c in r] for r in rows]
    while rows and not any(rows[0]):
        rows.pop(0)
    if not rows:
        raise QAError("TEST_DATA_INVALID", "The file is empty.", status=422)
    header = rows[0]
    columns: list[str] = []
    for i, name in enumerate(header):
        name = re.sub(r"[{}]", "", name).strip() or f"Column {col_letters(i)}"
        base, n = name, 2
        while name in columns:
            name, n = f"{base} {n}", n + 1
        columns.append(name)
    while columns and columns[-1].startswith("Column ") and not any(len(r) > len(columns) - 1 and r[len(columns) - 1]
                                                                   for r in rows[1:]):
        columns.pop()
    data = []
    for r in rows[1:]:
        if not any(r):
            continue
        data.append({col: (r[i] if i < len(r) else "") for i, col in enumerate(columns)})
        if len(data) > MAX_TABLE_ROWS:
            raise QAError("TEST_DATA_INVALID", f"The file has more than {MAX_TABLE_ROWS} rows. Split it.", status=422)
    if not columns:
        raise QAError("TEST_DATA_INVALID", "The first row must hold the column names.", status=422)
    if not data:
        raise QAError("TEST_DATA_INVALID", "The file has column names but no data rows.", status=422)
    return {"columns": columns, "rows": data, "sheet": chosen, "sheets": sheets}


# ---- text of a downloaded file ----------------------------------------------------------------------------


def _pdf_text(path: Path) -> str:
    if str(VENDOR) not in sys.path:
        sys.path.append(str(VENDOR))  # a pypdf installed on the PC wins; the shipped one is the fallback
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - shipped with the application
        raise QAError("PDF_READER_MISSING", "The PDF reader (pypdf) is missing from qa_platform/vendor.",
                      status=500) from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise QAError("FILE_UNREADABLE", f"{path.name} is password protected.", status=422) from exc
        return "\n".join((p.extract_text() or "") for p in reader.pages[:200])
    except QAError:
        raise
    except Exception as exc:
        raise QAError("FILE_UNREADABLE", f"{path.name} could not be read as a PDF: {str(exc)[:200]}",
                      status=422) from exc


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join("".join(t.text or "" for t in p.iter(w + "t")) for p in root.iter(w + "p"))


def file_text(path: Path) -> str:
    """Text of a downloaded file, for 'Verify the downloaded file contains'."""
    ext = path.suffix.lower()
    content = path.read_bytes()
    if ext == ".pdf" or content[:5] == b"%PDF-":
        return _pdf_text(path)
    if ext in (".xlsx", ".xlsm"):
        return "\n".join(f"{name}\n" + "\n".join("\t".join(r) for r in rows)
                         for name, rows in read_xlsx(content).items())
    if ext == ".docx":
        return _docx_text(content)
    if ext == ".xls":
        raise QAError("FILE_UNSUPPORTED", "Old .xls files cannot be read; check the file name instead.", status=422)
    text = _decode(content)
    if ext in (".html", ".htm", ".xml"):
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def same_value(actual: str, expected: str) -> bool:
    """Cell comparison: text ignoring case and extra spaces; numbers by value (10 = 10.0 = 10.00)."""
    a, e = normalize(actual), normalize(expected)
    if a == e:
        return True
    try:
        return abs(float(a.replace(",", "")) - float(e.replace(",", ""))) < 1e-9
    except ValueError:
        return False


def parse_cell_check(value: str) -> tuple[str | None, str, str]:
    """'B2=Total', 'Sheet1!B2=Total' or 'Summary!C5 = 10' -> (sheet, cell, expected)."""
    if "=" not in (value or ""):
        raise QAError("STEP_VALUE_INVALID", "Write the check as Cell=expected, for example B2=Total or "
                                            "Summary!C5=10.", status=422)
    left, expected = value.split("=", 1)
    sheet, cell = (left.rsplit("!", 1) if "!" in left else (None, left))
    sheet = sheet.strip().strip("'") if sheet else None
    return sheet or None, cell.strip().upper(), expected.strip()


def safe_name(name: str) -> str:
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise QAError("FILE_NAME_INVALID", "The file needs a name.", status=422)
    return name[:150]
