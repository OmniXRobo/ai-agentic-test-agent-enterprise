"""23.13.0: offline Arabic spoken replies with Piper (https://github.com/rhasspy/piper, MIT).

Windows only ships English voices on most office PCs (David, Zira, Mark), and an Arabic Windows voice needs a
language pack that an offline PC cannot download. Piper is a small offline text-to-speech program; with an Arabic
voice model (default: ar_JO-kareem-medium, male) it speaks the Arabic replies. Layout, installed by
INSTALL_ARABIC_VOICE.cmd:

  runtime\\piper\\piper.exe                       (+ espeak-ng-data, onnxruntime.dll ... from the Piper zip)
  runtime\\piper\\voices\\ar_JO-kareem-medium.onnx  (+ .onnx.json)

Text goes to Piper on stdin, never on the command line.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config import APP_ROOT
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("voice.piper")


class PiperVoice:
    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root

    def homes(self) -> list[Path]:
        env = os.environ.get("QA_PIPER_DIR")
        out = [Path(env)] if env else []
        out += [APP_ROOT / "runtime" / "piper", APP_ROOT.parent / "runtime" / "piper"]
        if self.data_root:
            out.append(self.data_root / "piper")
        return out

    def exe(self) -> Path | None:
        names = ("piper.exe",) if sys.platform == "win32" else ("piper", "piper.exe")
        for h in self.homes():
            for base in (h, h / "piper"):
                for n in names:
                    if (base / n).is_file():
                        return base / n
        return None

    def voices(self) -> list[dict[str, str]]:
        """Installed voice models: name, language ("ar" ...), gender guess and path."""
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for h in self.homes():
            for folder in (h / "voices", h):
                if not folder.is_dir():
                    continue
                for model in sorted(folder.glob("*.onnx")):
                    cfg = model.with_suffix(".onnx.json")
                    if model.stem in seen or not cfg.is_file():
                        continue
                    lang = model.stem.split("_")[0].lower()
                    try:
                        meta = json.loads(cfg.read_text(encoding="utf-8"))
                        lang = str((meta.get("language") or {}).get("family") or lang).lower()
                    except (OSError, ValueError, AttributeError):
                        pass
                    seen.add(model.stem)
                    out.append({"name": model.stem, "lang": lang, "path": str(model),
                                "gender": "Female" if any(w in model.stem.lower() for w in FEMALE) else "Male"})
        return out

    def arabic_voice(self) -> dict[str, str] | None:
        if not self.exe():
            return None
        arabic = [v for v in self.voices() if v["lang"].startswith("ar")]
        return next((v for v in arabic if v["gender"] == "Male"), None) or (arabic[0] if arabic else None)

    def render(self, text: str, out: Path, rate: float = 1.0) -> None:
        exe, voice = self.exe(), self.arabic_voice()
        if not exe or not voice:
            raise QAError("VOICE_ARABIC_MISSING", "No Arabic voice is installed. Run INSTALL_ARABIC_VOICE.cmd.", status=409)
        length = max(0.5, min(2.0, 1.0 / max(rate, 0.1)))
        kw: dict[str, Any] = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
        try:
            done = subprocess.run([str(exe), "--model", voice["path"], "--output_file", str(out),
                                   "--length_scale", f"{length:.2f}"], input=text.encode("utf-8"),
                                  capture_output=True, timeout=60, cwd=str(exe.parent), check=False, **kw)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QAError("VOICE_TTS_FAILED", "The Arabic voice did not respond.", status=502,
                          details=type(exc).__name__) from exc
        if done.returncode != 0 or not out.is_file() or out.stat().st_size < 100:
            raise QAError("VOICE_TTS_FAILED", "The Arabic voice could not speak this reply.", status=502,
                          details=(done.stderr or b"").decode("utf-8", "replace")[-300:])


FEMALE = ("amira", "hoda", "salma", "female", "woman", "zira", "laila", "layla", "fatima", "noor")
