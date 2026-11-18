r"""Offline OCR for screenshots and remote (RDP/Citrix) screens, with table and KPI reconstruction.

23.9.0: engines, in the order of Preferences > Comparison > OCR engine ("auto" = this order), all local, no cloud:
  * the bundled engine (RapidOCR: PP-OCR detection + recognition models run by ONNX Runtime). The patch installs it
    with its models into runtime\ocr (or the per-user folder when the application folder cannot be written). It runs
    in a separate process (``ocr_worker.py``), reads tall screenshots in strips, and gives every word its box and the
    model's own confidence. The same worker measures chart shapes (bars, pie slices) for ``screen_reader``;
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
import sys
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


# 23.9.0: a second, closer reading of table cells that came back empty. Windows OCR skips a character that stands
# alone (a "0" in a cell), so each cell is cut out, enlarged three times and drawn COPIES times side by side on a
# white page (System.Drawing, part of Windows): the copies form a line that the engine reads. Python maps each word
# back to its copy and accepts a number only when the copies agree.
CELL_SCALE, CELL_PAD, CELL_COPIES = 3, 40, 3
CELL_SCALES = (3, 2, 1)  # tried in this order: the first that gives an agreeing number counts
WINDOWS_OCR_CELLS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'No OCR language is installed in Windows.' }
$spec = Get-Content -Raw -LiteralPath $env:QA_OCR_RECTS | ConvertFrom-Json
$pad = [int]$spec.pad; $copies = [int]$spec.copies
$src = [System.Drawing.Bitmap]::FromFile($env:QA_OCR_FILE)
$cells = @(); $i = 0
foreach ($r in @($spec.rects)) {
  $x = [Math]::Max(0, [int]$r.x); $y = [Math]::Max(0, [int]$r.y)
  $w = [Math]::Min([int]$r.w, $src.Width - $x); $h = [Math]::Min([int]$r.h, $src.Height - $y)
  # dark pixels in the cell, to tell an empty cell from one the engine could not read
  $ink = 0
  for ($py = $y; $py -lt ($y + $h); $py += 1) { for ($px = $x; $px -lt ($x + $w); $px += 1) {
    $c = $src.GetPixel($px, $py); if (($c.R + $c.G + $c.B) -lt 384) { $ink++ } } }
  $left = $pad
  foreach ($scale in @($spec.scales)) {
  $scale = [int]$scale
  $words = @()
  if ($w -gt 2 -and $h -gt 2) {
    $cw = $w * $scale; $ch = $h * $scale; $gap = $ch
    # a printed word in front of the copies gives the engine a line of text to work from; its words are ignored
    $font = New-Object System.Drawing.Font('Arial', [single]([Math]::Max(8, $ch * 0.35)), [System.Drawing.GraphicsUnit]::Pixel)
    $probe = New-Object System.Drawing.Bitmap(1, 1); $pg = [System.Drawing.Graphics]::FromImage($probe)
    $labelW = [int]$pg.MeasureString('Value', $font).Width; $pg.Dispose(); $probe.Dispose()
    $left = $pad + $labelW + $gap
    $canvas = New-Object System.Drawing.Bitmap(($left + $pad + $copies * $cw + ($copies - 1) * $gap), ($ch + 2 * $pad))
    $g = [System.Drawing.Graphics]::FromImage($canvas)
    $g.Clear([System.Drawing.Color]::White)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawString('Value', $font, [System.Drawing.Brushes]::Black, [single]$pad, [single]($pad + ($ch - $font.Height) / 2))
    $font.Dispose()
    for ($k = 0; $k -lt $copies; $k++) {
      $g.DrawImage($src, (New-Object System.Drawing.Rectangle(($left + $k * ($cw + $gap)), $pad, $cw, $ch)),
                   (New-Object System.Drawing.Rectangle($x, $y, $w, $h)), [System.Drawing.GraphicsUnit]::Pixel)
    }
    $g.Dispose()
    $tmp = Join-Path $env:QA_OCR_TMP ("cell-" + $i + "-" + $scale + ".png")
    $canvas.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
    $canvas.Dispose()
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    foreach ($l in $result.Lines) { foreach ($wd in $l.Words) {
      $b = $wd.BoundingRect
      $words += @{ text = $wd.Text; box = @($b.X, $b.Y, $b.Width, $b.Height) } } }
    $stream.Dispose()
  }
  $cells += ,@{ index = $i; scale = $scale; x = $x; y = $y; w = $w; h = $h; left = $left; ink = $ink; words = $words }
  }
  $i++
}
$src.Dispose()
ConvertTo-Json -InputObject @{ cells = $cells } -Depth 6 -Compress
"""


def _as_list(v: Any) -> list[Any]:
    return [v] if isinstance(v, dict) else list(v or [])


def _cell_number(rect: list[float], words: list[dict[str, Any]], scale: int = CELL_SCALE,
                 left: float = CELL_PAD) -> dict[str, Any] | None:
    """The number read in the copies of one cell (see WINDOWS_OCR_CELLS), with its box on the screenshot; None
    unless at least two copies show the same number and no copy shows another one."""
    w, h = rect[2], rect[3]
    cw, gap = w * scale, h * scale
    reads: dict[int, str] = {}
    boxes: dict[int, list[float]] = {}
    for wd in words:
        text = str(wd.get("text") or "").strip()
        b = [float(v) for v in (wd.get("box") or [])]
        if not text or len(b) != 4:
            continue
        if b[0] + b[2] <= left:
            continue  # the printed label in front of the copies
        first = int((b[0] - left) // (cw + gap))
        last = int((b[0] + b[2] - 1 - left) // (cw + gap))
        span = [k for k in range(max(first, 0), min(last, CELL_COPIES - 1) + 1)]
        if not span:
            continue
        unit = text
        if len(span) > 1:  # copies run together into one word ("000"): the repeated part, if it repeats exactly
            n = len(span)
            unit = text[: len(text) // n] if len(text) % n == 0 and text[: len(text) // n] * n == text else ""
        for k in span:
            if k in reads and reads[k] != unit:
                reads[k] = "\x00"  # two different readings in one copy
            else:
                reads[k] = unit
                off = left + k * (cw + gap)
                boxes[k] = [rect[0] + max(0.0, b[0] - off) / scale, rect[1] + (b[1] - CELL_PAD) / scale,
                            min(w, b[2] / len(span) / scale), b[3] / scale]
    from .screen_reader import is_number

    texts = {t for t in reads.values()}
    if len(texts) != 1:
        return None
    text = texts.pop()
    agree = [k for k, t in reads.items() if t == text]
    read_as = None
    if not is_number(text):
        # the cell belongs to a number column: O for 0 and I / l / | for 1, as in screen_reader._digit_lookalikes
        from .screen_reader import LOOKALIKE, _DIGIT_OF
        if LOOKALIKE.match(text) and is_number(text.translate(_DIGIT_OF)):
            read_as, text = text, text.translate(_DIGIT_OF)
    if len(agree) < 2 or not is_number(text):
        return None
    out = {"text": text, "box": [round(v, 1) for v in boxes[agree[0]]], "copies": len(agree)}
    if read_as:
        out["read_as"] = read_as
    return out


def _windows_cells(path: Path, rects: list[list[float]], settings: Any = None) -> list[dict[str, Any]]:
    """The number Windows OCR reads in each box (image pixels), each box read on its own (see _cell_number)."""
    import tempfile

    ps = _powershell()
    if not ps or not rects:
        return []
    tmp = Path(tempfile.mkdtemp(prefix="skyguard-ocr-cells-"))
    try:
        spec = tmp / "rects.json"
        spec.write_text(json.dumps({"scales": list(CELL_SCALES), "pad": CELL_PAD, "copies": CELL_COPIES,
                                    "rects": [{"x": int(r[0]), "y": int(r[1]), "w": int(r[2]), "h": int(r[3])}
                                              for r in rects]}), encoding="utf-8")
        env = dict(os.environ, QA_OCR_FILE=str(Path(path).resolve()), QA_OCR_RECTS=str(spec), QA_OCR_TMP=str(tmp))
        proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", WINDOWS_OCR_CELLS],
                              capture_output=True, text=True, timeout=120, env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            raise QAError("OCR_FAILED", "Windows OCR could not read the table cells again.", status=500,
                          details=(proc.stderr or proc.stdout or "")[-400:])
        cells = _as_list(json.loads(proc.stdout or "{}").get("cells"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    by_cell: dict[int, list[dict[str, Any]]] = {}
    for c in cells:
        i = int(c.get("index", -1))
        if 0 <= i < len(rects):
            by_cell.setdefault(i, []).append(c)
    out = []
    for i, tries in sorted(by_cell.items()):
        found, seen = None, []
        for c in sorted(tries, key=lambda c: CELL_SCALES.index(int(c.get("scale") or CELL_SCALE))
                        if int(c.get("scale") or CELL_SCALE) in CELL_SCALES else 99):
            used = [int(c.get(k, rects[i][n])) for n, k in enumerate(("x", "y", "w", "h"))]  # clipped to the image
            words = _as_list(c.get("words"))
            seen.append(f"x{c.get('scale')} ink {c.get('ink')}: "
                        + " ".join(str(w.get("text") or "")[:12] for w in words[:8]))
            found = _cell_number(used, words, int(c.get("scale") or CELL_SCALE), float(c.get("left") or CELL_PAD))
            if found:
                break
        # what the engine saw is kept, so that a cell that is still empty can be explained
        out.append(found or {"seen": seen})
    return out


def _fill_empty_cells(path: Path, data: dict[str, Any], settings: Any = None, reader: Any = None) -> dict[str, Any]:
    """Windows OCR drops a digit that stands alone in a table cell (a "0" between other numbers). The cells of
    number columns that came back empty are read again, enlarged (``_windows_cells``). Only a number read inside
    such a cell is added, with a confidence at the review threshold; a cell that still reads as nothing stays
    empty. What happened is kept in ``cell_rereads`` for the analysis."""
    import copy

    from . import screen_reader

    rects = screen_reader.empty_number_cells(screen_reader.read_screen(copy.deepcopy(data)))
    if not rects:
        return data
    try:
        found = (reader or _windows_cells)(path, rects, settings)
    except QAError as exc:
        data["cell_rereads"] = {"cells": len(rects), "read": 0, "error": f"{exc.message} {exc.details or ''}"[:500]}
        return data
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        data["cell_rereads"] = {"cells": len(rects), "read": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}
        return data
    have = [w["box"] for w in words_of(data)]
    added = []
    seen = [w["seen"] for w in found if "seen" in w]
    for w in found:
        if "text" not in w:
            continue
        text = str(w["text"]).strip()
        box = [float(v) for v in w["box"]]
        if not screen_reader.is_number(text) or any(_overlaps(box, b) for b in have):
            continue
        if not any(_overlaps(box, r) for r in rects):
            continue
        word = {"text": text, "box": box, "confidence": min(_text_confidence(text), screen_reader.LOW)}
        added.append(word)
        have.append(box)
    if added:
        data["lines"] = list(data.get("lines") or []) + [{"text": w["text"], "words": [w], "box": w["box"],
                                                          "confidence": w["confidence"]} for w in added]
    data["cell_rereads"] = {"cells": len(rects), "read": len(added)}
    if seen:
        data["cell_rereads"]["unread"] = seen[:10]
    return data

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


def _section(settings: Any) -> dict[str, Any]:
    if settings is not None and hasattr(settings, "section"):
        try:
            return dict(settings.section("comparison") or {})
        except Exception:
            return {}
    return {}


def _user_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "SkyGuardQA"


def runtime_sites(settings: Any = None) -> list[Path]:
    """Folders that may hold the bundled OCR runtime (RapidOCR + ONNX Runtime + OpenCV + NumPy + Pillow), in order:
    Preferences, the application folder, the per-user folder (used when the application folder cannot be written)."""
    from ..config import APP_ROOT

    cfg = _section(settings)
    out = []
    if cfg.get("ocr_runtime_path"):
        out.append(Path(str(cfg["ocr_runtime_path"])).expanduser())
    out += [APP_ROOT / "runtime" / "ocr" / "site-packages", _user_dir() / "ocr" / "site-packages"]
    return out


def _bundled_site(settings: Any = None) -> str | None:
    for p in runtime_sites(settings):
        if (p / "rapidocr").is_dir() and (p / "onnxruntime").is_dir():
            return str(p)
    try:  # a development installation that has the packages in its own Python
        import importlib.util

        spec = importlib.util.find_spec("rapidocr")
        if spec and spec.origin and importlib.util.find_spec("onnxruntime"):
            return str(Path(spec.origin).parent.parent)
    except (ImportError, ValueError):
        pass
    return None


def _tesseract_exe(settings: Any = None) -> str | None:
    from ..config import APP_ROOT

    cfg = _section(settings)
    exe = "tesseract.exe" if os.name == "nt" else "tesseract"
    for cand in [cfg.get("tesseract_path"), APP_ROOT / "runtime" / "tesseract" / exe, _user_dir() / "tesseract" / exe]:
        if cand and Path(str(cand)).is_file():
            return str(cand)
    return shutil.which("tesseract")


def _powershell() -> str | None:
    if os.name != "nt":
        return None
    found = shutil.which("powershell")
    if found:
        return found
    # the launcher may start the application with a short PATH: Windows PowerShell is always in System32
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    p = Path(sysroot) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(p) if p.is_file() else None


def engines(settings: Any = None) -> dict[str, Any]:
    """Engines that are present (not yet proven to work: see ``diagnose``)."""
    return {"rapidocr": _bundled_site(settings), "windows": bool(_powershell()), "tesseract": _tesseract_exe(settings)}


def order(settings: Any = None) -> list[str]:
    choice = str(_section(settings).get("ocr_engine") or "auto").lower()
    default = ["rapidocr", "windows", "tesseract"]
    if choice in default:
        return [choice] + [e for e in default if e != choice]
    return default


def _worker(args: list[str], settings: Any = None, timeout: float | None = None) -> dict[str, Any]:
    site = _bundled_site(settings)
    if not site:
        raise QAError("OCR_RUNTIME_MISSING", "The bundled OCR runtime is not installed (runtime\\ocr). Apply the "
                      "23.9.0 patch again, or choose another engine in Preferences > Comparison.", status=503)
    script = Path(__file__).with_name("ocr_worker.py")
    limit = float(timeout or _section(settings).get("ocr_timeout_seconds") or 180)
    try:
        proc = subprocess.run([sys.executable, "-I", str(script), "--site", site, *args], capture_output=True,
                              text=True, timeout=limit, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise QAError("OCR_TIMEOUT", f"The OCR engine did not finish within {int(limit)} seconds.", status=504) from exc
    try:
        data = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        # 23.9.0: say where it stopped (the worker prints a stage before each step) and how (the exit code: on
        # Windows 0xC0000005 is an access violation inside a native library, 0xC0000135 a missing DLL)
        out = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.startswith("stage: ")]
        where = out[-1][7:] if out else "start"
        code = proc.returncode & 0xFFFFFFFF if proc.returncode is not None else None
        how = f"exit code 0x{code:08X}" if code is not None and code > 255 else f"exit code {proc.returncode}"
        err = " ".join((proc.stderr or "").strip().splitlines()[-2:])[-240:]
        raise QAError("OCR_FAILED", f"The OCR engine stopped without an answer at '{where}' ({how})"
                      + (f": {err}" if err else "") + ".", status=500,
                      details=(proc.stderr or "")[-600:]) from None
    if data.get("error"):
        raise QAError(data["error"], data.get("message") or "The OCR engine failed.", status=422
                      if data["error"].startswith("OCR_IMAGE") or data["error"] == "OCR_FORMAT" else 503)
    return data


def _preprocessed(path: Path, engine: str, settings: Any) -> tuple[Path, float, Path | None]:
    """A cleaned copy for engines trained on dark text on white (only when the bundled runtime is present)."""
    if not _bundled_site(settings):
        return path, 1.0, None
    import tempfile

    fd, tmp = tempfile.mkstemp(prefix="skyguard-ocr-", suffix=".png")
    os.close(fd)
    try:
        info = _worker(["--image", str(path), "--mode", "preprocess", "--engine", engine, "--out", tmp], settings, 60)
        return Path(tmp), float(info.get("scale") or 1.0), Path(tmp)
    except QAError:
        Path(tmp).unlink(missing_ok=True)
        return path, 1.0, None


def _rescale(data: dict[str, Any], scale: float) -> dict[str, Any]:
    if scale == 1.0:
        return data
    for line in data.get("lines") or []:
        for w in line.get("words") or []:
            w["box"] = [round(v / scale, 1) for v in w["box"]]
        line["box"] = [round(v / scale, 1) for v in line.get("box") or [0, 0, 0, 0]]
    data["scale"] = scale
    return data


def _rapidocr(path: Path, settings: Any) -> dict[str, Any]:
    return _worker(["--image", str(path), "--mode", "ocr"], settings)


def graphics(path: Path, settings: Any = None) -> dict[str, Any] | None:
    """Coloured chart shapes (bars, pie slices) of a screenshot; None when the bundled runtime is missing."""
    if not _bundled_site(settings):
        return None
    try:
        return _worker(["--image", str(path), "--mode", "graphics"], settings, 90)
    except QAError:
        return None


def _windows(path: Path, settings: Any = None) -> dict[str, Any]:
    ps = _powershell()
    if not ps:
        raise QAError("OCR_FAILED", "Windows PowerShell was not found.", status=503)
    src, scale, tmp = _preprocessed(path, "windows", settings)
    env = dict(os.environ, QA_OCR_FILE=str(src.resolve()))
    try:
        proc = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", WINDOWS_OCR], capture_output=True,
                              text=True, timeout=120, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")
        hint = ""
        if "LanguageMode" in err or "Cannot add type" in err or "language mode" in err.lower():
            hint = " PowerShell runs in Constrained Language Mode on this PC (an IT policy), which blocks Windows OCR."
        elif "MaxImageDimension" in err or "dimension" in err.lower():
            hint = " The image is larger than Windows OCR accepts."
        raise QAError("OCR_FAILED", "Windows OCR could not read the screenshot." + hint, status=500,
                      details=err[-600:])
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
    return _rescale(data, scale)


def _tesseract(path: Path, exe: str, settings: Any = None, psm: str = "11") -> dict[str, Any]:
    src, scale, tmp = _preprocessed(path, "tesseract", settings)
    env = dict(os.environ)
    tessdata = _section(settings).get("tessdata_path")
    if tessdata:
        env["TESSDATA_PREFIX"] = str(tessdata)
    elif (Path(exe).parent / "tessdata").is_dir():
        env["TESSDATA_PREFIX"] = str(Path(exe).parent / "tessdata")
    try:
        proc = subprocess.run([exe, str(src), "stdout", "--psm", psm, "tsv"], capture_output=True, text=True,
                              timeout=180, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        detail = proc.stderr[-600:]
        hint = " Its language data (eng.traineddata) was not found: set the tessdata folder in Preferences." \
            if "traineddata" in detail or "TESSDATA" in detail else ""
        raise QAError("OCR_FAILED", "Tesseract could not read the screenshot." + hint, status=500, details=detail)
    return _rescale(parse_tesseract_tsv(proc.stdout), scale)


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
    """Read a screenshot with the first offline engine that works (order: Preferences > Comparison > OCR engine,
    else the bundled engine, Windows OCR, Tesseract). Every engine that failed is named in the result (``attempts``)
    or, when none worked, in the error, with the reason."""
    eng = engines(settings)
    attempts: list[dict[str, str]] = []
    for name in order(settings):
        if not eng.get(name):
            attempts.append({"engine": name, "status": "not installed"})
            continue
        try:
            if name == "rapidocr":
                data = _rapidocr(path, settings)
            elif name == "windows":
                data = _windows(path, settings)
            else:
                data = _tesseract(path, eng["tesseract"], settings)
        except QAError as exc:
            if exc.error_code in ("OCR_IMAGE_INVALID", "OCR_IMAGE_TOO_LARGE", "OCR_FORMAT"):
                raise  # the image itself is the problem: another engine will not help
            attempts.append({"engine": name, "status": "failed", "reason": exc.message})
            continue
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            attempts.append({"engine": name, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        data["attempts"] = attempts
        return data
    reasons = "; ".join(f"{a['engine']}: {a.get('reason') or a['status']}" for a in attempts)
    raise QAError("OCR_UNAVAILABLE", "No offline OCR engine could read the screenshot (" + reasons + "). "
                  "Open Preferences > Comparison > OCR diagnostics to see what is missing.", status=503,
                  details=attempts)


def diagnose(settings: Any = None, sample: Path | None = None) -> dict[str, Any]:
    """Which OCR engines exist on this PC, which one is used, and why the others are not, in plain words."""
    eng = engines(settings)
    out: list[dict[str, Any]] = []
    site = eng["rapidocr"]
    if site:
        try:
            probe = _worker(["--image", "-", "--mode", "probe"], settings, 120)["probe"]
            ok = all(not str(v).startswith("missing") for v in probe.values()) and "test" in probe
            out.append({"engine": "rapidocr", "label": "Bundled OCR (RapidOCR, offline)", "status": "ready" if ok
                        else "broken", "detail": ", ".join(f"{k} {v}" for k, v in probe.items()), "path": site})
        except QAError as exc:
            out.append({"engine": "rapidocr", "label": "Bundled OCR (RapidOCR, offline)", "status": "broken",
                        "detail": exc.message, "path": site})
    else:
        out.append({"engine": "rapidocr", "label": "Bundled OCR (RapidOCR, offline)", "status": "not installed",
                    "detail": "Looked in: " + "; ".join(str(p) for p in runtime_sites(settings))})
    if os.name == "nt":
        out.append({"engine": "windows", "label": "Windows OCR", "status": "available" if eng["windows"] else
                    "not available", "detail": "Windows.Media.Ocr through Windows PowerShell; needs an OCR language "
                    "(Settings > Time & language > Language) and PowerShell in Full Language Mode."})
    else:
        out.append({"engine": "windows", "label": "Windows OCR", "status": "not available",
                    "detail": "Only on Windows 10/11."})
    out.append({"engine": "tesseract", "label": "Tesseract", "status": "available" if eng["tesseract"] else
                "not installed", "path": eng["tesseract"],
                "detail": "Optional. Put a portable Tesseract in runtime\\tesseract or set its path in Preferences."})
    used = next((e for e in order(settings) if any(o["engine"] == e and o["status"] in ("ready", "available")
                                                   for o in out)), None)
    res: dict[str, Any] = {"engines": out, "order": order(settings), "selected": used,
                           "graphics": bool(site), "offline": True}
    if sample is not None:
        try:
            r = read(sample, settings)
            res["sample"] = {"engine": r.get("engine"), "lines": len(r.get("lines") or [])}
        except QAError as exc:
            res["sample"] = {"error": exc.message}
    return res


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


# ---- 23.8.0 Integration Comparison Workspace -----------------------------------------------------------------------

def _overlaps(a: list[float], b: list[float]) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def _grow(b: list[float], d: float) -> list[float]:
    return [b[0] - d, b[1] - d, b[2] + 2 * d, b[3] + 2 * d]


def read_for_layout(path: Path, settings: Any = None) -> dict[str, Any]:
    """OCR for the workspace's screen reader. With Windows OCR, empty cells of number columns are read again one by
    one (``_fill_empty_cells``). With Tesseract, two page modes are merged: sparse text (finds labels
    and chart values anywhere) and a uniform block (finds single digits in table rows that sparse mode misses).
    A word of the second pass is added only where the first pass found nothing."""
    eng = engines(settings)
    first = read(path, settings)
    if first.get("engine") == "windows":
        return _fill_empty_cells(path, first, settings)
    if first.get("engine") != "tesseract" or not eng["tesseract"]:
        return first
    try:
        second = _tesseract(path, eng["tesseract"], settings, psm="6")
    except (QAError, OSError, subprocess.SubprocessError):
        return first
    have = [w["box"] for w in words_of(first)]
    # a word of the second pass counts only where the first pass found nothing and it is not the same text a few
    # pixels away (that duplicate is what turned table cells into repeated strings in 23.8.0)
    extra = [w for w in words_of(second) if not any(_overlaps(_grow(w["box"], 4), b) for b in have)]
    if extra:
        first["lines"] = list(first["lines"]) + [{"text": w["text"], "words": [w], "box": w["box"],
                                                  "confidence": w["confidence"]} for w in extra]
        first["confidence_source"] = "Tesseract (two page modes)"
    return first
