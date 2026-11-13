"""The 23.5.0 patch installer against a real 23.4.0 tree (git archive of the baseline commit): refusal of a wrong
version and of a changed patch file, the automatic rollback after a failure, apply, verify, rollback and apply again.

The Windows wrappers (APPLY_PATCH.cmd / .ps1) call this same install_patch.py; they were not executed here (no Windows).
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# 23.6.0: the 23.5.0 package is built from the working tree, which is 23.6.0 now; its installer would refuse a tree
# that does not say 23.5.0. The cumulative patch (23.4.0 + 23.5.0 + 23.6.0) is tested in test_cumulative_patch_23600.
_TREE = (ROOT / "qa_platform" / "__init__.py").read_text(encoding="utf-8")
pytestmark = pytest.mark.skipif('__version__ = "23.5.0"' not in _TREE,
                                reason="superseded by the 23.6.0 cumulative patch (tests/test_cumulative_patch_23600.py)")
BASELINE = "53e80093fe99272f27dc424392d1fd3d746b3c7c"
NAME = "SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch"


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    try:
        _git("cat-file", "-e", BASELINE)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("the 23.4.0 baseline commit is not in this checkout")
    out = tmp_path_factory.mktemp("build")
    subprocess.run([sys.executable, str(ROOT / "packaging" / "ops-agent-patch" / "build_patch.py"), "--out", str(out)],
                   check=True, capture_output=True)
    return out / NAME


def _baseline_install(base: Path) -> tuple[Path, Path]:
    app = base / "app"
    app.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", BASELINE)), mode="r:") as tar:
        members = [m for m in tar.getmembers() if m.name.split("/")[0] in ("qa_platform", "web")]
        tar.extractall(app, members=members, filter="data")
    data = base / "data"
    data.mkdir()
    env = {**os.environ, "QA_HOME": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; "
                    "from qa_platform.storage.db import Database; d = Database(Paths().ensure().db_file); d.close()"],
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
    assert manifest["version"] == "23.5.0" and manifest["baseline"] == "23.4.0"
    files = manifest["files"]
    assert "qa_platform/__init__.py" in files and "skyguard_agent/agent.py" in files
    assert any(f.startswith("web/js/pages/ops-agent") for f in files)
    for tool in ("APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py",
                 "SHA256_CHECKSUM.txt"):
        assert (built / tool).is_file(), tool
    assert b"\r\n" in (built / "APPLY_PATCH.cmd").read_bytes()


def test_wrong_version_is_refused(built, tmp_path):
    app, data = _baseline_install(tmp_path / "old")
    init = app / "qa_platform" / "__init__.py"
    init.write_text(init.read_text().replace("23.4.0", "23.3.31"))
    r = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 1 and "installs on 23.4.0" in r.stderr
    assert _version(app) == "23.3.31"


def test_changed_patch_file_is_refused(built, tmp_path):
    import shutil

    stage = tmp_path / "patch"
    shutil.copytree(built, stage)
    target = stage / "app" / "skyguard_agent" / "agent.py"
    target.write_text(target.read_text() + "\n# changed\n")
    app, data = _baseline_install(tmp_path / "inst")
    r = _run(stage, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 1 and "damaged or changed" in r.stderr
    assert _version(app) == "23.4.0" and not (app / "skyguard_agent").exists()


def test_failure_after_copy_rolls_back(built, tmp_path):
    app, data = _baseline_install(tmp_path / "inst")
    before = _rows(data / "qa_platform.sqlite3")
    r = _run(built, "apply", "--app", str(app), "--data", str(data), "--fail-after-copy")
    assert r.returncode == 1 and "previous version was restored" in r.stderr
    assert _version(app) == "23.4.0"
    assert not (app / "skyguard_agent").exists() and not (app / "PATCH_FILES_23.5.0.json").exists()
    assert _rows(data / "qa_platform.sqlite3") == before


def test_apply_verify_rollback_and_apply_again(built, tmp_path):
    app, data = _baseline_install(tmp_path / "inst")
    before = _rows(data / "qa_platform.sqlite3")
    r = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _version(app) == "23.5.0"
    kdb = data / "opsagent" / "knowledge.sqlite3"
    assert kdb.is_file()
    after = _rows(data / "qa_platform.sqlite3")
    assert all(after.get(t, 0) >= n for t, n in before.items())

    v = _run(built, "verify", "--app", str(app), "--data", str(data))
    assert v.returncode == 0, v.stdout + v.stderr
    assert "FAIL" not in v.stdout

    rb = _run(built, "rollback", "--app", str(app), "--data", str(data))
    assert rb.returncode == 0, rb.stdout + rb.stderr
    assert _version(app) == "23.4.0" and not (app / "skyguard_agent").exists()
    assert kdb.is_file()  # data is never deleted by a rollback

    again = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _version(app) == "23.5.0"
    history = json.loads((app / "PATCH_HISTORY.json").read_text())
    assert history[-1]["version"] == "23.5.0"
