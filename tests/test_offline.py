"""Stage 9: offline package manifest, validation (network blocked), per-user install, rollback, builder helpers."""

from __future__ import annotations

import hashlib
import json
import socket
import sys
import zipfile
from pathlib import Path

import pytest

from qa_platform.config import APP_ROOT
from qa_platform.offline import install as inst
from qa_platform.offline import manifest as mf
from qa_platform.offline import validate as val

sys.path.insert(0, str(APP_ROOT / "packaging"))
import build_offline_package as build  # noqa: E402


def _payload(root: Path, version: str = "1.0.0") -> Path:
    (root / "qa_platform").mkdir(parents=True)
    (root / "qa_platform" / "__init__.py").write_text(f"__version__ = '{version}'\n")
    (root / "runtime" / "python").mkdir(parents=True)
    (root / "runtime" / "python" / "python.exe").write_bytes(b"MZ fake")
    (root / "START_QA_PLATFORM.cmd").write_text("@echo off\r\n")
    mf.write(root, {"name": "AI Agentic Test Agent Enterprise", "version": version, "built_at": "2026-10-21T10:00:00+04:00",
                    "python": "3.12.8", "components": [], "packages": []})
    return root


def _ok(app: Path, data: Path, quick: bool):
    return True, f"RESULT: PASS ({'quick' if quick else 'full'}) {app.name}"


# ---- manifest ----------------------------------------------------------------------------------


def test_manifest_detects_missing_changed_and_extra_files(tmp_path):
    root = _payload(tmp_path / "p")
    (root / "data").mkdir()
    (root / "data" / "settings.json").write_text("{}")  # run-time data is never part of the manifest
    (root / "qa_platform" / "__pycache__").mkdir()
    (root / "qa_platform" / "__pycache__" / "x.pyc").write_bytes(b"0")
    m = mf.write(root, {"version": "1"})
    assert set(m["files"]) == {"qa_platform/__init__.py", "runtime/python/python.exe", "START_QA_PLATFORM.cmd"}
    assert mf.verify(root) == {"missing": [], "changed": [], "extra": []}
    (root / "START_QA_PLATFORM.cmd").write_text("tampered")
    (root / "runtime" / "python" / "python.exe").unlink()
    (root / "runtime" / "ai").mkdir()
    (root / "runtime" / "ai" / "model.gguf").write_bytes(b"gguf")
    diff = mf.verify(root)
    assert diff == {"missing": ["runtime/python/python.exe"], "changed": ["START_QA_PLATFORM.cmd"], "extra": ["runtime/ai/model.gguf"]}


# ---- validation ---------------------------------------------------------------------------------


def test_network_guard_blocks_outbound_but_allows_this_pc():
    with val.network_blocked():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(val.NetworkBlocked):
            s.connect(("93.184.216.34", 443))
        s.close()
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(srv.getsockname())  # loopback is allowed (the dashboard itself)
        c.close()
        srv.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    assert socket.socket.connect.__name__ == "connect"  # restored
    s.close()


def test_validation_reports_missing_runtime_parts_without_secrets(tmp_path):
    app = _payload(tmp_path / "app")
    data = tmp_path / "data"
    results, manifest = val.validate(app, data)
    by = {r.name: r for r in results}
    assert by["Package files"].status == "PASS" and "All 3 files" in by["Package files"].detail
    assert by["Java runtime"].status == "FAIL" and by["JMeter"].status == "FAIL"
    assert by["Chromium for Playwright"].status == "FAIL"
    assert by["Data folder"].status == "PASS" and by["On-Premise AI files"].status == "INFO"
    assert by["Offline speech model"].status == "INFO"
    assert by["Offline operation"].status == "INFO"
    text = val.render(results, app, manifest)
    assert "RESULT: FAIL" in text and "Version: 1.0.0" in text
    quick = {r.name for r in val.validate(app, data, quick=True)[0]}
    assert "JMeter" not in quick and "Chromium for Playwright" not in quick
    # a damaged copy fails the file check
    (app / "START_QA_PLATFORM.cmd").write_text("changed")
    assert {r.name: r for r in val.validate(app, data, quick=True)[0]}["Package files"].status == "FAIL"


def test_validation_cli_writes_report_beside_package(tmp_path, capsys):
    app = _payload(tmp_path / "pkg" / "payload")
    rc = val.main(["--app", str(app), "--quick"])
    out = capsys.readouterr().out
    assert rc == 1 and "[FAIL] Java runtime" in out
    assert (tmp_path / "pkg" / "validation-report.txt").read_text().startswith("AI Agentic Test Agent Enterprise")


def test_bundled_java_is_found_and_run(tmp_path, monkeypatch):
    app = _payload(tmp_path / "app")
    java = app / "runtime" / "jdk-17.0.13+11-jre" / "bin" / ("java.exe" if val.WINDOWS else "java")
    java.parent.mkdir(parents=True)
    java.write_text("x")
    calls = []

    class Out:
        returncode, stdout, stderr = 0, "", 'openjdk version "17.0.13" 2024-10-15'

    monkeypatch.setattr(val, "_run", lambda cmd, timeout=90, env=None: calls.append(cmd) or Out())
    check = val.Validator(app, None, None).check_java()
    assert check.status == "PASS" and "17.0.13" in check.detail and calls[0][0] == str(java)


# ---- install / rollback -------------------------------------------------------------------------


def test_install_upgrade_keeps_one_previous_version_and_data(tmp_path):
    target = tmp_path / "Programs" / "AIAgenticTestAgent"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False) == 0
    app = target / "app"
    assert json.loads((app / "INSTALLED.json").read_text())["version"] == "1.0.0"
    assert (target / "data").is_dir() and (target / "START_QA_PLATFORM.cmd").exists()
    rollback_cmd = (target / "ROLLBACK_OFFLINE.cmd").read_text()
    assert 'move "app" "app.swap"' in rollback_cmd and "python" not in rollback_cmd.split("move")[1]
    (target / "data" / "settings.json").write_text('{"server": {"port": 8899}}')
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False) == 0
    assert json.loads((target / "app.previous" / "INSTALLED.json").read_text())["version"] == "1.0.0"
    assert inst.install(_payload(tmp_path / "v3", "3.0.0"), target, validation=_ok, shortcuts=False) == 0
    assert json.loads((target / "app.previous" / "INSTALLED.json").read_text())["version"] == "2.0.0"
    assert not list(target.glob("app.old-*")) and not (target / "app.new").exists()
    assert (target / "data" / "settings.json").read_text() == '{"server": {"port": 8899}}'
    logs = list((target / "logs").glob("install-*.log"))
    assert logs and "Kept the current version as app.previous" in "".join(p.read_text() for p in logs)


def test_failed_validation_switches_back_automatically(tmp_path):
    target = tmp_path / "t"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False) == 0
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False) == 0

    def full_fails(app, data, quick):
        return (True, "quick ok") if quick else (False, "RESULT: FAIL - Chromium did not start")

    assert inst.install(_payload(tmp_path / "v3", "3.0.0"), target, validation=full_fails, shortcuts=False) == 1
    assert json.loads((target / "app" / "INSTALLED.json").read_text())["version"] == "2.0.0"
    assert json.loads((target / "app.previous" / "INSTALLED.json").read_text())["version"] == "1.0.0"
    assert not list(target.glob("app.failed-*")) and not list(target.glob("app.old-*"))
    # a copy that fails the quick check never replaces anything
    assert inst.install(_payload(tmp_path / "v4", "4.0.0"), target, validation=lambda a, d, q: (False, "bad"), shortcuts=False) == 1
    assert json.loads((target / "app" / "INSTALLED.json").read_text())["version"] == "2.0.0"
    assert not (target / "app.new").exists()


def test_damaged_package_and_unsafe_targets_are_refused(tmp_path):
    src = _payload(tmp_path / "pkg")
    (src / "START_QA_PLATFORM.cmd").write_text("tampered")
    target = tmp_path / "t"
    assert inst.install(src, target, validation=_ok, shortcuts=False) == 1
    assert not (target / "app").exists()
    assert "The package is damaged" in next((target / "logs").glob("install-*.log")).read_text()
    with pytest.raises(inst.InstallError):
        inst.install(_payload(tmp_path / "pkg2"), tmp_path / "pkg2" / "inside", validation=_ok, shortcuts=False)


def test_rollback_swaps_versions_and_back(tmp_path):
    target = tmp_path / "t"
    inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False)
    inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False)
    version = lambda p: json.loads((p / "INSTALLED.json").read_text())["version"]  # noqa: E731
    assert inst.rollback(target, validation=_ok) == 0
    assert version(target / "app") == "1.0.0" and version(target / "app.previous") == "2.0.0"
    assert inst.rollback(target) == 0 and version(target / "app") == "2.0.0"
    assert (target / "data").is_dir()
    assert inst.rollback(tmp_path / "empty") == 1


def test_start_script_uses_data_beside_installed_app():
    text = (APP_ROOT / "START_QA_PLATFORM.cmd").read_text()
    assert 'if exist "%HERE%INSTALLED.json" set "DATA=%HERE%..\\data"' in text and "%QA_HOME%\\settings.json" in text
    for name in ("INSTALL_OFFLINE.cmd", "VALIDATE_OFFLINE.cmd", "VALIDATE_OFFLINE_PACKAGE.cmd", "README_OFFLINE.txt"):
        assert (APP_ROOT / "packaging" / "templates" / name).exists()
    assert "Administrator" not in (APP_ROOT / "packaging" / "templates" / "INSTALL_OFFLINE.cmd").read_text().replace("administrator", "")


# ---- builder ------------------------------------------------------------------------------------


def test_builder_verifies_checksums_before_use(tmp_path, monkeypatch):
    blob = tmp_path / "src" / "thing-1.0.zip"
    blob.parent.mkdir()
    blob.write_bytes(b"pinned content")
    good = hashlib.sha256(b"pinned content").hexdigest()
    monkeypatch.setitem(build.LOCK, "thing", {"name": "Thing", "version": "1.0", "url": blob.as_uri(), "sha256": good})
    cache = tmp_path / "cache"
    assert build.fetch("thing", cache).read_bytes() == b"pinned content"
    monkeypatch.setitem(build.LOCK, "thing", {"name": "Thing", "version": "1.0", "url": blob.as_uri(), "sha256": "0" * 64})
    with pytest.raises(SystemExit, match="Checksum mismatch"):
        build.fetch("thing", cache)
    assert not (cache / "thing-1.0.zip").exists()


def test_builder_refuses_archive_paths_outside_target(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("tools/ok.txt", "ok")
        z.writestr("tools/../../evil.txt", "x")
    with pytest.raises(SystemExit, match="outside the target"):
        build.extract_zip(bad, tmp_path / "out", prefix="tools/")


def test_builder_stages_application_without_tests_or_data(tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    build.stage_app(payload)
    assert (payload / "qa_platform" / "offline" / "validate.py").exists() and (payload / "web" / "index.html").exists()
    assert (payload / "VALIDATE_OFFLINE.cmd").exists() and not (payload / "tests").exists() and not (payload / "data").exists()
    assert not list(payload.rglob("__pycache__"))
    md = build.inventory_md({"version": "0.1.0", "built_at": "x", "python": "3.12.8",
                             "components": [{"name": "Apache JMeter", "version": "5.6.3", "license": "Apache-2.0",
                                             "sha256": "f68efc17fe060f69", "source": "u"}],
                             "packages": [{"name": "fastapi", "version": "0.115.6", "license": "MIT"}]})
    assert "| Apache JMeter | 5.6.3 |" in md and "| fastapi | 0.115.6 | MIT |" in md


def test_lock_pins_every_download_and_every_python_package():
    for key in ("python", "jre", "jmeter"):
        assert len(build.LOCK[key]["sha256"]) == 64 and build.LOCK[key]["url"].startswith("https://")
    reqs = build.REQUIREMENTS.read_text()
    pinned = [ln.split("==")[0] for ln in reqs.splitlines() if "==" in ln and not ln.startswith("#")]
    for top in ("fastapi", "uvicorn", "httpx", "pydantic", "keyring", "playwright", "pytest", "pywin32-ctypes"):
        assert top in pinned
    assert reqs.count("--hash=sha256:") >= len(pinned)
    top_level = {ln.split("==")[0]: ln.split("==")[1].strip() for ln in (APP_ROOT / "requirements.txt").read_text().splitlines()
                 if "==" in ln}
    for name, version in top_level.items():
        assert f"{name}=={version}" in reqs, name
