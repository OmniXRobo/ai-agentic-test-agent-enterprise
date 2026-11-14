"""The 23.7.0 cumulative patch (23.4.0 + 23.5.0 + 23.6.0 + 23.7.0) installed on real 23.3.35, 23.4.0, 23.5.0 and
23.6.0 trees (git archive of each release commit): apply, verify, rollback, apply again, the knowledge database
migration from schema 1 to 2 with its data kept, the automatic rollback after a failure, refusal of a wrong version and
of a changed patch file, the browser extension update files, and that the installer needs no network and no
administrator rights.

The Windows wrappers (APPLY_PATCH.cmd / .ps1) call this same install_patch.py; they are parsed on Windows by
tests/test_ops_windows.py and were not executed here (no Windows in this environment).
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = "SkyGuard_Autonomous_Application_Learning_Final_Patch"
PKG = ROOT / "packaging" / "autolearn-patch"
RELEASES = {"23.3.35": "6d448fa", "23.4.0": "53e80093fe99272f27dc424392d1fd3d746b3c7c", "23.5.0": "900e4e0",
            "23.6.0": "c229f6b"}


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    try:
        for commit in RELEASES.values():
            _git("cat-file", "-e", commit)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("the release commits are not in this checkout")
    out = tmp_path_factory.mktemp("build")
    r = subprocess.run([sys.executable, str(PKG / "build_patch.py"), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out / NAME


def _install(base: Path, version: str) -> tuple[Path, Path]:
    app = base / "app"
    app.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", RELEASES[version])), mode="r:") as tar:
        members = [m for m in tar.getmembers() if m.name.split("/")[0] in ("qa_platform", "web", "skyguard_agent")]
        tar.extractall(app, members=members, filter="data")
    data = base / "data"
    data.mkdir()
    env = {**os.environ, "QA_HOME": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; "
                    "from qa_platform.storage.db import Database; d = Database(Paths().ensure().db_file); d.close()"],
                   cwd=app, env=env, check=True, capture_output=True)
    assert _version(app) == version
    if (app / "skyguard_agent").is_dir():  # 23.5.0 / 23.6.0: the agent's knowledge database with an application in it
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from pathlib import Path; "
                        "from qa_platform.config import Paths; from skyguard_agent.store import Store; "
                        "s = Store(Path(Paths().ensure().root) / 'opsagent' / 'knowledge.sqlite3'); "
                        "s.execute(\"INSERT INTO applications (id, name, base_url, created_at, updated_at) VALUES "
                        "('app-1', 'FleetOps', 'http://fleet.local', 't', 't')\"); s.close()"],
                       cwd=app, env=env, check=True, capture_output=True)
    return app, data


def _run(stage: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(stage / "install_patch.py"), *args, "--skip-running-check"],
                          capture_output=True, text=True, timeout=900)


def _version(app: Path) -> str:
    for line in (app / "qa_platform" / "__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "?"


def _rows(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def test_patch_contents(built):
    manifest = json.loads((built / "PATCH_MANIFEST.json").read_text())
    assert manifest["version"] == "23.7.0" and manifest["baseline"] == ["23.3.35", "23.4.0", "23.5.0", "23.6.0"]
    assert manifest["extension_version"] == "1.1.0"
    files = manifest["files"]
    for must in ("qa_platform/__init__.py", "qa_platform/automation/components.py",
                 "qa_platform/automation/js/components.js", "qa_platform/automation/semantic.py",
                 "qa_platform/design/service.py", "skyguard_agent/agent.py", "skyguard_agent/locator.py",
                 "web/js/pages/ops-agent.js", "web/js/pages/history.js", "skyguard_agent/understanding.py",
                 "skyguard_agent/journal.py", "skyguard_agent/assistant.py", "skyguard_agent/store.py",
                 "qa_platform/api/opsagent.py", "qa_platform/opsagent/service.py"):
        assert must in files, must
    assert not any("__pycache__" in f or f.startswith(("tests/", "data/")) for f in files)
    for tool in ("APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py",
                 "UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1", "SHA256_CHECKSUM.txt"):
        assert (built / tool).is_file(), tool
    for crlf in ("APPLY_PATCH.cmd", "UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1"):
        assert b"\r\n" in (built / crlf).read_bytes(), crlf
    ext = built / "browser-extension-update"
    assert json.loads((ext / "extension" / "manifest.json").read_text())["version"] == "1.1.0"
    for must in ("app/companion_main.py", "app/skyguard_companion/server.py", "app/skyguard_agent/understanding.py",
                 "extension/background.js", "extension/lib/agent_dom.js"):
        assert (ext / must).is_file(), must
    assert not any("__pycache__" in str(p) for p in ext.rglob("*"))
    sums = dict(reversed(line.split("  ", 1)) for line in (built / "SHA256_CHECKSUM.txt").read_text().splitlines())
    assert "install_patch.py" in sums and "app/qa_platform/__init__.py" in sums
    assert "browser-extension-update/app/skyguard_agent/understanding.py" in sums


def test_installer_needs_no_network_and_no_administrator():
    here = PKG
    py = (here / "install_patch.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(urllib|http|requests|httpx|ftplib|smtplib)\b", py, re.M)
    for f in here.glob("*.*"):
        text = f.read_text(encoding="utf-8", errors="replace")
        assert not re.search(r"RunAs|HKLM:|HKEY_LOCAL_MACHINE|requireAdministrator|Program Files|sc\.exe|New-Service",
                             text, re.I), f.name


def test_wrong_version_is_refused(built, tmp_path):
    app, data = _install(tmp_path / "old", "23.4.0")
    init = app / "qa_platform" / "__init__.py"
    init.write_text(init.read_text().replace("23.4.0", "23.3.31"))
    r = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 1 and "installs on 23.3.35, 23.4.0, 23.5.0 or 23.6.0" in r.stderr
    assert _version(app) == "23.3.31"


def test_changed_patch_file_is_refused(built, tmp_path):
    stage = tmp_path / "patch"
    shutil.copytree(built, stage)
    target = stage / "app" / "skyguard_agent" / "understanding.py"
    target.write_text(target.read_text() + "\n# changed\n")
    app, data = _install(tmp_path / "inst", "23.6.0")
    r = _run(stage, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 1 and "damaged or changed" in r.stderr
    assert _version(app) == "23.6.0" and not (app / "skyguard_agent" / "understanding.py").exists()


def test_failure_after_copy_rolls_back(built, tmp_path):
    app, data = _install(tmp_path / "inst", "23.4.0")
    before = _rows(data / "qa_platform.sqlite3")
    r = _run(built, "apply", "--app", str(app), "--data", str(data), "--fail-after-copy")
    assert r.returncode == 1 and "previous version was restored" in r.stderr
    assert _version(app) == "23.4.0"
    assert not (app / "skyguard_agent").exists() and not (app / "PATCH_FILES_23.7.0.json").exists()
    assert _rows(data / "qa_platform.sqlite3") == before


def _knowledge(data: Path) -> tuple[int, set[str], int]:
    conn = sqlite3.connect(data / "opsagent" / "knowledge.sqlite3")
    try:
        return (conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0])
    finally:
        conn.close()


@pytest.mark.parametrize("version", ["23.3.35", "23.4.0", "23.5.0", "23.6.0"])
def test_apply_verify_rollback_and_apply_again(built, tmp_path, version):
    app, data = _install(tmp_path / "inst", version)
    before = _rows(data / "qa_platform.sqlite3")
    r = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _version(app) == "23.7.0"
    assert "Passive learning engine (23.7.0)" in r.stdout and "FAIL" not in r.stdout
    kver, ktables, kapps = _knowledge(data)
    assert kver == 2 and {"observations", "app_model"} <= ktables
    assert kapps == (1 if version in ("23.5.0", "23.6.0") else 0)  # the agent's earlier knowledge is kept
    after = _rows(data / "qa_platform.sqlite3")
    assert all(after.get(t, 0) >= n for t, n in before.items())
    conn = sqlite3.connect(data / "qa_platform.sqlite3")
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 18
    conn.close()

    v = _run(built, "verify", "--app", str(app), "--data", str(data))
    assert v.returncode == 0 and "FAIL" not in v.stdout, v.stdout + v.stderr

    rb = _run(built, "rollback", "--app", str(app), "--data", str(data))
    assert rb.returncode == 0, rb.stdout + rb.stderr
    assert _version(app) == version
    assert not (app / "skyguard_agent" / "understanding.py").exists()
    assert (app / "skyguard_agent").exists() == (version in ("23.5.0", "23.6.0"))
    assert (app / "qa_platform" / "automation" / "components.py").exists() == (version == "23.6.0")
    assert _knowledge(data)[0] == 2  # data is never deleted by a rollback
    if version in ("23.5.0", "23.6.0"):  # the older agent opens the schema 2 knowledge database as it is
        out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from pathlib import Path; "
                              "from qa_platform.config import Paths; from skyguard_agent.store import Store; "
                              "s = Store(Path(Paths().ensure().root) / 'opsagent' / 'knowledge.sqlite3'); "
                              "print(s.query_one('SELECT COUNT(*) AS n FROM applications')['n']); s.close()"],
                             cwd=app, env={**os.environ, "QA_HOME": str(data)}, capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip() == "1", out.stderr

    again = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _version(app) == "23.7.0"
    assert json.loads((app / "PATCH_HISTORY.json").read_text())[-1] == {**json.loads(
        (app / "PATCH_HISTORY.json").read_text())[-1], "version": "23.7.0", "from": version}
