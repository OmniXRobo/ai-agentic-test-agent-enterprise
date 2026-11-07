"""23.3.32: desktop start without a console, the app window, Exit, and the shortcut."""

from __future__ import annotations

import json

from qa_platform import launcher, startup
from qa_platform.api import app_control


class FakeLauncher:
    def __init__(self):
        self.opened = 0
        self.exited = False

    def open_window(self):
        self.opened += 1
        return True

    def exit(self):
        self.exited = True


def test_status_window_and_exit_need_the_desktop_start(client):
    st = client.get("/api/app/status").json()
    assert st["desktop"] is False and st["active_schedules"] == 0
    assert client.post("/api/app/exit").status_code == 409
    fake = FakeLauncher()
    client.app.state.launcher = fake
    try:
        assert client.get("/api/app/status").json()["closing_window_stops"] is True
        assert client.post("/api/app/window").json() == {"opened": True} and fake.opened == 1
        assert client.post("/api/app/exit").json() == {"stopping": True}
        import time
        time.sleep(0.8)
        assert fake.exited
    finally:
        del client.app.state.launcher


def test_an_active_schedule_keeps_the_application_running(client, rt, monkeypatch):
    assert app_control.active_schedules(rt) == 0
    monkeypatch.setattr(rt.perf_scheduler, "list", lambda: [{"enabled": True, "next_run_at": "2030-01-01T00:00:00"},
                                                            {"enabled": False, "next_run_at": None}])
    assert app_control.active_schedules(rt) == 1
    assert client.get("/api/app/status").json()["active_schedules"] == 1


def test_environment_port_and_window_command(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "runtime" / "ms-playwright" / "chromium-1200" / "chrome-win64").mkdir(parents=True)
    exe = app / "runtime" / "ms-playwright" / "chromium-1200" / "chrome-win64" / "chrome.exe"
    exe.write_bytes(b"")
    (app / "INSTALLED.json").write_text("{}")
    for k in ("QA_HOME", "PLAYWRIGHT_BROWSERS_PATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.setenv(k, "")  # registers the original value (or its absence) to restore after the test
        monkeypatch.delenv(k)
    monkeypatch.setattr(launcher, "installed_browser", lambda name: None)  # as on a PC without Edge or Chrome
    data = launcher.prepare_environment(app)
    assert data == tmp_path / "data"
    import os
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["PLAYWRIGHT_BROWSERS_PATH"].endswith("ms-playwright")
    assert launcher.find_window_browser(app) == [str(exe)]
    assert launcher.configured_port(data) == 8765
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"server": {"port": 9123}}))
    assert launcher.configured_port(data) == 9123
    cmd = launcher.window_command([str(exe)], "http://127.0.0.1:9123/", data / "window-profile")
    assert cmd[0] == str(exe) and "--app=http://127.0.0.1:9123/" in cmd
    assert any(a.startswith("--user-data-dir=") for a in cmd)


def test_hidden_children_flag_is_added_only_on_windows(monkeypatch):
    monkeypatch.setattr(launcher, "WIN", False)
    launcher.hide_child_windows()  # no change off Windows
    import subprocess
    assert not getattr(subprocess.Popen, "_qa_hidden", False)


def test_sign_in_start_uses_the_hidden_launcher_when_pythonw_is_there(tmp_path):
    app = tmp_path / "app"
    (app / "runtime" / "python").mkdir(parents=True)
    assert "START_QA_PLATFORM.cmd" in startup._content(app)
    (app / "runtime" / "python" / "pythonw.exe").write_bytes(b"")
    text = startup._content(app)
    assert "qa_platform.launcher --background" in text and "QA_NO_BROWSER=1" in text


def test_window_prefers_microsoft_edge(tmp_path, monkeypatch):
    """23.3.33: the included Chromium (Chrome for Testing) shows an "only for automated testing" bar, so the
    dashboard window opens in Edge when it is installed."""
    app = tmp_path / "app"
    bundled = app / "runtime" / "ms-playwright" / "chromium-1200" / "chrome-win64" / "chrome.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    pf = tmp_path / "pf86"
    for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA", "PLAYWRIGHT_BROWSERS_PATH"):
        monkeypatch.delenv(k, raising=False)
    assert launcher.find_window_browser(app) == [str(bundled)]
    edge = pf / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    monkeypatch.setenv("ProgramFiles(x86)", str(pf))
    assert launcher.find_window_browser(app) == [str(edge)]
    cmd = launcher.window_command([str(edge)], "http://127.0.0.1:8765/", tmp_path / "profile")
    assert "--disable-background-mode" in cmd  # closing the window ends the browser, so SkyGuard QA stops too
