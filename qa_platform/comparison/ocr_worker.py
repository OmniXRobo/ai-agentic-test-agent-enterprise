"""Offline OCR and chart-graphics worker (23.9.0). Runs in its own process, started by ``ocr.py``.

    python -I ocr_worker.py --site <ocr runtime site-packages> --image <png/jpg> [--mode ocr|graphics|preprocess]
                            [--out <png>] [--engine rapidocr]

Why a separate process: the OCR runtime (RapidOCR with ONNX Runtime, OpenCV, NumPy, Pillow) is installed by the patch
into a folder of its own (``runtime\\ocr\\site-packages`` in the application folder, or the per-user location), not
into the application's Python. The worker puts that folder on its path; the application never imports those
packages, so a broken or missing OCR runtime can never stop SkyGuard QA from starting, and a hostile image can at worst
end this process (it has a time limit and its own memory).

It never downloads anything: RapidOCR's models are inside its wheel, and the worker refuses to start an engine whose
model files are missing instead of letting it fetch them.

Output (stdout, JSON):
  ocr       {engine, width, height, lines:[{text, box, confidence, words:[{text, box, confidence}]}], tiles, scale}
  graphics  {background, rects:[{box, color, fill}], pies:[{box, center, radius, donut, slices:[{color, pixels,
            share}]}], lines:[...]}
  preprocess {out, scale, inverted}: a cleaned copy for Windows OCR / Tesseract (the original is never changed)
Boxes are [x, y, width, height] in the ORIGINAL image's pixels.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ALLOWED = {"PNG", "JPEG", "BMP", "GIF", "WEBP", "TIFF"}
MAX_PIXELS = 80_000_000  # about 9000 x 9000; larger images are refused (decompression bombs)
TILE_H = 1800  # tall screenshots are read in overlapping strips so small text is not shrunk away
OVERLAP = 120


MSVC_DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
             "msvcp140_codecvt_ids.dll", "concrt140.dll", "vcomp140.dll")


def _load_msvc(sites: list[str]) -> str:
    """Windows: load the Microsoft C++ runtime that the patch ships in ``<site>\\_msvc`` before anything else.

    ONNX Runtime and OpenCV link to msvcp140.dll by name. Without this, Windows takes the copy in System32, which on
    many PCs is older than the one they were built with (before 14.40), and the process ends with an access violation
    on its first lock, without a Python error. Once a DLL of that name is loaded, every later load by name uses it."""
    if os.name != "nt":
        return "not needed"
    import ctypes

    for site in sites:
        d = Path(site) / "_msvc"
        if not (d / "msvcp140.dll").is_file():
            continue
        os.add_dll_directory(str(d))
        loaded = []
        for name in MSVC_DLLS:
            if (d / name).is_file():
                ctypes.WinDLL(str(d / name))
                loaded.append(name)
        return f"{len(loaded)} DLLs from {d}"
    return "not shipped (Windows copies are used)"


def stage(text: str) -> None:
    """Progress on stdout, flushed: if a native library ends the process, the last stage says where."""
    print(f"stage: {text}", flush=True)


def fail(code: str, message: str) -> None:
    print(json.dumps({"error": code, "message": message}))
    sys.exit(2)


def load(path: str):  # type: ignore[no-untyped-def]
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(path) as probe:
            fmt = probe.format
            probe.verify()  # structure check before decoding
        if fmt not in ALLOWED:
            fail("OCR_FORMAT", f"Image format {fmt} is not accepted (PNG, JPG, BMP, GIF, WEBP, TIFF).")
        img = Image.open(path)
        img.load()
    except Image.DecompressionBombError:
        fail("OCR_IMAGE_TOO_LARGE", "The image is larger than 80 megapixels.")
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a bad image
        fail("OCR_IMAGE_INVALID", f"The image cannot be decoded: {type(exc).__name__}")
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    return img.convert("RGB")


def luminance_stats(img):  # type: ignore[no-untyped-def]
    import numpy as np

    a = np.asarray(img.convert("L"), dtype=np.uint8)
    border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
    return float(np.median(a)), float(np.median(border))


def preprocess(img, *, for_engine: str):  # type: ignore[no-untyped-def]
    """Scale small screenshots up (text under ~12 px is read badly by every engine), invert dark themes for engines
    trained on dark text (Tesseract, Windows OCR), stretch contrast. Returns (image, scale, inverted)."""
    from PIL import ImageOps

    w, h = img.size
    scale = 1.0
    if max(w, h) < 1100:
        scale = 2.0
    elif max(w, h) < 1600:
        scale = 1.5
    if for_engine == "windows" and max(w, h) * scale > 9500:  # Windows OCR refuses images over MaxImageDimension
        scale = 9500 / max(w, h)
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), resample=3)  # bicubic
    inverted = False
    if for_engine in ("tesseract", "windows"):
        med, border = luminance_stats(img)
        gray = img.convert("L")
        if border < 110 and med < 128:  # light text on a dark background
            gray = ImageOps.invert(gray)
            inverted = True
        img = ImageOps.autocontrast(gray, cutoff=1).convert("RGB")
    return img, scale, inverted


# ---- RapidOCR ----------------------------------------------------------------------------------------------------

def _engine():  # type: ignore[no-untyped-def]
    try:
        import rapidocr
        from rapidocr import RapidOCR
    except Exception as exc:  # noqa: BLE001
        fail("OCR_RUNTIME_MISSING", f"The bundled OCR runtime could not be loaded ({type(exc).__name__}: {exc}).")
    models = Path(rapidocr.__file__).parent / "models"
    if not any(models.glob("*det*.onnx")) or not any(models.glob("*rec*.onnx")):
        fail("OCR_MODELS_MISSING", f"The OCR model files are missing in {models}. Install the patch again.")
    return RapidOCR(params={"Global.log_level": "error", "Global.max_side_len": 4096, "Global.min_side_len": 30,
                            "Global.return_word_box": True, "Global.text_score": 0.45,
                            # screenshots are never upside down: the angle classifier only turned "Dubai" into "uqna"
                            "Global.use_cls": False})


def _box(pts, dx: float, dy: float, s: float) -> list[float]:  # type: ignore[no-untyped-def]
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    return [round((x1 + dx) / s, 1), round((y1 + dy) / s, 1), round((x2 - x1) / s, 1), round((y2 - y1) / s, 1)]


def _overlap(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min(a[2] * a[3], b[2] * b[3]) or 1.0
    return inter / small


def run_rapidocr(img) -> dict:  # type: ignore[no-untyped-def]
    import numpy as np

    eng = _engine()
    work, scale, _ = preprocess(img, for_engine="rapidocr")
    W, H = work.size
    tiles = []
    y = 0
    while True:
        y2 = min(H, y + TILE_H)
        tiles.append((y, y2))
        if y2 >= H:
            break
        y = y2 - OVERLAP
    lines: list[dict] = []
    for (t0, t1) in tiles:
        arr = np.asarray(work.crop((0, t0, W, t1)))[:, :, ::-1].copy()  # RGB -> BGR for OpenCV
        res = eng(arr, return_word_box=True)
        if res.boxes is None:
            continue
        words_all = res.word_results or [None] * len(res.txts)
        for box, txt, score, wr in zip(res.boxes, res.txts, res.scores, words_all):
            lb = _box(box, 0, t0, scale)
            if any(_overlap(lb, other["box"]) > 0.6 and other["text"] == txt for other in lines):
                continue  # read twice in the overlap of two strips
            words = []
            for w in (wr or []):
                wt, ws, wb = w[0], float(w[1]), w[2]
                if str(wt).strip():
                    words.append({"text": str(wt), "box": _box(wb, 0, t0, scale), "confidence": round(ws, 3)})
            if not words:
                words = [{"text": str(txt), "box": lb, "confidence": round(float(score), 3)}]
            lines.append({"text": str(txt), "box": lb, "confidence": round(float(score), 3), "words": words})
    lines.sort(key=lambda ln: (ln["box"][1], ln["box"][0]))
    return {"engine": "rapidocr", "width": img.size[0], "height": img.size[1], "lines": lines,
            "tiles": len(tiles), "scale": scale, "confidence_source": "RapidOCR (PP-OCR, offline ONNX models)"}


# ---- chart graphics ------------------------------------------------------------------------------------------------

def graphics(img) -> dict:  # type: ignore[no-untyped-def]
    """Solid coloured shapes: bars (filled rectangles) and pie / donut slices (one disc split by colour). Text and thin
    lines are not shapes. Nothing is guessed here; the screen reader decides what the shapes mean."""
    import cv2
    import numpy as np

    a = np.asarray(img, dtype=np.int16)
    H, W = a.shape[:2]
    border = np.concatenate([a[0, :, :], a[-1, :, :], a[:, 0, :], a[:, -1, :]])
    vals, counts = np.unique((border // 8), axis=0, return_counts=True)
    bg = (vals[counts.argmax()] * 8 + 4).tolist()
    sat = a.max(axis=2) - a.min(axis=2)
    q = (a // 24).astype(np.int32)
    key = q[:, :, 0] * 10000 + q[:, :, 1] * 100 + q[:, :, 2]
    colored = (sat >= 45) & (np.abs(a - np.array(bg)).sum(axis=2) > 60)
    rects, blobs = [], []
    keys, kcounts = np.unique(key[colored], return_counts=True)
    for k, n in zip(keys, kcounts):
        if n < 120:
            continue
        mask = ((key == k) & colored).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        num, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for i in range(1, num):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < 120 or w < 6 or h < 6:
                continue
            fill = area / float(w * h)
            col = [int(c) for c in np.median(a[lab == i], axis=0)]
            item = {"box": [x, y, w, h], "color": col, "fill": round(fill, 3), "pixels": int(area)}
            if fill >= 0.88:
                rects.append(item)
            else:
                blobs.append(item)
    pies = []
    # a pie / donut: non-rectangular blobs (and quarter-like rects) of several colours that together form a disc
    cand = blobs + [r for r in rects if r["box"][2] > 40 and r["box"][3] > 40 and r["fill"] < 0.97]
    used: set[int] = set()
    for i, b in enumerate(sorted(cand, key=lambda r: -r["pixels"])):
        if i in used:
            continue
        group = [b]
        x, y, w, h = b["box"]
        for j, o in enumerate(sorted(cand, key=lambda r: -r["pixels"])):
            if j == i or j in used:
                continue
            ox, oy, ow, oh = o["box"]
            if ox < x + w + 4 and ox + ow > x - 4 and oy < y + h + 4 and oy + oh > y - 4:
                group.append(o)
                used.add(j)
                x1, y1 = min(x, ox), min(y, oy)
                x, y, w, h = x1, y1, max(x + w, ox + ow) - x1, max(y + h, oy + oh) - y1
        used.add(i)
        if len(group) < 2 or w < 60 or h < 60 or abs(w - h) > 0.12 * max(w, h):
            continue
        total = sum(g["pixels"] for g in group)
        disc = total / (w * h)
        if disc < 0.35 or disc > 0.86:
            continue
        cx, cy, r = x + w / 2, y + h / 2, (w + h) / 4
        centre = a[int(cy), int(cx)]
        donut = bool(np.abs(centre - np.array(bg)).sum() < 60) or disc < 0.62
        slices = [{"color": g["color"], "pixels": g["pixels"], "share": round(g["pixels"] / total, 4),
                   "box": g["box"]} for g in sorted(group, key=lambda g: -g["pixels"])]
        pies.append({"box": [int(x), int(y), int(w), int(h)], "center": [round(cx, 1), round(cy, 1)],
                     "radius": round(r, 1), "donut": donut, "slices": slices})
        for g in group:
            if g in rects:
                rects.remove(g)
    # axis / grid lines: long thin horizontal runs (help to find the zero line of a bar chart)
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    edges = (np.abs(gray.astype(np.int16) - int(sum(bg) / 3)) > 30).astype(np.uint8)
    horiz = cv2.morphologyEx(edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, W // 6), 1)))
    num, _, stats, _ = cv2.connectedComponentsWithStats(horiz, connectivity=4)
    hlines = [[int(v) for v in stats[i][:4]] for i in range(1, num) if stats[i][3] <= 3][:60]
    return {"background": bg, "rects": rects[:400], "pies": pies[:6], "hlines": hlines, "width": W, "height": H}


def _self_test() -> str:
    """The engine reads a small image drawn here (no file, no network): import alone does not prove that ONNX
    Runtime can create a session and run the models on this PC."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (520, 110), "white")
    try:
        font = ImageFont.load_default(size=44)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((20, 25), "Total 4120", fill="black", font=font)
    res = run_rapidocr(img)
    text = " ".join(ln.get("text", "") for ln in res.get("lines") or [])
    return f"read '{text[:40]}'" if "4120" in text.replace(",", "") else f"missing (read '{text[:40]}')"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="")
    ap.add_argument("--image", required=True)
    ap.add_argument("--mode", default="ocr", choices=("ocr", "graphics", "preprocess", "probe"))
    ap.add_argument("--engine", default="rapidocr")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sites = [p for p in args.site.split(os.pathsep) if p and Path(p).is_dir()]
    for p in sites:
        sys.path.insert(0, p)
    started = time.monotonic()
    stage("C++ runtime")
    try:
        msvc = _load_msvc(sites)
    except OSError as exc:
        fail("OCR_RUNTIME_MISSING", f"The C++ runtime of the OCR engine could not be loaded ({exc}).")
    if args.mode == "probe":
        info = {"msvc": msvc}
        for mod in ("PIL", "numpy", "cv2", "onnxruntime", "rapidocr"):
            stage(f"import {mod}")
            try:
                m = __import__(mod)
                info[mod] = getattr(m, "__version__", "ok")
            except Exception as exc:  # noqa: BLE001
                info[mod] = f"missing ({type(exc).__name__}: {exc})"[:300]
        if not any(str(v).startswith("missing") for v in info.values()):
            stage("read a test image")
            info["test"] = _self_test()
        print(json.dumps({"probe": info}))
        return
    img = load(args.image)
    if args.mode == "preprocess":
        out, scale, inv = preprocess(img, for_engine=args.engine)
        if not args.out:
            fail("OCR_ARGS", "--out is required")
        out.save(args.out, "PNG")
        print(json.dumps({"out": args.out, "scale": scale, "inverted": inv}))
        return
    if args.mode == "graphics":
        print(json.dumps(graphics(img)))
        return
    res = run_rapidocr(img)
    try:
        res["graphics"] = graphics(img)
    except Exception as exc:  # noqa: BLE001 - charts are optional; text still counts
        res["graphics"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    res["seconds"] = round(time.monotonic() - started, 2)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
