"""Spoken replies rendered with the PC's own Windows voices (System.Speech, offline).

The dashboard plays the WAV through a radio filter, squelch and static, so the
reply sounds like a pilot on the radio. Browsers cannot filter their own speech
output, which is why the audio is rendered here. On PCs without Windows voices
the dashboard falls back to the browser's installed voices.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from ..errors import QAError
from ..logging_setup import get_logger

log = get_logger("voice.tts")

MAX_CACHE_FILES = 200
# Text, voice and output path come in through environment variables, never through the command line.
_SPEAK = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:QA_TTS_VOICE) { $s.SelectVoice($env:QA_TTS_VOICE) }
else { try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male, [System.Speech.Synthesis.VoiceAge]::Adult) } catch {} }
$s.Rate = [int]$env:QA_TTS_RATE
$text = [System.Security.SecurityElement]::Escape($env:QA_TTS_TEXT)
$lang = $s.Voice.Culture.Name
$ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='$lang'><prosody pitch='low'>$text</prosody></speak>"
$s.SetOutputToWaveFile($env:QA_TTS_OUT)
$s.SpeakSsml($ssml)
$s.Dispose()
"""
_VOICES = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled) { $i = $v.VoiceInfo; "$($i.Name)|$($i.Gender)|$($i.Culture.Name)" } }
"""


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    return shutil.which("powershell.exe") or shutil.which("powershell")


class SystemVoice:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._voices: list[dict[str, str]] | None = None
        self._lock = threading.Lock()

    def _run(self, script: str, env: dict[str, str] | None = None, timeout: float = 30) -> str:
        exe = _powershell()
        if not exe:
            raise QAError("VOICE_TTS_UNAVAILABLE", "Windows voices are not available on this system.", status=409)
        kw: dict[str, Any] = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
        try:
            out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                                 capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})}, **kw)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QAError("VOICE_TTS_FAILED", "The Windows voice did not respond.", status=502,
                          details=type(exc).__name__) from exc
        if out.returncode != 0:
            raise QAError("VOICE_TTS_FAILED", "The Windows voice could not speak this reply.", status=502,
                          details=(out.stderr or "")[-300:])
        return out.stdout

    def voices(self) -> list[dict[str, str]]:
        if self._voices is None:
            if not _powershell():
                self._voices = []
            else:
                try:
                    lines = self._run(_VOICES, timeout=20).splitlines()
                except QAError as exc:
                    log.warning(f"Windows voices unavailable: {exc.message}")
                    lines = []
                self._voices = [dict(zip(("name", "gender", "lang"), line.strip().split("|", 2)))
                                for line in lines if line.count("|") == 2]
        return self._voices

    def available(self) -> bool:
        return bool(self.voices())

    def render(self, text: str, voice: str = "", rate: float = 1.0) -> bytes:
        """WAV bytes for the text. Renders are cached, so repeated phrases ("Roger, stand by") play at once."""
        text = " ".join(text.split())[:600]
        if not text:
            raise QAError("VALIDATION_FAILED", "Nothing to say.", status=422)
        if voice and voice not in {v["name"] for v in self.voices()}:
            voice = ""
        sapi_rate = max(-10, min(10, round((rate - 1) * 10)))
        key = hashlib.sha256(f"{voice}|{sapi_rate}|{text}".encode()).hexdigest()[:32]
        target = self.cache_dir / f"{key}.wav"
        if target.exists():
            return target.read_bytes()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part.wav")
        with self._lock:
            self._run(_SPEAK, env={"QA_TTS_TEXT": text, "QA_TTS_VOICE": voice, "QA_TTS_RATE": str(sapi_rate),
                                   "QA_TTS_OUT": str(partial)})
            partial.replace(target)
            self._prune()
        return target.read_bytes()

    def _prune(self) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for old in files[:-MAX_CACHE_FILES]:
            old.unlink(missing_ok=True)
