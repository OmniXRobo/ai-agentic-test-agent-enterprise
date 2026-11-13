"""23.6.0: "Open script" for AI-Driven framework scripts (FWS-...) and clear reasons when a script file is missing,
of the wrong type, unreadable or not text. Opening a script never creates or overwrites anything."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from qa_platform.automation.framework.workspace import FrameworkWorkspace
from qa_platform.errors import QAError
from tests.test_studio import generate, needs_browser, studio_env  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and ".versions" not in p.parts}


# ---- routing -----------------------------------------------------------------------------------


def test_history_and_recorded_pages_route_framework_scripts_to_their_page():
    history = (ROOT / "web/js/pages/history.js").read_text(encoding="utf-8")
    assert 'href="#/guided-automation?script=${esc(execution.script_id)}"' not in history
    assert "scriptHref(execution.script_id)" in history and '#/ai-automation?script=' in history
    automation = (ROOT / "web/js/pages/automation.js").read_text(encoding="utf-8")
    assert 'scriptId.startsWith("FWS-")' in automation and "The script could not be opened" in automation
    studio = (ROOT / "web/js/pages/script-studio.js").read_text(encoding="utf-8")
    assert "data.file_problem" in studio and "The script could not be opened" in studio


def test_recorded_script_api_explains_a_framework_id(client):
    r = client.get("/api/automation/scripts/FWS-B211AC4E14")
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "SCRIPT_ID_WRONG_PAGE" and "AI-Driven" in body["message"]
    assert body["details"]["open"] == "#/ai-automation?script=FWS-B211AC4E14"
    r = client.get("/api/automation/scripts/not-an-id")
    assert r.status_code == 400 and "SCR-" in r.json()["message"]
    assert client.get("/api/automation/scripts/SCR-0000000000").status_code == 404


# ---- workspace reads ---------------------------------------------------------------------------


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "Mes scripts ü 测试 folder"  # spaces and non-ASCII characters in the install path
    w = FrameworkWorkspace(root)
    (root / "tests").mkdir(parents=True)
    return w


def test_read_handles_unicode_paths_bom_and_windows_1252(ws):
    (ws.root / "tests" / "a.py").write_bytes("\ufeff# Café ✓\nprint('ok')\n".encode("utf-8"))
    assert ws.read("tests/a.py").startswith("# Café ✓")
    (ws.root / "tests" / "b.py").write_bytes("# Caf\xe9 \x93quoted\x94\n".encode("latin-1"))
    assert ws.read("tests/b.py") == "# Café \u201cquoted\u201d\n"


def test_read_missing_file_names_path_and_reason(ws):
    with pytest.raises(QAError) as e:
        ws.read("tests/missing.py")
    assert e.value.error_code == "FRAMEWORK_FILE_NOT_FOUND" and e.value.status == 404
    assert e.value.details["reason"] == "missing" and "Mes scripts ü 测试 folder" in e.value.details["path"]
    (ws.root / "tests" / "dir.py").mkdir()
    with pytest.raises(QAError) as e:
        ws.read("tests/dir.py")
    assert e.value.details["reason"] == "not a file"


def test_read_permission_denied_is_explained(ws, monkeypatch):
    target = ws.root / "tests" / "locked.py"
    target.write_text("x = 1\n", encoding="utf-8")
    real = Path.read_bytes

    def locked(self):
        if self == target:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked)
    with pytest.raises(QAError) as e:
        ws.read("tests/locked.py")
    assert e.value.error_code == "FRAMEWORK_FILE_UNREADABLE" and e.value.status == 403
    assert "permission denied" in e.value.message and e.value.details["path"].endswith("locked.py")


def test_read_binary_file_is_refused_not_garbled(ws):
    (ws.root / "tests" / "bin.py").write_bytes(b"\x81\x8d\x8f\x90\x9d\x00\xff")
    with pytest.raises(QAError) as e:
        ws.read("tests/bin.py")
    assert e.value.error_code == "FRAMEWORK_FILE_ENCODING"


def test_traversal_and_bad_names_are_refused(ws):
    for bad in ("../x.py", "/etc/passwd", "C:/x.py", "tests/../../x.py", ".versions/x.py"):
        with pytest.raises(QAError) as e:
            ws.read(bad)
        assert e.value.error_code == "PROJECT_PATH_INVALID"


# ---- opening a generated framework script --------------------------------------------------------


@needs_browser
def test_open_framework_script_reads_only_and_explains_problems(studio_env):  # noqa: F811
    c, rt = studio_env["c"], studio_env["rt"]
    job = generate(studio_env)
    assert job["status"] == "COMPLETED", job
    inv = {i["test_case_id"]: i for i in c.get("/api/automation/studio/inventory").json()["items"] if i["framework"]}
    sid = inv["1280"]["id"]
    root = rt.studio.workspace().root
    before = _digest(root)

    body = c.get(f"/api/automation/studio/scripts/{sid}").json()
    assert body["file_problem"] is None and body["script"]["file_verified"] and body["source"]
    assert _digest(root) == before  # opening wrote nothing

    rel = body["script"]["file"]
    (root / rel).rename(root / (rel + ".moved"))
    body = c.get(f"/api/automation/studio/scripts/{sid}").json()
    assert body["script"]["status"] == "FILE MISSING" and not body["script"]["file_verified"]
    assert body["file_problem"]["path"].endswith(Path(rel).name) and "missing" in body["file_problem"]["reason"]
    assert not (root / rel).exists()  # never recreated as an empty script

    (root / (rel + ".moved")).rename(root / rel)
    rt.db.execute("UPDATE framework_scripts SET file = ? WHERE id = ?", (rel[:-3] + ".txt", sid))
    body = c.get(f"/api/automation/studio/scripts/{sid}").json()
    assert body["script"]["status"] == "FILE INVALID" and ".py" in body["file_problem"]["reason"]
    rt.db.execute("UPDATE framework_scripts SET file = ? WHERE id = ?", (rel, sid))

    (root / rel).write_bytes(b"\x81\x8d\x8f\x90\x9d\x00\xff")
    body = c.get(f"/api/automation/studio/scripts/{sid}").json()
    assert body["script"]["status"] == "FILE UNREADABLE" and "not a text file" in body["file_problem"]["reason"]
    assert (root / rel).read_bytes() == b"\x81\x8d\x8f\x90\x9d\x00\xff"  # left exactly as it was
