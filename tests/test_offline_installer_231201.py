"""23.12.1: the full offline installer survives Windows holding new files (WinError 5 on app.new -> app).

Reported on a new Windows 11 PC: INSTALL.cmd of the 23.3.35 package copied 8,789 files into app.new and stopped with a
traceback, "PermissionError: [WinError 5] Access is denied: '...\\app.new' -> '...\\app'", twice (also after deleting
the folder and restarting). A first install now copies straight into app (no folder rename at all); an upgrade
retries the switch and copies into place when the rename keeps failing; nothing ends in a traceback.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from qa_platform.offline import install as inst
from qa_platform.offline import manifest as mf

NO_WAIT = {"sleep": lambda s: None}


def _payload(root: Path, version: str = "1.0.0") -> Path:
    (root / "qa_platform").mkdir(parents=True)
    (root / "qa_platform" / "__init__.py").write_text(f"__version__ = '{version}'\n")
    (root / "runtime" / "python").mkdir(parents=True)
    (root / "runtime" / "python" / "python.exe").write_bytes(b"MZ fake")
    (root / "START_QA_PLATFORM.cmd").write_text("@echo off\r\n")
    mf.write(root, {"name": "SkyGuard QA", "version": version, "built_at": "2026-11-19T10:00:00+04:00",
                    "python": "3.13.17", "components": [], "packages": []})
    return root


def _ok(app: Path, data: Path, quick: bool):
    return True, f"RESULT: PASS ({'quick' if quick else 'full'}) {app.name}"


def _version(app: Path) -> str:
    return json.loads((app / "INSTALLED.json").read_text())["version"]


def _log(target: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted((target / "logs").glob("install-*.log")))


def _deny_renames(monkeypatch, only=None):
    """Windows refusing to rename a folder, as antivirus does while it scans new files."""
    real = Path.rename
    calls = []

    def rename(self, target):
        calls.append((Path(self).name, Path(target).name))
        if only is None or only(Path(self), Path(target)):
            raise PermissionError(5, "Access is denied", str(self), None, str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    return calls


def test_first_install_renames_no_folder(tmp_path, monkeypatch, capsys):
    target = tmp_path / "Programs" / "AIAgenticTestAgent"
    calls = _deny_renames(monkeypatch)
    assert inst.install(_payload(tmp_path / "pkg", "23.12.1"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    assert calls == []  # the reported failure needs a folder rename; a first install does none
    assert _version(target / "app") == "23.12.1"
    assert not (target / "app.new").exists() and not (target / inst.INCOMPLETE_MARKER).exists()
    assert (target / "START_QA_PLATFORM.cmd").exists() and (target / "data").is_dir()
    assert "Traceback" not in capsys.readouterr().out


def test_first_install_removes_the_app_new_left_by_the_old_installer(tmp_path):
    target = tmp_path / "t"
    left = target / "app.new" / "runtime"
    left.mkdir(parents=True)
    (left / "file.txt").write_text("from the failed 23.3.35 install")
    assert inst.install(_payload(tmp_path / "pkg"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    assert not (target / "app.new").exists() and (target / "app" / "INSTALLED.json").exists()
    assert "left over from an earlier install" in _log(target)


def test_unfinished_first_install_starts_again(tmp_path):
    target = tmp_path / "t"
    (target / "app" / "qa_platform").mkdir(parents=True)  # half copied
    (target / inst.INCOMPLETE_MARKER).write_text("in progress")
    assert inst.install(_payload(tmp_path / "pkg"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    assert _version(target / "app") == "1.0.0" and not (target / "app.previous").exists()
    assert not (target / inst.INCOMPLETE_MARKER).exists()


def test_first_install_that_fails_validation_leaves_nothing_half_installed(tmp_path):
    target = tmp_path / "t"

    def full_fails(app, data, quick):
        return (True, "quick ok") if quick else (False, "RESULT: FAIL - Chromium did not start")

    assert inst.install(_payload(tmp_path / "pkg"), target, validation=full_fails, shortcuts=False, **NO_WAIT) == 1
    assert not (target / "app").exists() and not (target / inst.INCOMPLETE_MARKER).exists()


def test_upgrade_retries_the_switch_until_windows_lets_go(tmp_path, monkeypatch):
    target = tmp_path / "t"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    failures = {"n": 0}

    def busy(src, dst):
        if src.name == "app.new" and failures["n"] < 5:
            failures["n"] += 1
            return True
        return False

    _deny_renames(monkeypatch, busy)
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    assert _version(target / "app") == "2.0.0" and _version(target / "app.previous") == "1.0.0"
    assert failures["n"] == 5 and "done after 6 attempts" in _log(target)


def test_upgrade_copies_into_place_when_the_rename_never_works(tmp_path, monkeypatch):
    target = tmp_path / "t"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    calls = _deny_renames(monkeypatch, lambda src, dst: src.name == "app.new")
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    assert len([c for c in calls if c[0] == "app.new"]) == len(inst.RETRY_DELAYS) + 1
    assert _version(target / "app") == "2.0.0" and _version(target / "app.previous") == "1.0.0"
    assert not (target / "app.new").exists()
    assert "Copying its files into place instead" in _log(target)


def test_upgrade_switches_back_when_the_new_version_cannot_be_put_in_place(tmp_path, monkeypatch):
    target = tmp_path / "t"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    _deny_renames(monkeypatch, lambda src, dst: src.name == "app.new")

    def copy_denied(src, dst, *a, **k):
        if Path(src).name == "app.new":
            raise PermissionError(5, "Access is denied")
        return real_copytree(src, dst, *a, **k)

    real_copytree = inst.shutil.copytree
    monkeypatch.setattr(inst.shutil, "copytree", copy_denied)
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 1
    assert _version(target / "app") == "1.0.0"  # the current version is unchanged
    text = _log(target)
    assert "Your current version is unchanged" in text and "Wait two minutes" in text


def test_upgrade_backs_up_the_data_first(tmp_path):
    target = tmp_path / "t"
    assert inst.install(_payload(tmp_path / "v1"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    db = target / "data" / "qa_platform.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    con.close()
    (target / "data" / "settings.json").write_text('{"server": {"port": 8899}}')
    assert inst.install(_payload(tmp_path / "v2", "2.0.0"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    backups = list((target / "backups").glob("before-2.0.0-*"))
    assert len(backups) == 1
    copy = sqlite3.connect(backups[0] / "qa_platform.sqlite3")
    assert copy.execute("SELECT x FROM t").fetchone() == (42,)
    copy.close()
    assert (backups[0] / "settings.json").read_text() == '{"server": {"port": 8899}}'
    assert (target / "data" / "settings.json").read_text() == '{"server": {"port": 8899}}'


def test_unexpected_access_denied_is_a_plain_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    target = tmp_path / "t"

    def denied(*a, **k):
        raise PermissionError(5, "Access is denied", str(target / "app"))

    monkeypatch.setattr(inst.shutil, "copytree", denied)
    assert inst.install(_payload(tmp_path / "pkg"), target, validation=_ok, shortcuts=False, **NO_WAIT) == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out and "Wait two minutes" in out and "INSTALL.cmd again" in out
    assert "Traceback" in _log(target)  # the details stay in the log for support


def test_main_never_prints_a_traceback(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(inst, "install", boom)
    assert inst.main(["install", "--target", str(tmp_path / "t")]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err and "Install stopped" in err and "Wait two minutes" in err


def test_rollback_also_retries(tmp_path, monkeypatch):
    target = tmp_path / "t"
    for n, v in enumerate(("1.0.0", "2.0.0")):
        assert inst.install(_payload(tmp_path / f"v{n}", v), target, validation=_ok, shortcuts=False, **NO_WAIT) == 0
    left = {"n": 2}

    def busy(src, dst):
        if src.name == "app" and left["n"]:
            left["n"] -= 1
            return True
        return False

    _deny_renames(monkeypatch, busy)
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)
    assert inst.rollback(target) == 0
    assert _version(target / "app") == "1.0.0" and _version(target / "app.previous") == "2.0.0"


def test_no_administrator_wording_in_what_people_read():
    from qa_platform.config import APP_ROOT

    texts = [APP_ROOT / "packaging" / "templates" / n for n in ("INSTALL_OFFLINE.cmd", "README_OFFLINE.txt",
                                                                 "VALIDATE_OFFLINE.cmd")]
    for path in texts:
        assert "no administrator rights" not in path.read_text(encoding="utf-8").lower(), path.name
    src = (APP_ROOT / "qa_platform" / "offline" / "install.py").read_text(encoding="utf-8").lower()
    assert "without administrator rights" not in src and "no administrator rights" not in src


# ---- the 23.12.0 upgrade Setup's switch steps (install_patch.py) --------------------------------------------------------


def _install_patch():
    import importlib.util

    from qa_platform.config import APP_ROOT

    spec = importlib.util.spec_from_file_location("install_patch_231201",
                                                  APP_ROOT / "packaging" / "cumulative-patch" / "install_patch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._sleep = lambda s: None
    return mod


def test_setup_ocr_switch_retries_then_copies(tmp_path, monkeypatch):
    ip = _install_patch()
    staging, live = tmp_path / "site-packages.staging", tmp_path / "site-packages"
    (staging / "rapidocr").mkdir(parents=True)
    (staging / "rapidocr" / "x.py").write_text("ok")
    real, tries = os.replace, {"n": 0}

    def denied(src, dst):
        if Path(src) == staging:
            tries["n"] += 1
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(ip.os, "replace", denied)
    assert ip.move_folder_into_place(staging, live) == "copied"
    assert (live / "rapidocr" / "x.py").read_text() == "ok" and not staging.exists()
    assert tries["n"] == len(ip.RETRY_DELAYS) + 1


def test_setup_file_replace_retries_until_free(tmp_path, monkeypatch):
    ip = _install_patch()
    src, dst = tmp_path / "a.patch-tmp", tmp_path / "a.py"
    src.write_text("new")
    dst.write_text("old")
    real, left = os.replace, {"n": 3}

    def busy(a, b):
        if left["n"]:
            left["n"] -= 1
            raise PermissionError(5, "Access is denied")
        return real(a, b)

    monkeypatch.setattr(ip.os, "replace", busy)
    ip.replace_retry(src, dst)
    assert dst.read_text() == "new" and not src.exists()


# ---- real Windows: a file held open inside the new folder gives exactly the reported WinError 5 ----------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows file locking")


@windows_only
def test_windows_open_file_blocks_the_folder_rename_as_reported(tmp_path):
    staged, app = tmp_path / "app.new", tmp_path / "app"
    staged.mkdir()
    (staged / "python.exe").write_bytes(b"MZ")
    with (staged / "python.exe").open("rb"):  # as antivirus does while it scans a new file
        with pytest.raises(PermissionError) as err:
            staged.rename(app)
    assert getattr(err.value, "winerror", None) == 5


@windows_only
def test_windows_switch_waits_for_the_scan_to_finish(tmp_path):
    staged, app = tmp_path / "app.new", tmp_path / "app"
    staged.mkdir()
    (staged / "python.exe").write_bytes(b"MZ")
    handle = (staged / "python.exe").open("rb")
    timer = threading.Timer(4, handle.close)  # the "scan" ends after four seconds
    timer.start()
    try:
        lines = []
        assert inst.switch_into_place(staged, app, lines.append, stop=lambda f, log: 0) == "renamed"
    finally:
        timer.cancel()
        handle.close()
    assert (app / "python.exe").read_bytes() == b"MZ" and not staged.exists()
    assert any("in use" in line for line in lines)


@windows_only
def test_windows_switch_copies_when_the_files_stay_open(tmp_path):
    staged, app = tmp_path / "app.new", tmp_path / "app"
    (staged / "runtime").mkdir(parents=True)
    (staged / "runtime" / "python.exe").write_bytes(b"MZ")
    with (staged / "runtime" / "python.exe").open("rb"):
        how = inst.switch_into_place(staged, app, lambda m: None, sleep=lambda s: None, stop=lambda f, log: 0)
    assert how == "copied" and (app / "runtime" / "python.exe").read_bytes() == b"MZ"
