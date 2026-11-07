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
