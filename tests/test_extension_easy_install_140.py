"""Extension 1.4.0: install without typing a pairing code, creating a user or signing in.

The installer leaves a one-time install ticket in the extension folder of the person's own profile; the extension pairs
with it by itself and signs in as this PC's owner (POST /v1/auth/device). Checked here on the real companion server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_companion_security import EXT, call, comp, paired  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser-extension" / "companion"))


def test_ticket_pairs_and_signs_in_as_the_owner_without_a_password(comp):  # noqa: F811
    c, base, _ = comp
    owner = c.users.owner_for_ticket("Muhammad Tufail")          # a fresh PC: an admin named after the Windows user
    assert owner == "MuhammadTufail"
    ticket = c.pairing.new_ticket(owner)
    st, out = call(base, "POST", "/v1/pair", {"ticket": ticket, "name": "Edge"})
    assert st == 200 and out["owner"] == owner, out
    dev = {"X-SG-Device": out["device_id"], "X-SG-Secret": out["device_secret"]}
    st, login = call(base, "POST", "/v1/auth/device", {}, headers=dev)
    assert st == 200 and login["user"] == {"username": owner, "role": "admin"}, login
    st, apps = call(base, "GET", "/v1/applications", headers={**dev, "Authorization": "Bearer " + login["token"]})
    assert st == 200, apps
    assert call(base, "POST", "/v1/pair", {"ticket": ticket})[0] == 409            # single use


def test_ticket_is_refused_when_wrong_from_another_origin_or_too_often(comp):  # noqa: F811
    c, base, _ = comp
    ticket = c.pairing.new_ticket(None)
    assert call(base, "POST", "/v1/pair", {"ticket": ticket}, origin="https://evil.example")[0] == 403
    assert call(base, "POST", "/v1/pair", {"ticket": ticket}, origin=None)[0] == 403
    for _ in range(5):
        assert call(base, "POST", "/v1/pair", {"ticket": "x" * 43})[0] in (401, 429)
    assert call(base, "POST", "/v1/pair", {"ticket": ticket})[0] in (409, 429)     # locked after 5 attempts


def test_a_device_paired_with_a_code_never_signs_in_without_a_password(comp):  # noqa: F811
    c, base, _ = comp
    _auth, dev = paired(comp)
    st, out = call(base, "POST", "/v1/auth/device", {}, headers=dev)
    assert st == 401 and out["error_code"] == "AUTH_REQUIRED"


def test_owner_is_the_only_admin_and_none_when_several(comp):  # noqa: F811
    c, base, _ = comp
    c.users.create("boss", "Boss-pass-2026", "admin")
    assert c.users.owner_for_ticket("someone") == "boss"
    c.users.create("boss2", "Boss-pass-2026", "admin")
    assert c.users.owner_for_ticket("someone") is None
    ticket = c.pairing.new_ticket(None)
    st, out = call(base, "POST", "/v1/pair", {"ticket": ticket})
    assert st == 200
    dev = {"X-SG-Device": out["device_id"], "X-SG-Secret": out["device_secret"]}
    assert call(base, "POST", "/v1/auth/device", {}, headers=dev)[0] == 401     # signs in with a password instead


def test_install_ticket_command_writes_the_auto_setup_file(tmp_path):
    out = tmp_path / "auto-setup.json"
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "lad"), "SKYGUARD_AGENT_HOME": str(tmp_path / "home"),
           "USERNAME": "tester"}
    r = subprocess.run([sys.executable, "-m", "skyguard_companion", "install-ticket", str(out)], env=env,
                       cwd=str(ROOT / "browser-extension" / "companion"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr + r.stdout
    data = json.loads(out.read_text())
    assert set(data) == {"port", "ticket"} and len(data["ticket"]) >= 40
    assert "as tester" in r.stdout


# ---- the extension itself, loaded unpacked into Chromium, set up by the installer's file -------------------------

def test_extension_pairs_and_signs_in_by_itself(tmp_path):
    import threading
    import time

    import pytest
    from playwright.sync_api import sync_playwright

    from skyguard_agent.vault import MemoryBackend, Vault
    from skyguard_companion.config import Paths
    from skyguard_companion.server import Companion, serve
    from tests.test_companion_security import Off, free_port
    from tests.test_extension_e2e import extension_copy, wait_text

    ext = extension_copy(tmp_path)
    port = free_port()
    paths = Paths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(json.dumps({"port": port}))
    comp = Companion(paths, vault=Vault(MemoryBackend()), llm=Off(), whisper=Off())
    httpd = serve(comp)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    owner = comp.users.owner_for_ticket("tester")                     # what INSTALL.cmd does (install-ticket)
    (ext / "auto-setup.json").write_text(json.dumps({"port": port, "ticket": comp.pairing.new_ticket(owner)}))
    try:
        with sync_playwright() as pw:
            try:
                ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium",
                                                            headless=True,
                                                            args=[f"--disable-extensions-except={ext}",
                                                                  f"--load-extension={ext}"])
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Chromium with extensions is not available: {exc}")
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            base = sw.url.rsplit("/", 1)[0]
            end = time.time() + 30                                     # nothing opened, nothing typed
            while time.time() < end and not comp.pairing.devices():
                time.sleep(0.3)
            devices = comp.pairing.devices()
            assert devices and devices[0]["owner"] == owner and devices[0]["via"] == "install", devices
            p = ctx.new_page()
            errors = []
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.goto(base + "/options.html")
            wait_text(p, "#conn", f"{owner} (admin)")                  # signed in, no password
            assert not errors, errors
            ctx.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        comp.close()
