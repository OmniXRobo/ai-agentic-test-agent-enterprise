"""SkyGuard 23.13.2: Arabic replies spoken offline for the browser extension's companion (standard library only).

The companion has no Windows Arabic voice either, so it uses the same offline Piper voice that SkyGuard QA uses
(INSTALL_ARABIC_VOICE.cmd). It looks for runtime\\piper in, in order: SKYGUARD_PIPER_DIR / QA_PIPER_DIR, the
companion's own folder, the default SkyGuard QA folder (%LOCALAPPDATA%\\Programs\\AIAgenticTestAgent) and
%LOCALAPPDATA%\\SkyGuardQA\\app-*. Text goes to Piper on stdin, never on the command line.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

FEMALE = ("amira", "hoda", "salma", "female", "woman", "laila", "layla", "fatima", "noor")
PITCH_UP = 1.32  # a male voice moved into the female range (Piper has no female Arabic voice)


def piper_homes(extra: list[Path] | None = None) -> list[Path]:
    out = [Path(v) for v in (os.environ.get("SKYGUARD_PIPER_DIR"), os.environ.get("QA_PIPER_DIR")) if v]
    out += list(extra or [])
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out += [Path(local) / "SkyGuardAgent" / "runtime" / "piper",
                Path(local) / "Programs" / "AIAgenticTestAgent" / "app" / "runtime" / "piper",
                Path(local) / "Programs" / "AIAgenticTestAgent" / "runtime" / "piper"]
        out += sorted((Path(local) / "SkyGuardQA").glob("app-*/runtime/piper"), reverse=True)
    return out


def find_voice(gender: str = "male", extra: list[Path] | None = None) -> dict[str, Any] | None:
    """{"exe", "model", "pitched"} for an Arabic voice of this gender, or None."""
    names = ("piper.exe",) if sys.platform == "win32" else ("piper", "piper.exe")
    for home in piper_homes(extra):
        exe = next((home / n for n in names if (home / n).is_file()), None)
        if not exe:
            continue
        models = [m for folder in (home / "voices", home) if folder.is_dir() for m in sorted(folder.glob("ar_*.onnx"))
                  if m.with_suffix(".onnx.json").is_file()]
        if not models:
            continue
        female = [m for m in models if any(w in m.stem.lower() for w in FEMALE)]
        male = [m for m in models if m not in female]
        if gender == "female":
            if female:
                return {"exe": exe, "model": female[0], "pitched": False}
            return {"exe": exe, "model": models[0], "pitched": True}
        return {"exe": exe, "model": (male or models)[0], "pitched": False}
    return None


def render_arabic(text: str, gender: str = "male", rate: float = 1.0, extra: list[Path] | None = None) -> bytes:
    """WAV bytes of the Arabic text. RuntimeError when no Arabic voice is installed or Piper fails."""
    v = find_voice(gender, extra)
    if not v:
        raise RuntimeError("No Arabic voice is installed. Run INSTALL_ARABIC_VOICE.cmd from the SkyGuard QA folder.")
    length = max(0.5, min(2.0, 1.0 / max(rate, 0.1))) * (PITCH_UP if v["pitched"] else 1.0)
    kw: dict[str, Any] = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "reply.wav"
        try:
            done = subprocess.run([str(v["exe"]), "--model", str(v["model"]), "--output_file", str(out),
                                   "--length_scale", f"{length:.2f}"], input=" ".join(text.split())[:600].encode("utf-8"),
                                  capture_output=True, timeout=60, cwd=str(v["exe"].parent), check=False, **kw)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"The Arabic voice did not respond ({type(exc).__name__}).") from None
        if done.returncode != 0 or not out.is_file() or out.stat().st_size < 100:
            raise RuntimeError("The Arabic voice could not speak this reply.")
        data = out.read_bytes()
    return pitch_up(data, PITCH_UP) if v["pitched"] else data


def pitch_up(wav_bytes: bytes, factor: float) -> bytes:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(int(round(params.framerate * factor)))
        w.writeframes(frames)
    return buf.getvalue()


def status(extra: list[Path] | None = None) -> dict[str, Any]:
    m, f = find_voice("male", extra), find_voice("female", extra)
    return {"available": m is not None, "male": m and m["model"].stem,
            "female": f and (f["model"].stem + (" (higher pitch)" if f["pitched"] else "")),
            "hint": "" if m else "Run INSTALL_ARABIC_VOICE.cmd from the SkyGuard QA folder to install the Arabic voice."}


__all__ = ["find_voice", "render_arabic", "status", "piper_homes"]
