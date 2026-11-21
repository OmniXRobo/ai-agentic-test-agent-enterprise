"""23.13.2: the AI Agent page and the browser extension speak their replies, in English for an English request and in
Arabic (the offline Arabic voice) for an Arabic one."""

from __future__ import annotations

import json
import stat
import sys
import textwrap
import wave
import io
from pathlib import Path

import pytest

from skyguard_agent import speech
from skyguard_agent.translate import ARABIC_LINES, spoken_reply

ROOT = Path(__file__).resolve().parent.parent

FAKE_PIPER = textwrap.dedent(f"""\
    #!{sys.executable}
    import struct, sys, pathlib
    a = sys.argv[1:]
    out = pathlib.Path(a[a.index("--output_file") + 1])
    sys.stdin.buffer.read()
    data = b"\\x00\\x00" * 4000
    out.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
                    + b"data" + struct.pack("<I", len(data)) + data)
""")


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def chat(self, messages, max_tokens=0):
        self.calls += 1
        return self.answer


def test_english_request_gets_an_english_spoken_reply_without_the_address():
    r = spoken_reply({"type": "navigate", "text": "Opening the booking page: https://shop.example.ae/booking?x=1"}, False)
    assert r == {"speak": "Opening the booking page:", "language": "en"}


def test_arabic_request_gets_arabic_from_the_local_ai_or_a_short_arabic_line():
    llm = FakeLLM("حاضر، فتحت صفحة الحجز.")
    assert spoken_reply({"type": "navigate", "text": "Opening the booking page."}, True, llm) == {
        "speak": "حاضر، فتحت صفحة الحجز.", "language": "ar"}
    # the model is off, or answers in English: a short Arabic line for the kind of reply
    assert spoken_reply({"type": "run", "text": "Preparing the preview."}, True, None)["speak"] == ARABIC_LINES["run"]
    assert spoken_reply({"type": "records", "text": "3 records."}, True, FakeLLM("3 records"))["speak"] == ARABIC_LINES["records"]
    assert spoken_reply({"type": "unknown", "text": "x"}, True)["language"] == "ar"


def test_ai_agent_api_returns_the_line_to_speak(client):
    for text, lang in (("open the booking page", "en"), ("افتح صفحة الحجز", "ar")):
        r = client.post("/api/ops/simple/command", json={"text": text, "source": "voice"})
        assert r.status_code == 200, r.text
        reply = r.json()["reply"]
        assert reply["language"] == lang and reply["speak"]
        if lang == "ar":
            assert any("؀" <= ch <= "ۿ" for ch in reply["speak"]) and reply["translated_to"]


@pytest.mark.skipif(sys.platform == "win32", reason="the fake piper is a Python script")
def test_companion_renders_arabic_male_and_female(tmp_path, monkeypatch):
    home = tmp_path / "piper"
    (home / "voices").mkdir(parents=True)
    exe = home / "piper"
    exe.write_text(FAKE_PIPER)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (home / "voices" / "ar_JO-kareem-medium.onnx").write_bytes(b"x")
    (home / "voices" / "ar_JO-kareem-medium.onnx.json").write_text(json.dumps({"language": {"family": "ar"}}))
    monkeypatch.setenv("SKYGUARD_PIPER_DIR", str(home))
    male = speech.render_arabic("حاضر", "male")
    female = speech.render_arabic("حاضر", "female")
    rate = lambda b: wave.open(io.BytesIO(b)).getframerate()  # noqa: E731
    assert rate(male) == 16000 and rate(female) == round(16000 * speech.PITCH_UP)
    st = speech.status()
    assert st["available"] and st["male"] == "ar_JO-kareem-medium" and "higher pitch" in st["female"]


def test_companion_without_an_arabic_voice_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYGUARD_PIPER_DIR", str(tmp_path / "none"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="INSTALL_ARABIC_VOICE"):
        speech.render_arabic("حاضر")
    assert speech.status()["available"] is False


def test_pages_speak_the_reply():
    page = (ROOT / "web" / "js" / "pages" / "ai-agent.js").read_text(encoding="utf-8")
    assert 'if (source === "voice") sayReply(out.reply);' in page and "speak(line" in page
    side = (ROOT / "browser-extension" / "extension" / "sidepanel.js").read_text(encoding="utf-8")
    assert "if (spoken) sayReply(r);" in side and "api.speakArabic" in side
    assert "return false; // never an English voice for Arabic text" in side
    server = (ROOT / "browser-extension" / "companion" / "skyguard_companion" / "server.py").read_text(encoding="utf-8")
    assert '"/v1/voice/speak"' in server and "spoken_reply" in server
    assert "SkyGuardAgent" in (ROOT / "INSTALL_ARABIC_VOICE.ps1").read_text()
