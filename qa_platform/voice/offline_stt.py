"""Built-in offline speech recognition (Vosk / Kaldi).

The offline Windows package bundles a small English model in ``runtime/speech``,
so the microphone works on a PC without Internet and without a separate speech
server. A model can also be placed in ``<data>/speech-model`` or named in Voice
settings (``stt_model_dir``). The model is loaded once, on first use.
"""

from __future__ import annotations

import io
import json
import threading
import wave
from pathlib import Path
from typing import Any

from ..config import APP_ROOT, Settings
from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("voice.stt")

RATE = 16000


def _is_model(folder: Path) -> bool:
    return folder.is_dir() and (folder / "am").is_dir() and (folder / "conf").is_dir()


def _find_model(folder: Path) -> Path | None:
    if _is_model(folder):
        return folder
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            if _is_model(child):
                return child
    return None


class OfflineRecognizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any = None
        self._model_path: Path | None = None
        self._lock = threading.Lock()

    def candidates(self) -> list[Path]:
        configured = (self.settings.section("voice").get("stt_model_dir") or "").strip()
        out = [Path(configured).expanduser()] if configured else []
        return out + [APP_ROOT / "runtime" / "speech", self.settings.path.parent / "speech-model"]

    def model_dir(self) -> Path | None:
        for folder in self.candidates():
            found = _find_model(folder)
            if found:
                return found
        return None

    @staticmethod
    def engine_installed() -> bool:
        try:
            import vosk  # noqa: F401
        except Exception:  # missing package or its native library
            return False
        return True

    def status(self) -> tuple[str, str]:
        """(PASS | FAIL | NOT_CONFIGURED, detail) for diagnostics."""
        if not self.engine_installed():
            return "FAIL", "The offline speech engine (vosk) is not installed in this Python."
        model = self.model_dir()
        if not model:
            where = " or ".join(str(p) for p in self.candidates())
            return "NOT_CONFIGURED", f"No speech model found. The offline package includes one; otherwise unzip a Vosk model into {where}."
        return "PASS", f"Offline speech model: {model.name}. Nothing leaves this PC."

    def _load(self) -> Any:
        model_dir = self.model_dir()
        if not model_dir:
            raise QAError("VOICE_MODEL_MISSING", "The built-in speech model was not found. Run the offline package "
                                                 "installer again, or put a Vosk model in the data folder's speech-model folder.",
                          status=409)
        if not self.engine_installed():
            raise QAError("VOICE_ENGINE_MISSING", "The offline speech engine (vosk) is not installed.", status=409)
        with self._lock:
            if self._model is None or self._model_path != model_dir:
                import vosk

                vosk.SetLogLevel(-1)
                log.info(f"Loading offline speech model {model_dir}")
                self._model, self._model_path = vosk.Model(str(model_dir)), model_dir
            return self._model

    def transcribe(self, audio: bytes) -> str:
        """16 kHz mono 16-bit WAV (what the dashboard records) -> text."""
        model = self._load()
        try:
            with wave.open(io.BytesIO(audio)) as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise QAError("VOICE_AUDIO_FORMAT", "Send 16-bit mono WAV audio.", status=422)
                rate, frames = w.getframerate(), w.readframes(w.getnframes())
        except (wave.Error, EOFError) as exc:
            raise QAError("VOICE_AUDIO_FORMAT", "The recording is not a WAV file.", status=422) from exc
        import vosk

        rec = vosk.KaldiRecognizer(model, rate)
        for i in range(0, len(frames), rate):  # half a second of 16-bit samples at a time
            rec.AcceptWaveform(frames[i:i + rate])
        return str(json.loads(rec.FinalResult()).get("text") or "").strip()
