"""The 23.9.0 cumulative patch (23.4.0 to 23.9.0: Integration Comparison, offline OCR and browser final patch) installed
on real 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2 and 23.8.0 trees (git archive of each release
commit): apply, verify, rollback, apply again, the knowledge database migration with its data kept, the automatic
rollback after a failure, refusal of a wrong version and of a changed patch file, the offline OCR runtime (unpacked from
the bundled wheels into a staging folder, swapped in, the previous runtime restored by rollback), the side-by-side
install for a protected application folder, the browser extension update files, and that the installer needs no
network and no administrator rights.

Not verified here (no Windows in this environment): the Windows wrappers (APPLY_PATCH.cmd / .ps1) and the OCR engine
probe with the bundled Windows wheels on Windows / Python 3.13. On Linux the installer reports the OCR probe as SKIP;
the unpacking, swap and rollback of the runtime are exercised with --force-ocr.
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
NAME = "SkyGuard_Integration_Comparison_OCR_Browser_Final_Patch"
WHEELS = Path(os.environ.get("SKYGUARD_OCR_WHEELS", "/tmp/claude-0/wheels/win"))
PKG = ROOT / "packaging" / "cumulative-patch"
RELEASES = {"23.3.35": "6d448fa", "23.4.0": "53e80093fe99272f27dc424392d1fd3d746b3c7c", "23.5.0": "900e4e0",
            "23.6.0": "c229f6b", "23.6.1": "e344315", "23.7.0": "fbefa64", "23.7.1": "a15c01f", "23.7.2": "51a9a29",
            "23.8.0": "c838bf8"}
AGENT = ("23.5.0", "23.6.0", "23.6.1", "23.7.0", "23.7.1", "23.7.2", "23.8.0")  # versions that have the Operations Agent (skyguard_agent)


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    try:
        for commit in RELEASES.values():
            _git("cat-file", "-e", commit)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("the release commits are not in this checkout")
    if not list(WHEELS.glob("rapidocr-*.whl")):
        pytest.skip(f"the Windows OCR wheels are not in {WHEELS} (set SKYGUARD_OCR_WHEELS)")
    out = tmp_path_factory.mktemp("build")
    r = subprocess.run([sys.executable, str(PKG / "build_patch.py"), "--out", str(out), "--ocr-wheels", str(WHEELS)],
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
    assert manifest["version"] == "23.9.0"
    assert manifest["baseline"] == ["23.3.35", "23.4.0", "23.5.0", "23.6.0", "23.6.1", "23.7.0", "23.7.1", "23.7.2",
                                    "23.8.0"]
    wheels = json.loads((built / "ocr-runtime" / "WHEELS.json").read_text())
    assert manifest["ocr_wheels"] == len(wheels) >= 5
    assert any(w.startswith("rapidocr-") for w in wheels) and any(w.startswith("onnxruntime-") for w in wheels)
    assert manifest["extension_version"] == "1.1.0"
    files = manifest["files"]
    for must in ("qa_platform/__init__.py", "qa_platform/automation/components.py",
                 "qa_platform/automation/js/components.js", "qa_platform/automation/semantic.py",
                 "qa_platform/design/service.py", "skyguard_agent/agent.py", "skyguard_agent/locator.py",
                 "web/js/pages/ops-agent.js", "web/js/pages/history.js", "skyguard_agent/understanding.py",
                 "skyguard_agent/journal.py", "skyguard_agent/assistant.py", "skyguard_agent/store.py",
                 "qa_platform/api/opsagent.py", "qa_platform/opsagent/service.py", "qa_platform/automation/runner.py",
                 "qa_platform/automation/js/recorder.js", "qa_platform/automation/recorder.py",
                 "qa_platform/automation/pytest_export.py", "web/js/pages/automation.js",
                 "qa_platform/automation/framework/steps.py", "qa_platform/automation/framework/walker.py",
                 "qa_platform/automation/framework/builder.py", "qa_platform/automation/framework/studio.py",
                 "qa_platform/automation/framework/template/utilities/reporting.py",
                 "qa_platform/automation/framework/template/utilities/locators.py",
                 "qa_platform/comparison/workspace.py", "qa_platform/comparison/formula.py",
                 "qa_platform/comparison/screen_reader.py", "qa_platform/api/workspace.py",
                 "web/js/pages/integration-workspace.js", "qa_platform/storage/db.py",
                 "qa_platform/comparison/filters.py", "qa_platform/comparison/semantics.py",
                 "qa_platform/comparison/ocr.py", "qa_platform/comparison/ocr_worker.py",
                 "qa_platform/comparison/extract_web.py", "qa_platform/automation/browser.py"):
        assert must in files, must
    assert not any("__pycache__" in f or f.startswith(("tests/", "data/")) for f in files)
    assert "web/js/pages/comparison.js" not in files  # the separate comparison pages were removed
    for tool in ("APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py",
                 "UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1", "DIAGNOSE_SITE.cmd", "DIAGNOSE_SITE.ps1",
                 "VERIFY_LIVE_SITE.cmd", "VERIFY_LIVE_SITE.ps1", "SHA256_CHECKSUM.txt"):
        assert (built / tool).is_file(), tool
    for crlf in ("APPLY_PATCH.cmd", "UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1", "DIAGNOSE_SITE.cmd", "DIAGNOSE_SITE.ps1"):
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
    assert "tests/site/tripcom/flights.html" in sums and "previous-23.7.0/PATCH_CHANGELOG.md" in sums
    assert "previous-23.6.1/ROOT_CAUSE_REPORT.md" in sums and "previous-23.7.1/ROOT_CAUSE_REPORT.md" in sums
    assert "PATCH_CHANGELOG.md" in sums and "USER_GUIDE.md" in sums and "tests/site/saucedemo/checkout-step-two.html" in sums
    assert "previous-23.8.0/PATCH_CHANGELOG.md" in sums and "ROOT_CAUSE_REPORT.md" in sums
    assert "evidence/lineage-4-dubai-fail-detail.png" in sums and "evidence/filters-3-fetched.png" in sums
    assert "KNOWN_LIMITATIONS.md" in sums and "SECURITY_REVIEW.md" in sums and "TEST_REPORT.md" in sums
    assert "tests/site/integration_live/filters.html" in sums and "tests/site/integration_ocr/truth.json" in sums
    assert any(k.startswith("ocr-runtime/wheels/rapidocr-") for k in sums)
    assert "tests/test_ai_automation_steps_23720.py" in sums and "previous-23.7.2/PATCH_CHANGELOG.md" in sums
    assert "tests/test_integration_workspace_23800.py" in sums and "tests/site/integration/app_c.html" in sums
    assert "tests/test_integration_ocr_browser_23900.py" in sums and "tests/test_cumulative_patch_23900.py" in sums
    app = built / "app" / "qa_platform"
    assert '__version__ = "23.9.0"' in (app / "__init__.py").read_text()
    assert "class FilterSession" in (app / "comparison" / "filters.py").read_text()
    assert "class Workspace" in (app / "comparison" / "workspace.py").read_text()
    assert "def plan_steps" in (app / "automation" / "framework" / "steps.py").read_text()
    assert "_counter_row_click" in (app / "automation" / "runner.py").read_text()
    assert "set_count" in (built / "app" / "web" / "js" / "pages" / "automation.js").read_text()


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
    assert r.returncode == 1 and "installs on 23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2 or 23.8.0" in r.stderr
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
    assert not (app / "skyguard_agent").exists() and not (app / "PATCH_FILES_23.9.0.json").exists()
    assert _rows(data / "qa_platform.sqlite3") == before


def _knowledge(data: Path) -> tuple[int, set[str], int]:
    conn = sqlite3.connect(data / "opsagent" / "knowledge.sqlite3")
    try:
        return (conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0])
    finally:
        conn.close()


@pytest.mark.parametrize("version", list(RELEASES))
def test_apply_verify_rollback_and_apply_again(built, tmp_path, version):
    app, data = _install(tmp_path / "inst", version)
    before = _rows(data / "qa_platform.sqlite3")
    r = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _version(app) == "23.9.0"
    assert "Comparison 23.9.0 (filters, meaning)" in r.stdout and "Maximized browser policy" in r.stdout
    assert "Offline OCR engine" in r.stdout  # SKIP on Linux: the bundled runtime is for Windows / Python 3.13
    assert "Passive learning engine (23.7.0)" in r.stdout and "Component engine (23.7.1)" in r.stdout
    assert "Step reader (23.7.2)" in r.stdout and "Integration Comparison (23.8.0)" in r.stdout
    assert "FAIL" not in r.stdout
    kver, ktables, kapps = _knowledge(data)
    assert kver == 2 and {"observations", "app_model"} <= ktables
    assert kapps == (1 if version in AGENT else 0)  # the agent's earlier knowledge is kept
    after = _rows(data / "qa_platform.sqlite3")
    assert all(after.get(t, 0) >= n for t, n in before.items())
    conn = sqlite3.connect(data / "qa_platform.sqlite3")
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 19
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"cmp_ws_screens", "cmp_ws_state", "cmp_ws_configs", "cmp_ws_decisions"} <= tables
    conn.close()

    v = _run(built, "verify", "--app", str(app), "--data", str(data))
    assert v.returncode == 0 and "FAIL" not in v.stdout, v.stdout + v.stderr

    rb = _run(built, "rollback", "--app", str(app), "--data", str(data))
    assert rb.returncode == 0, rb.stdout + rb.stderr
    assert _version(app) == version
    assert (app / "skyguard_agent" / "understanding.py").exists() == (version in ("23.7.0", "23.7.1", "23.7.2", "23.8.0"))
    assert (app / "skyguard_agent").exists() == (version in AGENT)
    assert (app / "qa_platform" / "automation" / "components.py").exists() == (version in ("23.6.0", "23.6.1", "23.7.0",
                                                                                             "23.7.1", "23.7.2", "23.8.0"))
    assert ("_counter_row_click" in (app / "qa_platform" / "automation" / "runner.py").read_text()) is (version in ("23.7.1", "23.7.2", "23.8.0"))
    assert (app / "qa_platform" / "automation" / "framework" / "steps.py").exists() == (version in ("23.7.2", "23.8.0"))
    assert (app / "qa_platform" / "comparison" / "workspace.py").exists() == (version == "23.8.0")
    assert not (app / "qa_platform" / "comparison" / "filters.py").exists()
    assert _knowledge(data)[0] == 2  # data is never deleted by a rollback
    if version in AGENT:  # the older agent opens the schema 2 knowledge database as it is
        out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from pathlib import Path; "
                              "from qa_platform.config import Paths; from skyguard_agent.store import Store; "
                              "s = Store(Path(Paths().ensure().root) / 'opsagent' / 'knowledge.sqlite3'); "
                              "print(s.query_one('SELECT COUNT(*) AS n FROM applications')['n']); s.close()"],
                             cwd=app, env={**os.environ, "QA_HOME": str(data)}, capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout.strip() == "1", out.stderr

    again = _run(built, "apply", "--app", str(app), "--data", str(data))
    assert again.returncode == 0, again.stdout + again.stderr
    assert _version(app) == "23.9.0"
    assert json.loads((app / "PATCH_HISTORY.json").read_text())[-1] == {**json.loads(
        (app / "PATCH_HISTORY.json").read_text())[-1], "version": "23.9.0", "from": version}


# ---- 23.9.0: the offline OCR runtime and protected application folders -----------------------------------------------

def test_ocr_runtime_is_staged_swapped_and_rolled_back(built, tmp_path):
    app, data = _install(tmp_path / "inst", "23.8.0")
    site = app / "runtime" / "ocr" / "site-packages"
    site.mkdir(parents=True)
    (site / "OLD_RUNTIME.txt").write_text("the runtime that was there before")
    r = _run(built, "apply", "--app", str(app), "--data", str(data), "--force-ocr")
    assert r.returncode == 0, r.stdout + r.stderr
    for pkg in ("rapidocr", "onnxruntime", "cv2", "numpy", "PIL"):
        assert (site / pkg).is_dir(), pkg
    assert not (site / "OLD_RUNTIME.txt").exists()
    assert json.loads((site / "SKYGUARD_OCR_RUNTIME.json").read_text())["patch"] == "23.9.0"
    # the Microsoft C++ runtime that the OCR worker loads before ONNX Runtime and OpenCV (older System32 copies
    # end the worker with an access violation on Windows 10 PCs without a recent Visual C++ redistributable)
    for dll in ("msvcp140.dll", "msvcp140_1.dll", "concrt140.dll"):
        assert (site / "_msvc" / dll).is_file(), dll
    assert not (site / "Scripts").exists() and not list(site.glob("*.dll"))
    assert not list(site.parent.glob("site-packages.staging-*"))       # the staging folder became the runtime
    assert list(site.parent.glob("site-packages.before-23.9.0-*"))     # the previous runtime is kept for rollback
    # the application finds it where it looks (qa_platform.comparison.ocr.runtime_sites)
    out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); from qa_platform.comparison import ocr; "
                          "print(ocr._bundled_site())"], cwd=app, env={**os.environ, "QA_HOME": str(data)},
                         capture_output=True, text=True)
    assert out.stdout.strip() == str(site), out.stderr
    rb = _run(built, "rollback", "--app", str(app), "--data", str(data))
    assert rb.returncode == 0, rb.stdout + rb.stderr
    assert (site / "OLD_RUNTIME.txt").read_text() == "the runtime that was there before"
    assert not (site / "rapidocr").exists()


def test_failed_apply_restores_the_previous_ocr_runtime(built, tmp_path):
    stage = tmp_path / "patch"
    shutil.copytree(built, stage)
    app, data = _install(tmp_path / "inst", "23.8.0")
    site = app / "runtime" / "ocr" / "site-packages"
    site.mkdir(parents=True)
    (site / "OLD_RUNTIME.txt").write_text("before")
    # make the verification fail after the runtime was swapped in: a broken workspace module
    bad = stage / "app" / "qa_platform" / "comparison" / "filters.py"
    bad.write_text(bad.read_text() + "\nraise RuntimeError('broken on purpose')\n")
    manifest = json.loads((stage / "PATCH_MANIFEST.json").read_text())
    import hashlib
    manifest["files"]["qa_platform/comparison/filters.py"] = hashlib.sha256(bad.read_bytes()).hexdigest()
    (stage / "PATCH_MANIFEST.json").write_text(json.dumps(manifest))
    r = _run(stage, "apply", "--app", str(app), "--data", str(data), "--force-ocr")
    assert r.returncode == 1 and "previous version was restored" in r.stderr, r.stdout + r.stderr
    assert _version(app) == "23.8.0"
    assert (site / "OLD_RUNTIME.txt").read_text() == "before" and not (site / "rapidocr").exists()


def test_damaged_ocr_wheel_is_refused(built, tmp_path):
    stage = tmp_path / "patch"
    shutil.copytree(built, stage)
    w = next((stage / "ocr-runtime" / "wheels").glob("rapidocr-*.whl"))
    w.write_bytes(w.read_bytes()[:-10])
    app, data = _install(tmp_path / "inst", "23.8.0")
    r = _run(stage, "apply", "--app", str(app), "--data", str(data), "--force-ocr")
    assert r.returncode == 1 and "OCR runtime files are damaged" in r.stderr
    assert _version(app) == "23.8.0"


def test_protected_application_folder_is_never_forced_and_side_by_side_works(built, tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_patch_239", built / "install_patch.py")
    ip = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ip)
    app, data = _install(tmp_path / "inst", "23.8.0")
    real = ip.writable
    monkeypatch.setattr(ip, "writable", lambda folder: False if Path(folder).resolve() == app.resolve() else real(folder))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    rc = ip.main(["apply", "--app", str(app), "--data", str(data), "--skip-running-check"])
    assert rc == 1 and _version(app) == "23.8.0"                        # nothing changed, the user is told why
    rc = ip.main(["apply", "--app", str(app), "--data", str(data), "--skip-running-check", "--side-by-side"])
    assert rc == 0
    copy = tmp_path / "local" / "SkyGuardQA" / "app-23.9.0"
    assert _version(copy) == "23.9.0" and _version(app) == "23.8.0"   # the original is untouched
    launcher = (copy / "START_SKYGUARD_23.9.0.cmd").read_bytes()
    assert b"\r\n" in launcher and str(data).encode() in launcher
