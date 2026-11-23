"""Builds SkyGuard_Arabic_Speech_Kit.zip: the add-on that lets SkyGuard QA understand spoken Arabic.

    python packaging/arabic-speech-kit/build_kit.py [--out dist/arabic-speech-kit]

The kit holds no model (it is downloaded on a PC with Internet by GET_ARABIC_SPEECH_MODEL.cmd); it is a few small
scripts, the same GET_FASTER_WHISPER_MODEL.ps1 and INSTALL_SPEECH_MODEL.ps1 as the application folder, and
SHA256SUMS.txt. Windows line endings for .cmd, .ps1 and .txt (cmd.exe misreads a .cmd with Unix line endings), and
a UTF-8 BOM on README_FIRST.txt (Arabic examples).
"""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = Path(__file__).resolve().parent / "kit"
NAME = "SkyGuard_Arabic_Speech_Kit"
FILES = {  # name in the kit -> source
    "README_FIRST.txt": KIT / "README_FIRST.txt",
    "GET_ARABIC_SPEECH_MODEL.cmd": KIT / "GET_ARABIC_SPEECH_MODEL.cmd",
    "INSTALL_ARABIC_SPEECH_MODEL.cmd": KIT / "INSTALL_ARABIC_SPEECH_MODEL.cmd",
    "INSTALL_ARABIC_SPEECH_MODEL.ps1": KIT / "INSTALL_ARABIC_SPEECH_MODEL.ps1",
    "set_speech_language.py": KIT / "set_speech_language.py",
    "GET_FASTER_WHISPER_MODEL.ps1": ROOT / "GET_FASTER_WHISPER_MODEL.ps1",
    "INSTALL_SPEECH_MODEL.ps1": ROOT / "INSTALL_SPEECH_MODEL.ps1",
}


def content(name: str, src: Path) -> bytes:
    data = src.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if name.endswith((".cmd", ".ps1", ".txt")):
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if name == "README_FIRST.txt":
        data = b"\xef\xbb\xbf" + data
    elif name.endswith((".cmd", ".ps1")):
        # Windows PowerShell 5.1 reads a file without a BOM as the ANSI code page: keep the scripts plain ASCII
        data.decode("ascii")
    return data


def build(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    blobs = {n: content(n, s) for n, s in FILES.items()}
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\r\n" for n, b in sorted(blobs.items()))
    blobs["SHA256SUMS.txt"] = sums.encode("ascii")
    zpath = out / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in blobs.items():
            info = zipfile.ZipInfo(f"{NAME}/{n}", date_time=(2026, 11, 23, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, b)
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (out / f"{NAME}.zip.sha256").write_text(f"{digest}  {NAME}.zip\n", encoding="ascii")
    return zpath


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "arabic-speech-kit"))
    z = build(Path(ap.parse_args().out))
    print(f"{z} ({z.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
