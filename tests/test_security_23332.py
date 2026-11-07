"""23.3.32: the local API refuses other web pages and other host names (CSRF and DNS rebinding)."""

from __future__ import annotations

from qa_platform.api.guard import refusal


def test_other_web_pages_cannot_change_anything(client):
    r = client.post("/api/notifications/read", headers={"Origin": "http://intranet.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and r.json()["error_code"] == "REQUEST_REFUSED"
    r = client.post("/api/notifications/read", headers={"Origin": "null"})
    assert r.status_code == 403


def test_dashboard_and_local_programs_are_accepted(client):
    assert client.post("/api/notifications/read").status_code != 403  # PowerShell, installer: no Origin
    same = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/notifications/read", headers=same).status_code != 403
    assert client.get("/api/health", headers={"Origin": "http://evil.example"}).status_code == 200  # reads are fine


def test_foreign_host_name_refused_even_for_reads(client):
    r = client.get("/api/health", headers={"Host": "attacker.example:8765"})
    assert r.status_code == 403


def test_refusal_rules():
    local = {"host": "127.0.0.1:8765"}
    assert refusal("GET", local) is None
    assert refusal("POST", local) is None
    assert refusal("POST", {**local, "origin": "http://127.0.0.1:8765", "sec-fetch-site": "same-origin"}) is None
    assert refusal("POST", {**local, "origin": "http://localhost:8765"})  # another origin than the page's own
    assert refusal("POST", {**local, "origin": "http://127.0.0.1:9999"})
    assert refusal("DELETE", {**local, "sec-fetch-site": "cross-site"})
    assert refusal("GET", {"host": "rebind.example:8765"})
    assert refusal("GET", {"host": "[::1]:8765"}) is None
    assert refusal("GET", {"host": "localhost"}) is None


def test_generated_scripts_may_not_run_programs_or_delete_files():
    import ast

    from qa_platform.automation.framework.validator import unsafe_calls

    bad = ("import subprocess\nimport os as o\nfrom shutil import rmtree\n"
           "def test_x(page):\n    o.system('del *')\n    eval('1')\n    subprocess.run(['x'])\n")
    found = unsafe_calls("tests/test_x.py", ast.parse(bad))
    assert any("subprocess" in f for f in found) and any("os.system" in f for f in found)
    assert any("shutil.rmtree" in f for f in found) and any("eval()" in f for f in found)
    good = ("import os\nimport re\nfrom pathlib import Path\n"
            "def test_y(page):\n    page.goto(os.environ.get('BASE_URL', ''))\n    assert re.match('a', 'a')\n")
    assert unsafe_calls("tests/test_y.py", ast.parse(good)) == []
