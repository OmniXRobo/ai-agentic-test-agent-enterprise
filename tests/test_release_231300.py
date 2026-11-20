"""23.13.0: Microsoft Edge when Chromium is not installed, the AI Agent repairs (public sites, changing addresses, the
lifecycle list, Arabic commands), self-healing that is kept, the API Testing trust store, the TFS "Assign to" list,
a personal TFS project and PAT per account with the welcome email, HTTPS with a friendly name (certificates made
offline, a real TLS handshake), the module report actions and the dashboard figures behind the new charts."""

from __future__ import annotations

import socket
import ssl
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qa_platform import https_setup
from qa_platform.auth import LOCAL_PROGRAM, User, reset_current_user, set_current_user
from qa_platform.automation import browser
from qa_platform.automation.runner import has_dynamic_part, url_matches, url_pattern
from qa_platform.credentials import MemoryBackend
from qa_platform.errors import QAError
from qa_platform.main import create_app
from skyguard_agent import sessions
from skyguard_agent.knowledge import same_site, site_of
from skyguard_agent.translate import by_words, has_arabic, to_english
from tests.fake_ado import PAT, FakeAzureDevOps

BROWSER = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


# ---- Edge on a PC without Chromium -----------------------------------------------------------------------------------

class _Fake:
    """Playwright's chromium launcher: only the channels in ``ok`` start; the rest are 'not installed'."""

    def __init__(self, ok):
        self.ok, self.calls = ok, []
        self.chromium = self

    def launch(self, **options):
        name = options.get("channel") or "chromium"
        self.calls.append(name)
        if name not in self.ok:
            raise RuntimeError(f"Executable doesn't exist at C:\\x\\{name}.exe\nLooks like Playwright was just installed")
        return type("B", (), {"close": lambda self: None})()


def test_edge_is_used_when_chromium_is_missing(monkeypatch):
    monkeypatch.setattr(browser, "_playback", lambda: {"browser": "msedge"})
    monkeypatch.setattr(browser, "installed_executable", lambda b, pw=None: "C:\\edge.exe" if b == "msedge" else None)
    fake = _Fake({"msedge"})
    assert browser.preferred("chromium") == "msedge"  # the old "chromium" default follows Preferences
    browser.launch(fake, "chromium", headless=True)
    assert fake.calls == ["msedge"]
    # Preferences say Chromium, but it is missing from this installation: Edge starts instead (and it is reported)
    monkeypatch.setattr(browser, "_playback", lambda: {"browser": "chromium"})
    fake = _Fake({"msedge"})
    browser.launch(fake, None, headless=True)
    assert fake.calls == ["chromium", "msedge"] and browser.last_fallback["used"] == "msedge"
    # nothing installed: one clear error that lists what was tried
    with pytest.raises(QAError) as err:
        browser.launch(_Fake(set()), None, headless=True)
    assert err.value.error_code == "BROWSER_LAUNCH_FAILED" and err.value.details["tried"]


def test_exact_launch_never_switches(monkeypatch):
    monkeypatch.setattr(browser, "_playback", lambda: {"browser": "msedge"})
    monkeypatch.setattr(browser, "installed_executable", lambda b, pw=None: "x")
    fake = _Fake({"msedge"})
    with pytest.raises(QAError):
        browser.launch(fake, "chromium", headless=True, exact=True)  # diagnostics test exactly what they name
    assert fake.calls == ["chromium"]


# ---- addresses that change on every visit ----------------------------------------------------------------------------

def test_dynamic_addresses_match_and_static_ones_do_not():
    assert has_dynamic_part("https://www.emirates.com/3432432dffdsf") and not has_dynamic_part("https://www.emirates.com/ae/english/")
    assert url_matches("https://www.emirates.com/99887abcdef12/", "https://www.emirates.com/3432432dffdsf/")
    assert url_matches("https://shop.test/order/5f1c2d3e-0000-4000-8000-123456789abc/pay",
                       "https://shop.test/order/0b7a1c2d-1111-4111-8111-abcdefabcdef/pay")
    assert not url_matches("https://www.emirates.com/ae/arabic/", "https://www.emirates.com/ae/english/")
    assert url_matches("https://a.test/x?sid=99&lang=en", "https://a.test/x?sid=12&lang=ar")
    assert url_pattern("/login").search("https://a.test/login")  # a partial address still works as before


def test_same_site_accepts_subdomains_but_not_other_sites():
    app = {"origins": ["https://www.emirates.com"]}
    assert same_site("https://fly.emirates.com/booking/3432432dffdsf", app)
    assert not same_site("https://emirates.com.evil.test/x", app) and not same_site("https://10.0.0.5/x", app)
    assert site_of("www.example.co.uk") == "example.co.uk"


def test_public_sites_need_no_account():
    assert not sessions.needs_sign_in({"profile": {"public": True}}, accounts=[{"id": "a"}])
    assert not sessions.needs_sign_in({"profile": {}}, accounts=[])  # nothing learned, no account: browse as is
    assert sessions.needs_sign_in({"profile": {"login": {"url": "https://x.test/login"}}}, accounts=[])
    assert not sessions.needs_sign_in({"profile": {}}, {"data": {"public": True}}, accounts=[{"id": "a"}])


# ---- Arabic commands -------------------------------------------------------------------------------------------------

def test_arabic_commands_are_translated_offline():
    assert has_arabic("افتح الموقع") and not has_arabic("open the site")
    assert by_words("احجز رحلة من دبي الى كراتشي بكرة") == "book flight from Dubai to Karachi tomorrow"
    assert by_words("سجل الدخول كمدير") == "log in as manager"
    assert by_words("ورني صفحة الحجز") == "show me booking page"
    assert to_english("open the site") == ("open the site", False)

    class Llm:
        def chat(self, messages, max_tokens=0):
            assert "Arabic" in messages[0]["content"]
            return '"open the booking page"\n'

    assert to_english("افتح صفحة الحجز", Llm()) == ("open the booking page", True)

    class Broken:
        def chat(self, *a, **k):
            raise ConnectionError("not running")

    assert to_english("افتح صفحة الحجز", Broken()) == ("open booking page", True)


# ---- the TFS "Assign to" list, manual defects, per-user TFS ------------------------------------------------------------

@pytest.fixture()
def env(paths):
    ado = FakeAzureDevOps(severity=True)
    ado.items[1420]["System.AssignedTo"] = {"displayName": "Lina Haddad", "uniqueName": "lina@acme.ae"}
    app = create_app(paths, secret_backend=MemoryBackend(), tfs_transport=ado.transport())
    with TestClient(app) as c:
        c.put("/api/tfs/profiles/online", json={"url": "https://dev.azure.com/acme", "project": "Shop", "pat": PAT,
                                                "make_active": True})
        yield c, ado, c.app.state.runtime


def test_assignees_come_from_tfs_and_reach_the_defect(env):
    c, ado, rt = env
    people = c.get("/api/defects/assignees").json()["people"]
    names = [p["name"] for p in people]
    assert names == sorted(names, key=str.lower) and {"Sara Khan", "Omar Ali", "Lina Haddad"} <= set(names)
    assert not any("Contributors" in n for n in names)  # groups are not people
    r = c.post("/api/defects/manual", json={"module": "API Testing", "source": "api", "title": "Orders API returns 500",
                                            "repro_steps": "1. POST /orders\nExpected: 201\nActual: 500",
                                            "assigned_to": "omar@acme.ae"}).json()
    draft = r["draft"]
    assert draft["status"] == "draft" and draft["assigned_to"] == "omar@acme.ae" and "<br>" in draft["repro_steps"]
    created = c.post(f"/api/defects/drafts/{draft['id']}/create", json={}).json()["draft"]
    assert ado.items[created["tfs_id"]]["System.AssignedTo"] == "omar@acme.ae"
    # Defect Review edits the person too ("" clears it)
    d2 = c.post("/api/defects/manual", json={"module": "Load Tests", "source": "performance", "title": "Slow search"}).json()["draft"]
    assert c.patch(f"/api/defects/drafts/{d2['id']}", json={"assigned_to": "ACME\\sara"}).json()["draft"]["assigned_to"] == "ACME\\sara"
    assert c.patch(f"/api/defects/drafts/{d2['id']}", json={"assigned_to": ""}).json()["draft"]["assigned_to"] is None
    # "Approve and send now": sent at once, but a second identical defect stops at the duplicate check
    body = {"module": "Load Tests", "source": "performance", "title": "Checkout times out under load",
            "repro_steps": "1. Run the load test\nExpected: < 2 s\nActual: timeout", "send_now": True}
    first = c.post("/api/defects/manual", json=body).json()
    assert first["draft"]["tfs_id"] and first["draft"]["status"] == "created"
    second = c.post("/api/defects/manual", json=body).json()
    assert second["draft"]["id"] == first["draft"]["id"] and "already in TFS" in second["note"]  # not sent twice


def test_each_account_has_its_own_tfs_project_and_pat(env):
    c, ado, rt = env
    tfs = rt.tfs
    # a PAT that cannot open the project is refused before the account is made
    bad = c.post("/api/auth/users", json={"username": "maya", "password": "Maya-pass-26", "role": "tester",
                                          "email": "maya@acme.ae", "tfs_project": "Shop", "tfs_pat": "wrong-pat-123"})
    assert bad.status_code == 422 and bad.json()["error_code"] == "TFS_PAT_INVALID"
    assert not any(u["username"] == "maya" for u in c.get("/api/auth/users").json()["users"])
    ok = c.post("/api/auth/users", json={"username": "maya", "password": "Maya-pass-26", "role": "tester",
                                         "email": "maya@acme.ae", "tfs_project": "Shop", "tfs_pat": PAT,
                                         "send_welcome": False}).json()
    assert ok["pat_check"]["ok"] and ok["user"]["email"] == "maya@acme.ae" and ok["user"]["tfs_project"] == "Shop"
    assert "tfs_pat" not in str(ok["user"]) and PAT not in str(ok)
    maya = User(ok["user"]["id"], "maya", "Maya", "tester")
    token = set_current_user(maya)
    try:
        assert tfs.profile()["personal"] and tfs.profile()["project"] == "Shop"
        tfs.save_profile("online", project="Mobile")  # Maya changes her project ...
        assert tfs.profile()["project"] == "Mobile"
    finally:
        reset_current_user(token)
    token = set_current_user(LOCAL_PROGRAM)
    try:  # ... the admin's (shared) project is unchanged
        assert tfs.profile()["project"] == "Shop" and not tfs.profile().get("personal")
    finally:
        reset_current_user(token)
    assert rt.secrets.get("tfs-user", "maya:online") == PAT  # in the credential store, never in settings.json
    assert PAT not in (rt.paths.root / "settings.json").read_text()
    # missing fields are refused
    r = c.post("/api/auth/users", json={"username": "nopat", "password": "Nopat-pass-26", "role": "tester",
                                        "email": "n@acme.ae", "tfs_project": "Shop"})
    assert r.status_code == 422


def test_welcome_email_carries_the_address_and_the_manual(env, monkeypatch):
    c, ado, rt = env
    sent = []
    monkeypatch.setattr(rt.mailer, "send", lambda to, subject, html, text=None, cc=None, attachments=None:
                        sent.append((to, subject, html, attachments)) or {"sent": True})
    r = c.post("/api/auth/users", json={"username": "omar", "password": "Omar-pass-26", "role": "developer",
                                        "email": "omar@acme.ae", "tfs_project": "Shop", "tfs_pat": PAT}).json()
    assert r["welcome"]["sent"] and sent[0][0] == "omar@acme.ae"
    assert "omar" in sent[0][2] and "Omar-pass-26" in sent[0][2] and "http" in sent[0][2]
    assert r["user"]["must_change"]  # the emailed password must be changed at the first sign-in
    manual = Path(__file__).resolve().parents[1] / "web" / "manual" / "SkyGuard_QA_User_Manual.pdf"
    assert sent[0][3] == ([manual] if manual.is_file() else [])
    # email not set up: the account is still made and the admin is told why no email went out
    monkeypatch.setattr(rt.mailer, "send", lambda *a, **k: (_ for _ in ()).throw(
        QAError("EMAIL_NOT_CONFIGURED", "Set up the Outlook email first.")))
    r = c.post("/api/auth/users", json={"username": "lina", "password": "Lina-pass-26", "role": "tester",
                                        "email": "lina@acme.ae", "tfs_project": "Shop", "tfs_pat": PAT}).json()
    assert r["ok"] and not r["welcome"]["sent"] and "Outlook" in r["welcome"]["reason"]
    assert not r["user"]["must_change"]
    assert c.post("/api/auth/users", json={"username": "bad", "password": "Badd-pass-26", "role": "tester",
                                           "email": "not-an-email", "tfs_project": "Shop", "tfs_pat": PAT}).json()["error_code"] == "EMAIL_INVALID"


# ---- HTTPS with a friendly name ----------------------------------------------------------------------------------------

def test_https_certificates_are_made_offline_and_trusted_by_name(tmp_path):
    made = https_setup.make_certificates(tmp_path, "SkyGuard-QA")
    assert made["hostname"] == "skyguard-qa" and made["new_ca"] and "127.0.0.1" in made["ips"]
    again = https_setup.make_certificates(tmp_path, "skyguard-qa")
    assert not again["new_ca"]  # the CA stays: colleagues trust it once
    f = https_setup.files(tmp_path)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(f["cert"], f["key"])
    client = ssl.create_default_context(cafile=str(f["ca"]))
    with socket.socket() as ls:
        ls.bind(("127.0.0.1", 0))
        ls.listen(1)
        port = ls.getsockname()[1]

        def serve():
            conn, _ = ls.accept()
            with server.wrap_socket(conn, server_side=True) as s:
                s.sendall(b"ok")

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        with socket.create_connection(("127.0.0.1", port)) as raw:
            with client.wrap_socket(raw, server_hostname="skyguard-qa") as tls:  # the name is checked
                assert tls.recv(2) == b"ok"
        th.join(5)
    with pytest.raises(ValueError):
        https_setup.check_hostname("bad name!")
    assert https_setup.public_url({"server": {"https": {"enabled": True, "hostname": "skyguard-qa", "port": 443}}}) == "https://skyguard-qa/"
    assert https_setup.public_url({"server": {"https": {"enabled": True, "hostname": "qa", "port": 8443}}}) == "https://qa:8443/"
    assert "PRIVATE KEY" not in f["ca"].read_text()


def test_https_settings_api_and_the_friendly_name_is_accepted(env):
    c, ado, rt = env
    assert c.put("/api/auth/network/https", json={"enabled": True, "hostname": "bad name"}).json()["error_code"] == "HTTPS_NAME_INVALID"
    assert c.put("/api/auth/network/https", json={"enabled": True, "hostname": "qa", "port": 8765}).json()["error_code"] == "HTTPS_PORT_TAKEN"
    r = c.put("/api/auth/network/https", json={"enabled": True, "hostname": "skyguard-qa"}).json()
    assert r["ready"] and r["url"] == "https://skyguard-qa/" and r["made"]["hostname"] == "skyguard-qa"
    assert c.get("/api/auth/https-ca.crt").text.startswith("-----BEGIN CERTIFICATE-----")
    from qa_platform.api.guard import refusal
    headers = {"host": "skyguard-qa", "origin": "https://skyguard-qa", "sec-fetch-site": "same-origin"}
    assert refusal("POST", headers, False) is not None  # not a name of this PC unless HTTPS gives it
    assert refusal("POST", headers, False, {"skyguard-qa"}) is None


# ---- module reports, dashboard figures, API Testing trust --------------------------------------------------------------

def test_module_reports_have_facts_and_a_pdf_without_ai(env):
    c, ado, rt = env
    from qa_platform.reports.ai_reports import MODULES
    for module in MODULES:
        facts = rt.ai_reports.module_facts(module)
        assert facts["module"] == MODULES[module]["title"]
    assert c.get("/api/ai-reports/module/not-a-module").status_code == 422
    st = c.get("/api/ai-reports/module/guided-automation").json()
    assert st["analysis"] is None and not st["has_pdf"]
    draft = c.get("/api/ai-reports/module/api-testing/email").json()
    assert draft["subject"].startswith("API Testing report") and draft["attachment"].endswith(".pdf")


def test_dashboard_insights_feed_the_new_charts(env):
    c, ado, rt = env
    c.post("/api/defects/manual", json={"module": "API Testing", "source": "api", "title": "Orders API returns 500"})
    ins = c.get("/api/analytics/summary").json()["insights"]
    assert ins["defects_by_module"].get("api") == 1
    assert {"passed", "failed", "other"} <= set(ins["api_results"]) and "applied" in ins["healing"]


def test_api_testing_trusts_the_system_store_and_its_own_ca(tmp_path):
    from qa_platform.apitesting.client import trust_context
    https_setup.make_certificates(tmp_path, "intranet-api", bits=1024)
    ctx = trust_context(str(https_setup.files(tmp_path)["ca"]))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname  # verification stays on
    assert any("SkyGuard QA Local CA" in str(c.get("subject")) for c in ctx.get_ca_certs())


def test_arabic_reply_uses_an_arabic_voice_of_the_same_gender(tmp_path):
    from qa_platform.voice.tts import SystemVoice
    v = SystemVoice(tmp_path)
    v._voices = [{"name": "Microsoft David Desktop", "gender": "Male", "lang": "en-US"},
                 {"name": "Microsoft Zira Desktop", "gender": "Female", "lang": "en-US"},
                 {"name": "Microsoft Hoda Desktop", "gender": "Female", "lang": "ar-EG"},
                 {"name": "Microsoft Naayf Desktop", "gender": "Male", "lang": "ar-SA"}]
    assert v.voice_for("تم فتح الصفحة", "Microsoft David Desktop") == "Microsoft Naayf Desktop"
    assert v.voice_for("تم فتح الصفحة", "Microsoft Zira Desktop") == "Microsoft Hoda Desktop"
    assert v.voice_for("The page is open", "Microsoft David Desktop") == "Microsoft David Desktop"
