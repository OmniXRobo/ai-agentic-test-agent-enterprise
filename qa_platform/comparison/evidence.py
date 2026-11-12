"""Evidence storage for the Data Comparison Agent: screenshots that are checked, cleaned and kept read-only.

* Only PNG and JPEG are accepted, recognised by their file signature (never by the name or the declared type).
* Limits: 15 MB per file, at most 12,000 pixels per side and 60 megapixels (decompression bombs are refused before
  any decoding, from the header alone).
* Metadata is removed (PNG text/time/EXIF chunks, JPEG APP1..APP15 and comments), so a screenshot never carries a
  camera serial, GPS position or user name.
* The stored original is named by its id (the uploaded name is kept only as text), hashed (SHA-256) and made
  read-only. Annotated copies are separate SVG files that show the original with highlight boxes; the original is
  never changed.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import stat
import struct
import uuid
from pathlib import Path
from typing import Any

from ..errors import QAError

MAX_BYTES = 15 * 1024 * 1024
MAX_SIDE = 12000
MAX_PIXELS = 60_000_000
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"bKGD", b"pHYs"}


def kind_of(data: bytes) -> str | None:
    if data.startswith(PNG_SIG):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    return None


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise QAError("EVIDENCE_INVALID", "The PNG file is damaged (no image header).", status=422)
    return struct.unpack(">II", data[16:24])


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise QAError("EVIDENCE_INVALID", "The JPEG file is damaged (no frame header).", status=422)


def check_image(data: bytes) -> dict[str, Any]:
    """{kind, width, height} or QAError. Nothing is decoded: header checks only."""
    if not data:
        raise QAError("EVIDENCE_EMPTY", "The file is empty.", status=422)
    if len(data) > MAX_BYTES:
        raise QAError("EVIDENCE_TOO_LARGE", f"Screenshots can be at most {MAX_BYTES // 1048576} MB.", status=413)
    kind = kind_of(data)
    if kind is None:
        raise QAError("EVIDENCE_TYPE", "Only PNG and JPEG screenshots are accepted (checked by file content).",
                      status=415)
    w, h = _png_size(data) if kind == "png" else _jpeg_size(data)
    if w <= 0 or h <= 0 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise QAError("EVIDENCE_DIMENSIONS", f"The image is {w} x {h} pixels; at most {MAX_SIDE} per side and "
                      f"{MAX_PIXELS // 1_000_000} megapixels are accepted.", status=422)
    return {"kind": kind, "width": w, "height": h}


def strip_metadata(data: bytes, kind: str) -> bytes:
    if kind == "png":
        out, i = [PNG_SIG], 8
        while i + 8 <= len(data):
            length = struct.unpack(">I", data[i:i + 4])[0]
            ctype = data[i + 4:i + 8]
            end = i + 12 + length
            if end > len(data):
                break
            if ctype in PNG_KEEP:
                out.append(data[i:end])
            i = end
            if ctype == b"IEND":
                break
        return b"".join(out)
    out_j, i = [b"\xff\xd8"], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return data  # unusual layout: keep the bytes as they are rather than damage the image
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: the rest is image data
            out_j.append(data[i:])
            return b"".join(out_j)
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        seg = data[i:i + 2 + length]
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):  # APP1..APP15 (EXIF, XMP, ...) and comments go
            out_j.append(seg)
        i += 2 + length
    return data


def safe_name(name: str | None) -> str:
    base = os.path.basename(str(name or "screenshot")).replace("\x00", "")
    base = re.sub(r"[^\w.\- ()]+", "_", base)[:120].strip(" .")
    return base or "screenshot"


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folder(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", project_id):
            raise QAError("CMP_PROJECT_INVALID", "Invalid project id.", status=422)
        path = self.root / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, project_id: str, data: bytes) -> dict[str, Any]:
        info = check_image(data)
        clean = strip_metadata(data, info["kind"])
        eid = "EV-" + uuid.uuid4().hex[:12].upper()
        target = self.folder(project_id) / f"{eid}.{'png' if info['kind'] == 'png' else 'jpg'}"
        target.write_bytes(clean)
        try:
            os.chmod(target, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)  # the original is never changed
        except OSError:
            pass
        return {"id": eid, "file": target.name, "sha256": hashlib.sha256(clean).hexdigest(), **info,
                "bytes": len(clean)}

    def path(self, project_id: str, file: str) -> Path:
        if not re.fullmatch(r"EV-[0-9A-F]{12}(\.annotated)?\.(png|jpg|svg)", file):
            raise QAError("EVIDENCE_NOT_FOUND", "Evidence not found.", status=404)
        p = self.folder(project_id) / file
        if not p.is_file():
            raise QAError("EVIDENCE_NOT_FOUND", "Evidence not found.", status=404)
        return p

    def verify(self, project_id: str, file: str, sha256: str) -> bool:
        return hashlib.sha256(self.path(project_id, file).read_bytes()).hexdigest() == sha256

    def annotate(self, project_id: str, file: str, width: int, height: int, boxes: list[dict[str, Any]]) -> str:
        """An SVG copy of the screenshot with highlight boxes ({x, y, w, h, label, color}). Returns its file name."""
        src = self.path(project_id, file)
        mime = "image/png" if src.suffix == ".png" else "image/jpeg"
        b64 = base64.b64encode(src.read_bytes()).decode()
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                 f'width="{int(width)}" height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}">',
                 f'<image x="0" y="0" width="{int(width)}" height="{int(height)}" href="data:{mime};base64,{b64}"/>']
        for b in boxes[:200]:
            color = b.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(b.get("color") or "")) else "#e5484d"
            x, y, w, h = (float(b.get(k) or 0) for k in ("x", "y", "w", "h"))
            parts.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" fill="none" '
                         f'stroke="{color}" stroke-width="3"/>')
            if b.get("label"):
                parts.append(f'<text x="{x:.0f}" y="{max(12, y - 4):.0f}" font-family="Segoe UI, Arial" '
                             f'font-size="14" fill="{color}">{html.escape(str(b["label"])[:80])}</text>')
        parts.append("</svg>")
        name = src.stem + ".annotated.svg"
        out = src.with_name(name)
        if out.exists():
            os.chmod(out, stat.S_IWRITE | stat.S_IREAD)
        out.write_text("".join(parts), encoding="utf-8")
        return name
