"""Browser extension companion: the localhost API's security controls and the agent through it.

The companion runs in-process on a free 127.0.0.1 port with an in-memory vault (DPAPI exists only on Windows).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "browser-extension" / "companion"))

from skyguard_agent.vault import MemoryBackend, Vault  # noqa: E402
from skyguard_companion.config import DEFAULT_EXTENSION_IDS, Paths  # noqa: E402
from skyguard_companion.security import validate, Refused  # noqa: E402
from skyguard_companion.server import Companion, serve  # noqa: E402

EXT = f"chrome-extension://{DEFAULT_EXTENSION_IDS[0]}"
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Off:
    """No local model / speech server in the test environment."""
    cfg: dict = {}
    name = "off"

    def ensure(self):
        return {"state": "off"}

    def status(self):
        return {"state": "off"}

    def stop(self):
        pass

    def transcribe(self, audio, ctype):
        return "create 1 trip"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def comp(tmp_path):
    port = free_port()
    paths = Paths(tmp_path / "home")
    paths.ensure()
    paths.config_file.write_text(json.dumps({"port": port}))
    c = Companion(paths, vault=Vault(MemoryBackend()), llm=Off(), whisper=Off())
    httpd = serve(c)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield c, f"http://127.0.0.1:{port}", port
    httpd.shutdown()
    httpd.server_close()
    c.close()


def call(base, method, path, body=None, *, origin=EXT, headers=None, raw=None, ctype="application/json",
         host=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ctype} if data is not None else {}
    if origin:
        h["Origin"] = origin
    if host:
        h["Host"] = host
    h.update(headers or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with NO_PROXY.open(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def paired(comp):
    c, base, _ = comp
    code = c.pairing.new_code()
    st, out = call(base, "POST", "/v1/pair", {"code": code, "name": "test"})
    assert st == 200, out
    dev = {"X-SG-Device": out["device_id"], "X-SG-Secret": out["device_secret"]}
    st, out = call(base, "POST", "/v1/setup", {"username": "admin1", "password": "Admin-pass-2026"}, headers=dev)
    assert st == 200, out
    st, out = call(base, "POST", "/v1/auth/login", {"username": "admin1", "password": "Admin-pass-2026"}, headers=dev)
    assert st == 200, out
    return {**dev, "Authorization": "Bearer " + out["token"]}, dev


def test_health_is_the_only_unauthenticated_request(comp):
    c, base, port = comp
    assert call(base, "GET", "/health", origin=None)[0] == 200
    st, out = call(base, "GET", "/v1/hello", origin=None)
    assert st == 403 and out["error_code"] == "ORIGIN_REQUIRED"
    st, out = call(base, "GET", "/v1/hello", origin="https://evil.example")
    assert st == 403 and out["error_code"] == "ORIGIN_REFUSED"
    st, out = call(base, "GET", "/v1/hello", origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop")
    assert st == 403  # another extension
    assert call(base, "GET", "/v1/hello")[0] == 200


def test_dns_rebinding_host_is_refused(comp):
    c, base, port = comp
    st, out = call(base, "GET", "/health", origin=None, host=f"attacker.example:{port}")
    assert st == 403 and out["error_code"] == "HOST_REFUSED"


def test_listens_on_loopback_only(comp):
    c, base, port = comp
    with socket.socket() as s:
        s.settimeout(1)
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            pytest.skip("no other address")
        if ip.startswith("127."):
            pytest.skip("host name resolves to loopback")
        assert s.connect_ex((ip, port)) != 0


def test_pairing_code_single_use_and_attempt_limit(comp):
    c, base, _ = comp
    st, out = call(base, "POST", "/v1/pair", {"code": "00000000"})
    assert st == 409  # no code active
    code = c.pairing.new_code()
    wrong = "1" + code[1:] if code[0] != "1" else "2" + code[1:]
    assert call(base, "POST", "/v1/pair", {"code": wrong})[0] == 401
    assert call(base, "POST", "/v1/pair", {"code": code})[0] == 200
    assert call(base, "POST", "/v1/pair", {"code": code})[0] == 409  # single use
    code = c.pairing.new_code()
    statuses = [call(base, "POST", "/v1/pair", {"code": wrong})[0] for _ in range(6)]
    assert 429 in statuses
    assert call(base, "POST", "/v1/pair", {"code": code})[0] in (409, 429)  # locked after too many attempts


def test_requests_need_pairing_and_a_token(comp):
    c, base, _ = comp
    assert call(base, "GET", "/v1/applications")[1]["error_code"] == "NOT_PAIRED"
    auth, dev = paired(comp)
    assert call(base, "GET", "/v1/applications", headers=dev)[1]["error_code"] == "AUTH_REQUIRED"
    assert call(base, "GET", "/v1/applications", headers={**dev, "Authorization": "Bearer nope"})[0] == 401
    assert call(base, "GET", "/v1/applications", headers=auth)[0] == 200
    bad = {**auth, "X-SG-Secret": "wrong"}
    assert call(base, "GET", "/v1/applications", headers=bad)[1]["error_code"] == "NOT_PAIRED"
    # the setup endpoint works only once
    assert call(base, "POST", "/v1/setup", {"username": "x1x", "password": "Another-pass-1"}, headers=dev)[0] == 409


def test_token_expires_and_refresh(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    st, out = call(base, "POST", "/v1/auth/refresh", headers=auth, body={})
    assert st == 200
    old = auth["Authorization"]
    auth["Authorization"] = "Bearer " + out["token"]
    assert call(base, "GET", "/v1/auth/me", headers={**auth, "Authorization": old})[0] == 401  # rotated
    for s in c.users._tokens.values():
        s.expires = 0
    assert call(base, "GET", "/v1/auth/me", headers=auth)[1]["error_code"] == "AUTH_EXPIRED"


def test_login_lockout(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    codes = [call(base, "POST", "/v1/auth/login", {"username": "admin1", "password": "wrong-pass-xx"},
                  headers=dev)[0] for _ in range(7)]
    assert 429 in codes


def test_json_only_size_limits_and_schema(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    st, out = call(base, "POST", "/v1/applications", raw=b"name=x", ctype="application/x-www-form-urlencoded",
                   headers=auth)
    assert st == 415
    st, out = call(base, "POST", "/v1/applications", {"name": "A", "base_url": "http://127.0.0.1:9/", "evil": 1},
                   headers=auth)
    assert st == 400 and out["error_code"] == "FIELD_UNKNOWN"
    st, out = call(base, "POST", "/v1/chat", {"text": "x" * 300000}, headers=auth)
    assert st == 413
    with pytest.raises(Refused):
        validate({"n": True}, {"n": ("int", True, 5)})


def test_roles(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    assert call(base, "POST", "/v1/users", {"username": "viewer1", "password": "Viewer-pass-1", "role": "viewer"},
                headers=auth)[0] == 200
    st, out = call(base, "POST", "/v1/auth/login", {"username": "viewer1", "password": "Viewer-pass-1"}, headers=dev)
    viewer = {**dev, "Authorization": "Bearer " + out["token"]}
    assert call(base, "GET", "/v1/applications", headers=viewer)[0] == 200
    assert call(base, "POST", "/v1/applications", {"name": "A", "base_url": "http://127.0.0.1:9/"},
                headers=viewer)[0] == 403
    assert call(base, "PUT", "/v1/config", {"llm": {"mode": "off"}}, headers=viewer)[0] == 403
    # the model must stay on this PC
    st, out = call(base, "PUT", "/v1/config", {"llm": {"url": "https://api.example.com/v1"}}, headers=auth)
    assert st == 400 and out["error_code"] == "URL_NOT_LOCAL"


def test_password_in_chat_never_stored_or_logged(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    secret = "Sup3r$ecret-99"
    st, out = call(base, "POST", "/v1/chat", {"text": f"sign in with password: {secret}"}, headers=auth)
    assert st == 200 and out["reply"]["type"] == "secret_intercepted"
    st, hist = call(base, "GET", f"/v1/chat/{out['conversation']}", headers=auth)
    assert secret not in json.dumps(hist)
    for f in c.paths.root.rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes(), f


def test_credential_form_requires_consent_and_is_never_returned(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    app = call(base, "POST", "/v1/applications", {"name": "FleetOps", "base_url": "http://127.0.0.1:9/"},
               headers=auth)[1]["application"]
    acc = call(base, "POST", "/v1/accounts", {"application_id": app["id"], "label": "Operator",
                                              "login_id": "operator"}, headers=auth)[1]["account"]
    st, out = call(base, "PUT", f"/v1/accounts/{acc['id']}/credential", {"password": "Pw-123456!", "consent": False},
                   headers=auth)
    assert st == 400 and out["error_code"] == "CONSENT_REQUIRED"
    st, out = call(base, "PUT", f"/v1/accounts/{acc['id']}/credential", {"password": "Pw-123456!", "consent": True},
                   headers=auth)
    assert st == 200 and out["account"]["has_credential"]
    assert "Pw-123456!" not in json.dumps(call(base, "GET", "/v1/accounts", headers=auth)[1])
    assert "Pw-123456!" not in json.dumps(call(base, "GET", "/v1/audit", headers=auth)[1])
    assert call(base, "GET", "/v1/audit/verify", headers=auth)[1]["valid"] is True


def test_unsafe_application_urls_are_refused(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    for url in ("javascript:alert(1)", "file:///C:/Windows/win.ini", "chrome://settings", "data:text/html,x"):
        st, out = call(base, "POST", "/v1/applications", {"name": "Bad", "base_url": url}, headers=auth)
        assert st == 400, url


def test_voice_accepts_only_wav(comp):
    c, base, _ = comp
    auth, dev = paired(comp)
    st, out = call(base, "POST", "/v1/voice/transcribe", raw=b"RIFF....", ctype="audio/wav", headers=auth)
    assert st == 200 and out["text"] == "create 1 trip"
    st, out = call(base, "POST", "/v1/voice/transcribe", raw=b"x", ctype="text/plain", headers=auth)
    assert st == 415


def test_learn_while_browsing_through_the_companion(comp):
    """23.7.0: the extension's observations; secrets never stored, other sites dropped, roles enforced."""
    c, base, _ = comp
    auth, dev = paired(comp)
    app = call(base, "POST", "/v1/applications", {"name": "FleetOps", "base_url": "http://127.0.0.1:9/"},
               headers=auth)[1]["application"]
    secret = "Pw-Observe-2026!"
    login = {"url": "http://127.0.0.1:9/login", "title": "Sign in", "heading": "Sign in",
             "fields": [{"label": "User", "tag": "input", "type": "text", "value": "operator"},
                        {"label": "Password", "tag": "input", "type": "password", "value": secret}],
             "buttons": [{"label": "Sign in"}], "auth": {"password_fields": 1}}
    events = [{"kind": "fill", "url": "http://127.0.0.1:9/login", "value": "operator",
               "element": {"tag": "input", "type": "text", "name": "u", "label": "User"}},
              {"kind": "fill", "url": "http://127.0.0.1:9/login", "value": secret,
               "element": {"tag": "input", "type": "password", "name": "p", "label": "Password"}},
              {"kind": "click", "url": "https://elsewhere.example/", "element": {"tag": "button", "label": "Buy"}}]
    st, out = call(base, "POST", "/v1/observe", {"application_id": app["id"], "session_id": "ext-tab-1",
                                                  "events": events, "snapshot": login}, headers=auth)
    assert st == 200 and out["events"] == 2 and out["pages"] == 1, out  # the other site's click is dropped
    st, out = call(base, "POST", "/v1/observe", {"application_id": app["id"], "session_id": "ext-tab-1",
                                                  "snapshot": {**login, "url": "https://elsewhere.example/"},
                                                  "final": True}, headers=auth)
    assert st == 200 and out["pages"] == 0 and out["digested"] is True
    st, view = call(base, "GET", f"/v1/understanding?app_id={app['id']}", headers=auth)
    assert st == 200 and view["journal"]["observations"] == 3
    for f in c.paths.root.rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes(), f
    assert call(base, "POST", "/v1/observe", {"application_id": app["id"], "events": "x"}, headers=auth)[0] == 400
    assert call(base, "POST", "/v1/users", {"username": "viewer2", "password": "Viewer-pass-2", "role": "viewer"},
                headers=auth)[0] == 200
    tok = call(base, "POST", "/v1/auth/login", {"username": "viewer2", "password": "Viewer-pass-2"}, headers=dev)[1]
    viewer = {**dev, "Authorization": "Bearer " + tok["token"]}
    assert call(base, "GET", f"/v1/understanding?app_id={app['id']}", headers=viewer)[0] == 200
    assert call(base, "POST", "/v1/observe", {"application_id": app["id"]}, headers=viewer)[0] == 403
    assert call(base, "POST", f"/v1/journal-clear/{app['id']}", headers=viewer)[0] == 403
    st, out = call(base, "POST", f"/v1/journal-clear/{app['id']}", headers=auth)
    assert st == 200 and out["cleared"] == 3
