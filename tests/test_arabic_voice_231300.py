"""23.13.0: Arabic spoken replies offline (Piper), and Arabic wake names / commands as the recognizer writes them."""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from qa_platform.errors import QAError
from qa_platform.voice.piper import PiperVoice
from qa_platform.voice.tts import SystemVoice
from skyguard_agent.translate import by_words, name_sound, strip_arabic_wake

ROOT = Path(__file__).resolve().parent.parent
ARABIC_REPLY = "تم. جاري فتح الصفحة."

FAKE_PIPER = textwrap.dedent(f"""\
    #!{sys.executable}
    # A stand-in for piper: writes a WAV and records its stdin and arguments.
    import json, struct, sys, pathlib
    args = sys.argv[1:]
    out = pathlib.Path(args[args.index("--output_file") + 1])
    text = sys.stdin.buffer.read().decode("utf-8")
    pathlib.Path(__file__).with_name("calls.jsonl").open("a", encoding="utf-8").write(
        json.dumps({{"args": args, "text": text}}, ensure_ascii=False) + "\\n")
    data = b"\\x00\\x00" * 8000
    out.write_bytes(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
                    + b"data" + struct.pack("<I", len(data)) + data)
""")


@pytest.fixture()
def piper_home(tmp_path, monkeypatch):
    home = tmp_path / "runtime" / "piper"
    (home / "voices").mkdir(parents=True)
    exe = home / ("piper.exe" if sys.platform == "win32" else "piper")
    exe.write_text(FAKE_PIPER, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (home / "voices" / "ar_JO-kareem-medium.onnx").write_bytes(b"onnx")
    (home / "voices" / "ar_JO-kareem-medium.onnx.json").write_text(json.dumps({"language": {"family": "ar"}}))
    monkeypatch.setenv("QA_PIPER_DIR", str(home))
    return home


def calls(home: Path) -> list[dict]:
    f = home / "calls.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


@pytest.mark.skipif(sys.platform == "win32", reason="the fake piper is a Python script")
def test_arabic_reply_is_spoken_by_the_offline_arabic_voice(tmp_path, piper_home):
    v = SystemVoice(tmp_path / "cache", PiperVoice())
    v._voices = [{"name": "Microsoft David Desktop", "gender": "Male", "lang": "en-US"}]  # the office PC: English only
    st = v.arabic_status("Microsoft David Desktop")
    assert (st["available"], st["engine"], st["voice"], st["gender"]) == (True, "piper", "ar_JO-kareem-medium", "Male")
    wav = v.render(ARABIC_REPLY, "Microsoft David Desktop", rate=1.25)
    assert wav[:4] == b"RIFF"
    (call,) = calls(piper_home)
    assert call["text"] == ARABIC_REPLY  # on stdin, never on the command line
    assert ARABIC_REPLY not in " ".join(call["args"])
    assert call["args"][call["args"].index("--length_scale") + 1] == "0.80"
    assert call["args"][call["args"].index("--model") + 1].endswith("ar_JO-kareem-medium.onnx")
    assert v.render(ARABIC_REPLY, "Microsoft David Desktop", rate=1.25) == wav and len(calls(piper_home)) == 1  # cached


def test_without_an_arabic_voice_arabic_is_never_read_by_an_english_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_PIPER_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(PiperVoice, "homes", lambda self: [tmp_path / "none"])
    v = SystemVoice(tmp_path / "cache")
    v._voices = [{"name": "Microsoft David Desktop", "gender": "Male", "lang": "en-US"}]
    with pytest.raises(QAError) as e:
        v.render(ARABIC_REPLY, "Microsoft David Desktop")
    assert e.value.error_code == "VOICE_ARABIC_MISSING" and e.value.status == 409
    st = v.arabic_status()
    assert st["available"] is False and "INSTALL_ARABIC_VOICE.cmd" in st["hint"]


def test_a_windows_arabic_voice_is_preferred_when_installed(tmp_path, piper_home, monkeypatch):
    v = SystemVoice(tmp_path / "cache", PiperVoice())
    v._voices = [{"name": "Microsoft David Desktop", "gender": "Male", "lang": "en-US"},
                 {"name": "Microsoft Naayf Desktop", "gender": "Male", "lang": "ar-SA"}]
    assert v.arabic_status()["engine"] == "windows"
    seen = {}
    monkeypatch.setattr(SystemVoice, "_run", lambda self, script, env=None, timeout=30: (
        seen.update(env), Path(env["QA_TTS_OUT"]).write_bytes(b"RIFF....WAVE"), "")[-1])
    assert v.render(ARABIC_REPLY, "Microsoft David Desktop")[:4] == b"RIFF"
    assert seen["QA_TTS_VOICE"] == "Microsoft Naayf Desktop" and calls(piper_home) == []


@pytest.mark.parametrize("heard, rest", [
    ("كابدان افتا صفا", "افتا صفا"),           # the reported case: "Captain, open page" heard badly
    ("كابتن افتح صفحة الحجز", "افتح صفحة الحجز"),
    ("يا كابتن، افتح الصفحة", "افتح الصفحة"),
    ("كبتن ورني الرحلات", "ورني الرحلات"),
    ("كابتين", ""),
])
def test_the_wake_name_in_arabic_is_recognised_in_any_spelling(heard, rest):
    assert strip_arabic_wake(heard, ["Captain"]) == rest


def test_ordinary_arabic_words_are_not_taken_for_the_wake_name():
    assert strip_arabic_wake("افتح صفحة الحجز", ["Captain"]) is None
    assert strip_arabic_wake("كتاب جديد", ["Captain"]) is None
    assert name_sound("Captain") == name_sound("كابتن") == name_sound("كابدان") == "kbtn"
    assert name_sound("Saqr") == name_sound("صقر") and name_sound("Hami") == name_sound("هامي")


def test_misheard_command_words_still_translate():
    assert by_words("افتا صفا") == "open page"
    assert by_words("افتح صفحه الحجز").startswith("open page")


def test_arabic_command_with_misheard_wake_name_reaches_the_command(client):
    rt = client.app.state.runtime
    rt.settings.update({"voice": {"agent_command_name": "Captain", "language": "ar"}})
    assert rt.voice.strip_wake_name("كابدان افتا صفا") == "افتا صفا"
    r = client.post("/api/voice/commands", json={"text": "كابدان افتح صفحة الحجز", "conversation_id": "conv-arabic-01"}).json()
    assert r.get("understood", "").startswith("open"), r
    assert r["language"] == "ar" and any("؀" <= ch <= "ۿ" for ch in r["speak"])  # answered in Arabic


def test_installed_multilingual_model_is_used_for_arabic(tmp_path, monkeypatch):
    from qa_platform.config import Paths, Settings
    from qa_platform.voice.fw_service import FasterWhisperService

    home = tmp_path / "fw"
    for name in ("small.en", "small.ar"):
        d = home / "models" / name
        d.mkdir(parents=True)
        for f in ("model.bin", "config.json", "tokenizer.json"):
            (d / f).write_text("x")
    paths = Paths(tmp_path / "d").ensure()
    settings = Settings(paths.settings_file)
    fw = FasterWhisperService(settings, paths.logs)
    monkeypatch.setattr(FasterWhisperService, "homes", lambda self: [home])
    settings.update({"voice": {"language": "ar", "fw_model": "small.en"}})
    assert fw.model_dir().name == "small.ar"
    settings.update({"voice": {"language": "en"}})
    assert fw.model_dir().name == "small.en"


def test_dashboard_plays_arabic_replies_with_the_arabic_voice():
    js = (ROOT / "web" / "js" / "voice.js").read_text(encoding="utf-8")
    assert "arabicVoice" in js and "playPlain" in js and "afterArabicName" in js
    assert "if (arabic) return same || inLang[0] || null;" in js  # no English browser voice for Arabic
    for f in ("INSTALL_ARABIC_VOICE.cmd", "INSTALL_ARABIC_VOICE.ps1", "GET_ARABIC_VOICE.ps1"):
        assert (ROOT / f).is_file()
    assert "f3c58906402b24f3a96d92145f58acba6d86c9b5db896d207f78dc80811efcea" in (ROOT / "GET_ARABIC_VOICE.ps1").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="the fake piper is a Python script")
def test_female_arabic_reply_uses_a_female_voice_or_kareem_at_a_higher_pitch(tmp_path, piper_home):
    import wave

    v = SystemVoice(tmp_path / "cache", PiperVoice())
    v._voices = [{"name": "Microsoft Zira Desktop", "gender": "Female", "lang": "en-US", "source": "desktop"}]
    st = v.arabic_status("Microsoft Zira Desktop")  # follows the radio voice: Zira is female
    assert st["gender"] == "Female" and "higher (female) pitch" in st["voice"]
    assert st["male"]["voice"] == "ar_JO-kareem-medium" and st["female"]["engine"] == "piper"
    male = v.render(ARABIC_REPLY, "Microsoft Zira Desktop", gender="male")
    female = v.render(ARABIC_REPLY, "Microsoft Zira Desktop")
    rate = lambda b: wave.open(__import__("io").BytesIO(b)).getframerate()  # noqa: E731
    assert rate(male) == 16000 and rate(female) == round(16000 * 1.32)  # played faster: a higher voice
    first, second = calls(piper_home)
    assert float(second["args"][second["args"].index("--length_scale") + 1]) > float(first["args"][first["args"].index("--length_scale") + 1])
    # a real female model, when installed, is used as it is
    for ext in (".onnx", ".onnx.json"):
        (piper_home / "voices" / f"ar_XX-amira-medium{ext}").write_text("{}" if ext.endswith("json") else "x")
    assert v.arabic_status(gender="female")["female"]["voice"] == "ar_XX-amira-medium"


def test_windows_female_arabic_voice_and_onecore_voices(tmp_path, piper_home):
    from qa_platform.voice.tts import parse_voices

    voices = parse_voices(["Microsoft David Desktop|Male|en-US|desktop", "Microsoft David|Male|en-US|onecore",
                           "Microsoft Hoda|Female|ar-EG|onecore", "Microsoft Naayf|Male|ar-SA|onecore",
                           "Microsoft Zira Desktop|Female|en-US"])
    assert [x["name"] for x in voices] == ["Microsoft David Desktop", "Microsoft Hoda", "Microsoft Naayf",
                                            "Microsoft Zira Desktop"]  # English OneCore voices stay out of the list
    v = SystemVoice(tmp_path / "cache", PiperVoice())
    v._voices = voices
    st = v.arabic_status("Microsoft David Desktop", "female")
    assert st["female"] == {"engine": "windows", "voice": "Microsoft Hoda", "label": "Microsoft Hoda", "gender": "Female"}
    assert st["male"]["voice"] == "Microsoft Naayf" and st["voice"] == "Microsoft Hoda"


def test_arabic_voice_gender_setting_is_saved(client):
    r = client.put("/api/voice/settings", json={"arabic_voice_gender": "female"})
    assert r.status_code == 200 and r.json()["arabic_voice_gender"] == "female"
    assert client.put("/api/voice/settings", json={"arabic_voice_gender": "child"}).status_code == 422
    assert client.get("/api/voice/radio-voices").json()["arabic"]["gender"] == "Female"
