"""23.3.32: the all-in-one package sets the bundled AI and speech models, and refuses paths too long to extract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from qa_platform.config import APP_ROOT
from qa_platform.offline import install as inst

sys.path.insert(0, str(APP_ROOT / "packaging"))
import build_offline_package as build  # noqa: E402


def _bundle(app: Path) -> None:
    (app / "runtime" / "ai" / "models").mkdir(parents=True)
    (app / "runtime" / "ai" / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf").write_bytes(b"GGUF")
    (app / "runtime" / "ai" / ("llama-server.exe" if inst.WINDOWS else "llama-server")).write_bytes(b"MZ")
    fw = app / "runtime" / "faster-whisper"
    (fw / "lib").mkdir(parents=True)
    (fw / "models" / "small.en").mkdir(parents=True)
    (fw / "models" / "small.en" / "model.bin").write_bytes(b"x")


def test_bundled_defaults_set_ai_and_faster_whisper(tmp_path):
    app, settings = tmp_path / "app", tmp_path / "data" / "settings.json"
    _bundle(app)
    done = inst.bundled_defaults(app, settings)
    assert len(done) == 2
    data = json.loads(settings.read_text())
    assert data["ai"]["mode"] == "on_premise"
    assert data["ai"]["managed"]["model_path"].endswith("qwen2.5-1.5b-instruct-q4_k_m.gguf")
    assert data["ai"]["managed"]["auto_start"] is True
    assert data["voice"]["stt_api"] == "faster_whisper" and data["voice"]["fw_model"] == "small.en"
    assert inst.bundled_defaults(app, settings) == []  # second install (upgrade) keeps what is set


def test_bundled_defaults_keep_model_chosen_by_it(tmp_path):
    app, settings = tmp_path / "app", tmp_path / "data" / "settings.json"
    _bundle(app)
    own = tmp_path / "own.gguf"
    own.write_bytes(b"GGUF")
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"ai": {"managed": {"model_path": str(own)}}}))
    inst.bundled_defaults(app, settings)
    assert json.loads(settings.read_text())["ai"]["managed"]["model_path"] == str(own)


def test_package_paths_are_short(tmp_path):
    package = tmp_path / build.PACKAGE_DIR
    (package / "payload" / "a").mkdir(parents=True)
    (package / "payload" / "a" / "f.txt").write_text("x")
    build.check_path_lengths(package)
    deep = package / "payload" / ("d" * 100) / ("e" * 100)
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("x")
    with pytest.raises(SystemExit):
        build.check_path_lengths(package)


def test_package_files_have_short_names_and_security_notes():
    assert build.PACKAGE_DIR == "SGQA"
    assert (APP_ROOT / "packaging" / "templates" / "SECURITY.txt").exists()
    readme = (APP_ROOT / "packaging" / "templates" / "README_OFFLINE.txt").read_text()
    assert "What is left out, and why" in readme and "SkyGuard QA" in readme
    lock = json.loads((APP_ROOT / "packaging" / "offline-lock.json").read_text())
    assert lock["llm_model"]["files"] == ["qwen2.5-1.5b-instruct-q4_k_m.gguf"]


def test_move_aside_stops_programs_and_retries(tmp_path, monkeypatch):
    src, dst = tmp_path / "app", tmp_path / "app.previous"
    src.mkdir()
    real_rename, calls, stops, waits = Path.rename, {"n": 0}, [], []

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] < 4:  # Windows keeps a file open for a moment after the program stopped
            raise PermissionError(5, "Access is denied")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    inst.move_aside(src, dst, lambda m: None, sleep=waits.append, stop=lambda f, log: stops.append(f) or 1)
    assert dst.is_dir() and not src.exists() and calls["n"] == 4
    assert stops == [src, src] and len(waits) == 3  # stopped before the first try and again after the third


def test_move_aside_gives_up_with_the_error(tmp_path, monkeypatch):
    src = tmp_path / "app"
    src.mkdir()
    monkeypatch.setattr(Path, "rename", lambda self, t: (_ for _ in ()).throw(PermissionError(5, "Access is denied")))
    with pytest.raises(PermissionError):
        inst.move_aside(src, tmp_path / "x", lambda m: None, attempts=3, sleep=lambda s: None, stop=lambda f, log: 0)


def test_no_program_lookup_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "WINDOWS", False)
    assert inst.programs_running_in(tmp_path) == [] and inst.stop_programs_in(tmp_path, print) == 0


def test_installer_chooses_edge_for_test_runs_when_installed(tmp_path, monkeypatch):
    app, settings = tmp_path / "app", tmp_path / "data" / "settings.json"
    _bundle(app)
    pf = tmp_path / "pf86"
    (pf / "Microsoft" / "Edge" / "Application").mkdir(parents=True)
    (pf / "Microsoft" / "Edge" / "Application" / "msedge.exe").write_bytes(b"")
    for k in ("ProgramFiles", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ProgramFiles(x86)", str(pf))
    inst.bundled_defaults(app, settings)
    assert json.loads(settings.read_text())["playback"]["browser"] == "msedge"
    data = json.loads(settings.read_text())
    data["playback"]["browser"] = "chromium"  # a choice made later is kept on the next install
    settings.write_text(json.dumps(data))
    inst.bundled_defaults(app, settings)
    assert json.loads(settings.read_text())["playback"]["browser"] == "chromium"
