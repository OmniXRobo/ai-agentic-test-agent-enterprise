"""First-run setup: three inputs (TFS, LLM model, optional voice model), installer pre-fill, no secrets in files."""

from __future__ import annotations

import json
import sys
import zipfile

import pytest

from qa_platform.config import APP_ROOT, Settings
from qa_platform.first_run import prefill, tfs_mode_for, voice_folder
from qa_platform.errors import QAError

sys.path.insert(0, str(APP_ROOT / "packaging"))
import build_offline_package as build  # noqa: E402


def _vosk_model(folder):
    (folder / "am").mkdir(parents=True)
    (folder / "conf").mkdir()
    return folder


def _llm_files(tmp_path):
    model = tmp_path / "models" / "qwen.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF")
    server = tmp_path / "llama" / "llama-server.exe"
    server.parent.mkdir()
    server.write_bytes(b"MZ")
    return model, server


def test_tfs_mode_follows_the_url():
    assert tfs_mode_for("https://dev.azure.com/org") == "online"
    assert tfs_mode_for("https://org.visualstudio.com") == "online"
    assert tfs_mode_for("https://tfs.company.local/tfs/DefaultCollection") == "offline"


def test_new_install_needs_setup_and_skip_hides_it(client):
    s = client.get("/api/setup").json()
    assert s["needed"] is True and s["tfs"]["ready"] is False and s["llm"]["ready"] is False
    assert client.post("/api/setup/dismiss").json()["needed"] is False


def test_setup_saves_tfs_llm_and_voice_without_secrets_in_files(client, rt, tmp_path):
    model, server = _llm_files(tmp_path)
    voice = _vosk_model(tmp_path / "vosk-model-en")
    out = rt.setup.save(tfs_url="http://127.0.0.1:9/tfs/DefaultCollection/", tfs_project="Web", tfs_pat="secret-pat-123",
                        llm_model=f'"{model}"', llm_server=str(server), voice_model=str(voice), start_llm=False)
    steps = {s["name"]: s for s in out["steps"]}
    assert steps["TFS"]["status"] == "FAIL"  # nothing listens on port 9; the values are still saved
    assert steps["Local LLM"]["status"] == "PASS" and steps["Voice"]["status"] == "PASS"
    assert out["needed"] is False and out["tfs"]["mode"] == "offline" and out["tfs"]["has_pat"]
    assert out["tfs"]["url"] == "http://127.0.0.1:9/tfs/DefaultCollection"
    settings = rt.settings.get()
    assert settings["tfs"]["active_mode"] == "offline"
    assert settings["ai"]["managed"]["model_path"] == str(model) and settings["ai"]["managed"]["auto_start"] is True
    assert settings["voice"]["stt_model_dir"] == str(voice)
    assert "secret-pat-123" not in rt.settings.path.read_text()


def test_setup_rejects_missing_inputs(client, tmp_path):
    model, _ = _llm_files(tmp_path)
    body = {"tfs_url": "https://tfs/tfs/Col", "tfs_project": "Web", "llm_model": str(model)}
    r = client.post("/api/setup", json=body)  # no PAT saved yet
    assert r.status_code == 400 and "Personal Access Token" in r.json()["message"]
    r = client.post("/api/setup", json={**body, "tfs_pat": "x", "llm_model": str(tmp_path / "missing.gguf")})
    assert r.status_code == 400 and ".gguf" in r.json()["message"]


def test_voice_model_zip_is_unpacked_once_and_unsafe_zip_refused(tmp_path):
    good = tmp_path / "vosk-small.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("vosk-small/am/final.mdl", "x")
        z.writestr("vosk-small/conf/model.conf", "x")
    folder = voice_folder(good, tmp_path / "data")
    assert folder == tmp_path / "data" / "speech-model" / "vosk-small" / "vosk-small"
    assert voice_folder(good, tmp_path / "data") == folder
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil/am/x", "x")
    with pytest.raises(QAError, match="outside"):
        voice_folder(bad, tmp_path / "data")


def test_installer_prefill_writes_settings_but_never_a_pat(tmp_path):
    model, server = _llm_files(tmp_path)
    voice = _vosk_model(tmp_path / "vosk")
    path = tmp_path / "data" / "settings.json"
    applied = prefill(path, tfs_url="https://tfs.local/tfs/Col/", tfs_project="Web", llm_model=str(model),
                      voice_model=str(voice))
    assert set(applied) == {"tfs", "ai", "voice"}
    stored = json.loads(path.read_text())
    assert stored["tfs"]["active_mode"] == "offline" and stored["tfs"]["offline"]["collection_url"] == "https://tfs.local/tfs/Col"
    assert stored["ai"]["managed"] == {**stored["ai"]["managed"], "model_path": str(model), "auto_start": True}
    assert Settings(path).section("voice")["stt_model_dir"] == str(voice)
    assert prefill(tmp_path / "other.json") == {} and not (tmp_path / "other.json").exists()


def test_sbom_and_checksums(tmp_path):
    payload = tmp_path / "payload"
    jar = payload / "runtime" / "apache-jmeter-5.6.3" / "lib" / "commons-text-1.10.0.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"jar")
    info = {"name": "AI Agentic Test Agent Enterprise", "version": "1.0.0", "built_at": "2026-10-25T10:00:00+04:00",
            "components": [{"name": "llama.cpp llama-server", "version": "b7000", "license": "MIT", "sha256": "a" * 64,
                            "source": "https://example.invalid/llama.zip"}],
            "packages": [{"name": "fastapi", "version": "0.142.4", "license": "MIT"}]}
    bom = build.sbom(info, payload)
    assert bom["bomFormat"] == "CycloneDX" and bom["metadata"]["component"]["version"] == "1.0.0"
    by = {c["name"]: c for c in bom["components"]}
    assert by["fastapi"]["purl"] == "pkg:pypi/fastapi@0.142.4"
    assert by["llama.cpp llama-server"]["hashes"][0]["content"] == "a" * 64
    assert by["commons-text"]["version"] == "1.10.0" and len(by["commons-text"]["hashes"][0]["content"]) == 64
    assert len({c["bom-ref"] for c in bom["components"]}) == len(bom["components"])
    archive = tmp_path / "pkg.zip"
    archive.write_bytes(b"zip")
    line = build.write_checksums(archive).read_text()
    assert line == f"{build.sha256(archive)}  pkg.zip\n"
