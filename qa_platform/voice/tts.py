"""Spoken replies rendered with the PC's own Windows voices (System.Speech, offline).

The dashboard plays the WAV through a radio filter, squelch and static, so the
reply sounds like a pilot on the radio. Browsers cannot filter their own speech
output, which is why the audio is rendered here. On PCs without Windows voices
the dashboard falls back to the browser's installed voices.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from ..errors import QAError
from ..logging_setup import get_logger
from .piper import PiperVoice

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
# 23.13.0: Windows 10 / 11 install the voices of a language pack (Arabic: Hoda, Naayf) as "OneCore" voices, which
# System.Speech does not see. Arabic OneCore voices are listed too and spoken through Windows.Media.SpeechSynthesis.
_VOICES = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($v in $s.GetInstalledVoices()) { if ($v.Enabled) { $i = $v.VoiceInfo; "$($i.Name)|$($i.Gender)|$($i.Culture.Name)|desktop" } }
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
  foreach ($v in [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices) { "$($v.DisplayName)|$($v.Gender)|$($v.Language)|onecore" }
} catch { }
"""
_SPEAK_ONECORE = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and
  $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$s = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$v = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.DisplayName -eq $env:QA_TTS_VOICE } | Select-Object -First 1
if (-not $v) { throw "voice not found" }
$s.Voice = $v
try { $s.Options.SpeakingRate = [double]$env:QA_TTS_SPEED } catch { }
$task = $asTask.MakeGenericMethod([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]).Invoke($null, @($s.SynthesizeTextToStreamAsync($env:QA_TTS_TEXT)))
if (-not $task.Wait(30000)) { throw "timeout" }
$stream = $task.Result
$in = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($stream.GetInputStreamAt(0))
$out = [System.IO.File]::Create($env:QA_TTS_OUT)
try { $in.CopyTo($out) } finally { $out.Dispose(); $in.Dispose(); $stream.Dispose(); $s.Dispose() }
"""


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def parse_voices(lines: list[str]) -> list[dict[str, str]]:
    """'name|gender|lang|source' lines. OneCore voices are kept only for Arabic (the English list stays as before)."""
    out: list[dict[str, str]] = []
    for line in lines:
        parts = line.strip().split("|")
        if len(parts) == 3:
            parts.append("desktop")
        if len(parts) != 4 or not parts[0]:
            continue
        v = dict(zip(("name", "gender", "lang", "source"), parts))
        if v["source"] == "onecore":
            if not v["lang"].lower().startswith("ar"):
                continue
            if any(o["name"] in (v["name"], v["name"] + " Desktop") for o in out):
                continue
        out.append(v)
    return out


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    return shutil.which("powershell.exe") or shutil.which("powershell")


class SystemVoice:
    def __init__(self, cache_dir: Path, piper: PiperVoice | None = None) -> None:
        self.cache_dir = cache_dir
        self.piper = piper or PiperVoice(cache_dir.parent)
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
                self._voices = parse_voices(lines)
        return self._voices

    def available(self) -> bool:
        return bool(self.voices()) or bool(self.piper.arabic_voices())

    # ---- Arabic replies: male or female, as chosen -------------------------------------------------------------

    def arabic_gender(self, voice: str = "", gender: str = "") -> str:
        """"Male" or "Female": the Arabic voice setting, else the gender of the chosen radio voice, else male."""
        g = (gender or "").strip().lower()
        if g in ("male", "female"):
            return g.title()
        chosen = next((v for v in self.voices() if v["name"] == voice), None)
        return "Female" if (chosen or {}).get("gender", "").lower() == "female" else "Male"

    def arabic_choice(self, gender: str = "Male") -> dict[str, str] | None:
        """The voice that speaks an Arabic reply in this gender: a Windows Arabic voice of that gender, an offline
        Piper voice of that gender (for female: Kareem at a higher pitch when no female model is installed), then
        any Arabic voice. None when no Arabic voice is installed."""
        win = [v for v in self.voices() if v.get("lang", "").lower().startswith("ar")]
        same = next((v for v in win if v.get("gender") == gender), None)
        if same:
            return {"engine": "windows", "voice": same["name"], "label": same["name"], "gender": gender}
        pv = self.piper.arabic_voice(gender)
        if pv and (pv["gender"] == gender):
            return {"engine": "piper", "voice": pv["name"], "label": pv.get("label") or pv["name"], "gender": gender}
        if win:
            return {"engine": "windows", "voice": win[0]["name"], "label": win[0]["name"], "gender": win[0].get("gender", "")}
        if pv:
            return {"engine": "piper", "voice": pv["name"], "label": pv.get("label") or pv["name"], "gender": pv["gender"]}
        return None

    def arabic_status(self, voice: str = "", gender: str = "") -> dict[str, Any]:
        """Which voice speaks Arabic replies now, and for each gender."""
        g = self.arabic_gender(voice, gender)
        now = self.arabic_choice(g)
        out: dict[str, Any] = {"available": now is not None, "gender": g, "engine": (now or {}).get("engine", ""),
                               "voice": (now or {}).get("label", ""),
                               "male": self.arabic_choice("Male"), "female": self.arabic_choice("Female")}
        if not now:
            out["hint"] = "No Arabic voice installed. Run INSTALL_ARABIC_VOICE.cmd from the SkyGuard QA folder."
        return out

    def render(self, text: str, voice: str = "", rate: float = 1.0, gender: str = "") -> bytes:
        """WAV bytes for the text. Renders are cached, so repeated phrases ("Roger, stand by") play at once."""
        text = " ".join(text.split())[:600]
        if not text:
            raise QAError("VALIDATION_FAILED", "Nothing to say.", status=422)
        if voice and voice not in {v["name"] for v in self.voices()}:
            voice = ""
        if is_arabic(text):
            # An Arabic reply always gets an Arabic voice of the chosen gender; an English voice never reads it.
            g = self.arabic_gender(voice, gender)
            choice = self.arabic_choice(g)
            if not choice:
                raise QAError("VOICE_ARABIC_MISSING", "No Arabic voice is installed. Run INSTALL_ARABIC_VOICE.cmd from "
                              "the SkyGuard QA folder (the reply stays on screen).", status=409)
            if choice["engine"] == "piper":
                return self._render_piper(text, rate, g)
            voice = choice["voice"]
        sapi_rate = max(-10, min(10, round((rate - 1) * 10)))
        key = hashlib.sha256(f"{voice}|{sapi_rate}|{text}".encode()).hexdigest()[:32]
        target = self.cache_dir / f"{key}.wav"
        if target.exists():
            return target.read_bytes()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part.wav")
        onecore = next((v for v in self.voices() if v["name"] == voice and v.get("source") == "onecore"), None)
        with self._lock:
            if onecore:
                self._run(_SPEAK_ONECORE, env={"QA_TTS_TEXT": text, "QA_TTS_VOICE": voice, "QA_TTS_OUT": str(partial),
                                               "QA_TTS_SPEED": f"{max(0.5, min(3.0, rate)):.2f}"})
            else:
                self._run(_SPEAK, env={"QA_TTS_TEXT": text, "QA_TTS_VOICE": voice, "QA_TTS_RATE": str(sapi_rate),
                                       "QA_TTS_OUT": str(partial)})
            partial.replace(target)
            self._prune()
        return target.read_bytes()

    def _is_arabic_voice(self, voice: str) -> bool:
        v = next((v for v in self.voices() if v["name"] == voice), None)
        return bool(v and v.get("lang", "").lower().startswith("ar"))

    def _render_piper(self, text: str, rate: float, gender: str = "Male") -> bytes:
        pv = self.piper.arabic_voice(gender)
        if not pv:
            raise QAError("VOICE_ARABIC_MISSING", "No Arabic voice is installed. Run INSTALL_ARABIC_VOICE.cmd from "
                          "the SkyGuard QA folder (the reply stays on screen).", status=409)
        key = hashlib.sha256(f"piper|{pv['name']}|{pv.get('pitched', '')}|{rate:.2f}|{text}".encode()).hexdigest()[:32]
        target = self.cache_dir / f"{key}.wav"
        if target.exists():
            return target.read_bytes()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part.wav")
        with self._lock:
            self.piper.render(text, partial, rate, gender)
            partial.replace(target)
            self._prune()
        return target.read_bytes()

    def voice_for(self, text: str, voice: str = "") -> str:
        """23.13.0: the voice for this reply. An Arabic reply is spoken by an installed Arabic voice (Windows
        "Arabic" language pack: Naayf, Hoda ...) of the same gender as the chosen voice (male by default); other
        replies keep the chosen voice. Without an Arabic voice the chosen one is used (the text stays on screen)."""
        if not is_arabic(text):
            return voice
        voices = self.voices()
        chosen = next((v for v in voices if v["name"] == voice), None)
        if chosen and chosen.get("lang", "").lower().startswith("ar"):
            return voice
        gender = (chosen or {}).get("gender") or "Male"
        arabic = [v for v in voices if v.get("lang", "").lower().startswith("ar")]
        pick = next((v for v in arabic if v.get("gender") == gender), None) or (arabic[0] if arabic else None)
        return pick["name"] if pick else voice

    def _prune(self) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for old in files[:-MAX_CACHE_FILES]:
            old.unlink(missing_ok=True)
