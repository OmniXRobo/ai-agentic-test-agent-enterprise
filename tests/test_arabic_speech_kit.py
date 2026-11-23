"""Arabic speech kit (add-on for 23.14.1): the kit's contents and the Voice settings change it makes.

The download, the install from a real faster-whisper-small.zip and Arabic and English speech recognized through
SkyGuard QA run on Windows in CI (job arabic-speech-kit-windows, packaging/arabic-speech-kit/ci_end_to_end.py).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "packaging" / "arabic-speech-kit"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


helper = load("set_speech_language", KIT / "kit" / "set_speech_language.py")
builder = load("build_kit", KIT / "build_kit.py")


def app_folder(tmp_path: Path, installed: bool = True, fw: bool = True, settings: dict | None = None) -> tuple[Path, Path]:
    app = tmp_path / "app"
    (app / "qa_platform").mkdir(parents=True)
    (app / "qa_platform" / "__init__.py").write_text('__version__ = "23.14.1"\n', encoding="utf-8")
    if installed:
        (app / "INSTALLED.json").write_text('{"installed": true}', encoding="utf-8")
    if fw:
        (app / "runtime" / "faster-whisper" / "lib" / "faster_whisper").mkdir(parents=True)
    data = (tmp_path / "data") if installed else (app / "data")
    if settings is not None:
        data.mkdir(parents=True, exist_ok=True)
        (data / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return app, data / "settings.json"


@pytest.fixture(autouse=True)
def no_qa_home(monkeypatch):
    monkeypatch.delenv("QA_HOME", raising=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_switches_to_the_multilingual_model_and_english_plus_arabic(tmp_path, capsys):
    port = free_port()
    old = {"server": {"port": port}, "ui": {"brand_name": "SkyGuard QA"},
           "voice": {"stt_engine": "builtin", "stt_api": "whisper_cpp", "fw_model": "small.en", "language": "en",
                     "radio_voice": "ar_JO-kareem-medium", "arabic_voice_gender": "male"}}
    app, sfile = app_folder(tmp_path, settings=old)
    assert helper.main(["--app", str(app), "--model", "small"]) == 0
    new = json.loads(sfile.read_text(encoding="utf-8"))
    assert new["voice"] == {**old["voice"], "stt_engine": "local", "stt_api": "faster_whisper", "fw_model": "small",
                            "language": "auto"}
    assert new["server"] == old["server"] and new["ui"] == old["ui"]  # nothing else touched
    backups = list(sfile.parent.glob("settings.before-arabic-speech-*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8")) == old
    out = capsys.readouterr().out
    assert "voice.language: 'en' -> 'auto'" in out and "voice.fw_model: 'small.en' -> 'small'" in out


def test_arabic_only_and_medium_and_a_portable_copy_without_settings(tmp_path):
    app, sfile = app_folder(tmp_path, installed=False)
    assert not sfile.exists()
    assert helper.main(["--app", str(app), "--model", "medium", "--language", "ar"]) == 0
    v = json.loads(sfile.read_text(encoding="utf-8"))["voice"]
    assert v == {"stt_engine": "local", "stt_api": "faster_whisper", "fw_model": "medium", "language": "ar"}
    assert sfile.parent == app / "data"


def test_without_faster_whisper_the_recognizer_is_left_alone(tmp_path, capsys):
    app, sfile = app_folder(tmp_path, fw=False, settings={"server": {"port": free_port()}, "voice": {"stt_engine": "builtin"}})
    assert helper.main(["--app", str(app), "--model", "small"]) == 0
    v = json.loads(sfile.read_text(encoding="utf-8"))["voice"]
    assert v["stt_engine"] == "builtin" and v["language"] == "auto" and v["fw_model"] == "small"
    assert "Faster-Whisper itself is not installed" in capsys.readouterr().out


def test_a_running_skyguard_is_never_changed_under_its_feet(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        old = {"server": {"port": s.getsockname()[1]}, "voice": {"language": "en"}}
        app, sfile = app_folder(tmp_path, settings=old)
        assert helper.main(["--app", str(app), "--model", "small"]) == helper.RUNNING
    assert json.loads(sfile.read_text(encoding="utf-8")) == old
    assert not list(sfile.parent.glob("settings.before-arabic-speech-*.json"))


def test_english_only_model_and_unreadable_settings_change_nothing(tmp_path):
    app, sfile = app_folder(tmp_path, settings={"voice": {"language": "en"}})
    assert helper.main(["--app", str(app), "--model", "small.en", "--no-running-check"]) == 2
    sfile.write_text("{broken", encoding="utf-8")
    assert helper.main(["--app", str(app), "--model", "small", "--no-running-check"]) == 1
    assert sfile.read_text(encoding="utf-8") == "{broken"


def test_qa_home_is_where_the_settings_are(tmp_path, monkeypatch):
    app, _ = app_folder(tmp_path)
    home = tmp_path / "elsewhere"
    monkeypatch.setenv("QA_HOME", str(home))
    assert helper.main(["--app", str(app), "--model", "small", "--no-running-check"]) == 0
    assert json.loads((home / "settings.json").read_text(encoding="utf-8"))["voice"]["language"] == "auto"


def test_the_app_picks_the_installed_multilingual_model_for_these_settings(tmp_path, monkeypatch):
    """What the helper writes is what SkyGuard QA 23.14.1 needs to run Faster-Whisper with the "small" model."""
    from qa_platform.config import Settings
    from qa_platform.voice.fw_service import FasterWhisperService

    home = tmp_path / "fw"
    (home / "lib" / "faster_whisper").mkdir(parents=True)
    for name in ("small.en", "small"):
        (home / "models" / name).mkdir(parents=True)
        for f in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
            (home / "models" / name / f).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("QA_FASTER_WHISPER_DIR", str(home))
    app, sfile = app_folder(tmp_path, settings={"voice": {"stt_engine": "builtin", "fw_model": "small.en"}})
    assert helper.main(["--app", str(app), "--model", "small", "--no-running-check"]) == 0
    fw = FasterWhisperService(Settings(sfile))
    assert fw.selected() and fw.config()["language"] == "auto" and fw.model_dir().name == "small"


def test_kit_zip_contents(tmp_path):
    z = builder.build(tmp_path)
    with zipfile.ZipFile(z) as zf:
        names = {n.split("/", 1)[1] for n in zf.namelist()}
        assert names == set(builder.FILES) | {"SHA256SUMS.txt"}
        files = {n.split("/", 1)[1]: zf.read(n) for n in zf.namelist()}
    for n, b in files.items():
        if n.endswith((".cmd", ".ps1", ".txt")):
            assert b"\n" not in b.replace(b"\r\n", b""), f"{n} has Unix line endings"
    assert files["README_FIRST.txt"].startswith(b"\xef\xbb\xbf")
    for line in files["SHA256SUMS.txt"].decode().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(files[name]).hexdigest() == digest
    # the scripts the kit carries are the application's own
    assert files["GET_FASTER_WHISPER_MODEL.ps1"].replace(b"\r\n", b"\n") == \
        (ROOT / "GET_FASTER_WHISPER_MODEL.ps1").read_bytes().replace(b"\r\n", b"\n").removeprefix(b"\xef\xbb\xbf")
    get = files["GET_ARABIC_SPEECH_MODEL.cmd"].decode()
    assert "-ExecutionPolicy Bypass" in get and 'set "MODEL=small"' in get and "small.en" in get
    inst = files["INSTALL_ARABIC_SPEECH_MODEL.cmd"].decode()
    assert "-ExecutionPolicy Bypass" in inst and "INSTALL_ARABIC_SPEECH_MODEL.ps1" in inst
    ps1 = files["INSTALL_ARABIC_SPEECH_MODEL.ps1"].decode()
    assert "set_speech_language.py" in ps1 and "SOURCE.json" in ps1 and '\\.en$' in ps1
    assert (tmp_path / f"{builder.NAME}.zip.sha256").read_text().split()[0] == hashlib.sha256(z.read_bytes()).hexdigest()
