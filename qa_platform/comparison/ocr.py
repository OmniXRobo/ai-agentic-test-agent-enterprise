"""Offline OCR for screenshots and remote (RDP/Citrix) screens, with table and KPI reconstruction.

Engines, tried in this order, all local (no cloud service):
  * Windows OCR (Windows.Media.Ocr, part of Windows 10/11) through PowerShell. It reports no confidence, so each
    word gets a confidence from checks on its own text (digits mixed with O/l/I/S, broken separators ...);
  * Tesseract, when installed (``tesseract`` on PATH or Preferences > Comparison > OCR path), with its own
    per-word confidence.
When neither is available, OCR is reported as unavailable: nothing is guessed.

``table_from_words`` rebuilds rows and columns from word boxes: rows by vertical position, columns by the header
words' horizontal positions. ``kpis_from_lines`` reads "Label: value" or "Label value" cards.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..errors import QAError

WINDOWS_OCR = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:QA_OCR_FILE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No OCR language is installed in Windows.' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = @()
foreach ($l in $result.Lines) {
  $words = @()
  foreach ($w in $l.Words) { $r = $w.BoundingRect; $words += @{ text = $w.Text; box = @($r.X, $r.Y, $r.Width, $r.Height) } }
  $lines += @{ text = $l.Text; words = $words }
}
$stream.Dispose()
@{ engine = 'windows'; width = $bitmap.PixelWidth; height = $bitmap.PixelHeight; lines = $lines } | ConvertTo-Json -Depth 6 -Compress
"""


def _text_confidence(text: str) -> float:
    """A plausibility score for an OCR word without engine confidence (Windows OCR)."""
    t = text.strip()
    if not t:
        return 0.0
    if re.fullmatch(r"[\d.,%()\-+]*[OolISB][\d.,%()\-+]*", t) and re.search(r"\d", t):
        return 0.55  # 1OO, 5l2, 2S0: a digit read as a letter
    if re.search(r"\d[.,]{2,}\d|,\.|\.,", t):
        return 0.6
    return 0.9


def engines(settings: Any = None) -> dict[str, Any]:
    tess = None
    if settings is not None and hasattr(settings, "section"):
        tess = (settings.section("comparison") or {}).get("tesseract_path")
    tess = tess if tess and Path(tess).is_file() else shutil.which("tesseract")
    return {"windows": os.name == "nt" and bool(shutil.which("powershell")), "tesseract": tess}


def _windows(path: Path) -> dict[str, Any]:
    env = dict(os.environ, QA_OCR_FILE=str(path.resolve()))
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                           WINDOWS_OCR], capture_output=True, text=True, timeout=120, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise QAError("OCR_FAILED", "Windows OCR could not read the screenshot.", status=500,
                      details=(proc.stderr or proc.stdout)[-600:])
    data = json.loads(proc.stdout or "{}")
    lines = data.get("lines") or []
    if isinstance(lines, dict):
        lines = [lines]
    for line in lines:
        words = line.get("words") or []
        if isinstance(words, dict):
            words = [words]
        for w in words:
            w["confidence"] = _text_confidence(w.get("text", ""))
        line["words"] = words
        line["box"] = _union([w["box"] for w in words]) if words else [0, 0, 0, 0]
        line["confidence"] = min((w["confidence"] for w in words), default=0.0)
    data["lines"] = lines
    data["confidence_source"] = "text checks (Windows OCR gives no confidence)"
    return data


def _tesseract(path: Path, exe: str) -> dict[str, Any]:
    proc = subprocess.run([exe, str(path), "stdout", "--psm", "11", "tsv"], capture_output=True, text=True,
                          timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        raise QAError("OCR_FAILED", "Tesseract could not read the screenshot.", status=500, details=proc.stderr[-600:])
    return parse_tesseract_tsv(proc.stdout)


def parse_tesseract_tsv(tsv: str) -> dict[str, Any]:
    rows = [r.split("\t") for r in tsv.splitlines()]
    if not rows:
        return {"engine": "tesseract", "lines": []}
    head = rows[0]
    idx = {k: i for i, k in enumerate(head)}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows[1:]:
        if len(r) < len(head) or not r[idx["text"]].strip():
            continue
        conf = float(r[idx["conf"]] or -1)
        word = {"text": r[idx["text"]], "box": [int(r[idx[k]]) for k in ("left", "top", "width", "height")],
                "confidence": round(max(conf, 0) / 100, 3)}
        groups.setdefault((r[idx["block_num"]], r[idx["par_num"]], r[idx["line_num"]]), []).append(word)
    lines = [{"text": " ".join(w["text"] for w in ws), "words": ws, "box": _union([w["box"] for w in ws]),
              "confidence": min(w["confidence"] for w in ws)} for ws in groups.values()]
    lines.sort(key=lambda ln: (ln["box"][1], ln["box"][0]))
    return {"engine": "tesseract", "lines": lines, "confidence_source": "Tesseract"}


def _union(boxes: list[list[float]]) -> list[float]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return [x1, y1, x2 - x1, y2 - y1]


def read(path: Path, settings: Any = None) -> dict[str, Any]:
    eng = engines(settings)
    errors = []
    if eng["windows"]:
        try:
            return _windows(path)
        except Exception as exc:  # fall through to Tesseract
            errors.append(f"Windows OCR: {exc}")
    if eng["tesseract"]:
        return _tesseract(path, eng["tesseract"])
    raise QAError("OCR_UNAVAILABLE", "No offline OCR engine is available. Windows OCR needs Windows 10/11 with an OCR "
                  "language installed; or install Tesseract and set its path in Preferences.", status=503,
                  details=errors)


def words_of(ocr: dict[str, Any]) -> list[dict[str, Any]]:
    return [w for ln in ocr.get("lines") or [] for w in ln.get("words") or []]


def table_from_words(words: list[dict[str, Any]], headers: list[str] | None = None,
                     header_row: int = 0) -> dict[str, Any]:
    """Rows by vertical position; columns from the header row's word positions. Each cell keeps its confidence and
    its box (to draw it on the screenshot)."""
    if not words:
        return {"headers": [], "rows": []}
    heights = sorted(w["box"][3] for w in words)
    tol = max(4.0, heights[len(heights) // 2] * 0.6)
    rows: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: w["box"][1] + w["box"][3] / 2):
        cy = w["box"][1] + w["box"][3] / 2
        if rows and abs(cy - rows[-1][0]["_cy"]) <= tol:
            rows[-1].append({**w, "_cy": rows[-1][0]["_cy"]})
        else:
            rows.append([{**w, "_cy": cy}])
    rows = [sorted(r, key=lambda w: w["box"][0]) for r in rows]
    head_words = rows[header_row] if header_row < len(rows) else rows[0]
    # merge header words that sit close together into one header ("Invoice" "Date" -> "Invoice Date")
    cols: list[dict[str, Any]] = []
    gap = max(12.0, tol * 1.5)
    for w in head_words:
        x1, x2 = w["box"][0], w["box"][0] + w["box"][2]
        if cols and x1 - cols[-1]["x2"] <= gap:
            cols[-1]["name"] += " " + w["text"]
            cols[-1]["x2"] = x2
        else:
            cols.append({"name": w["text"], "x1": x1, "x2": x2})
    if headers and len(headers) == len(cols):
        for c, h in zip(cols, headers):
            c["name"] = h
    bounds = []
    for i, c in enumerate(cols):
        left = -1e9 if i == 0 else (cols[i - 1]["x2"] + c["x1"]) / 2
        right = 1e9 if i == len(cols) - 1 else (c["x2"] + cols[i + 1]["x1"]) / 2
        bounds.append((left, right))
    out_rows = []
    for r in rows[header_row + 1:]:
        cells: dict[str, dict[str, Any]] = {}
        for w in r:
            cx = w["box"][0] + w["box"][2] / 2
            j = next((i for i, (lo, hi) in enumerate(bounds) if lo <= cx < hi), len(bounds) - 1)
            name = cols[j]["name"]
            cell = cells.setdefault(name, {"value": "", "confidence": 1.0, "box": w["box"], "method": "ocr"})
            cell["value"] = (cell["value"] + " " + w["text"]).strip()
            cell["confidence"] = min(cell["confidence"], float(w.get("confidence", 0.0)))
            cell["box"] = _union([cell["box"], w["box"]])
        out_rows.append(cells)
    return {"headers": [c["name"] for c in cols], "rows": out_rows}


KPI_RE = re.compile(r"^(?P<label>[A-Za-z][^:\d]{1,60}?)\s*[:\-]?\s*(?P<value>[(\-+]?(?:[A-Z]{3}\s?)?[\d.,]+%?\)?)$")


def kpis_from_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """KPI cards read as "Total Invoices 1,000" or "Total Invoices: 1,000", or a label line with the number on the
    next line."""
    out = []
    for i, ln in enumerate(lines):
        text = ln.get("text", "").strip()
        m = KPI_RE.match(text)
        if m:
            out.append({"label": m.group("label").strip(), "value": m.group("value"),
                        "confidence": ln.get("confidence", 0.0), "box": ln.get("box"), "method": "ocr"})
            continue
        if i + 1 < len(lines) and re.fullmatch(r"[A-Za-z][A-Za-z %/&().-]{2,60}", text):
            nxt = lines[i + 1].get("text", "").strip()
            if re.fullmatch(r"[(\-+]?(?:[A-Z]{3}\s?)?[\d.,]+%?\)?", nxt):
                out.append({"label": text, "value": nxt,
                            "confidence": min(ln.get("confidence", 0.0), lines[i + 1].get("confidence", 0.0)),
                            "box": _union([ln["box"], lines[i + 1]["box"]]), "method": "ocr"})
    return out
