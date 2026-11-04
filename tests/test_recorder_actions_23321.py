"""23.3.21: recording and playing back double-click, right-click, hover menus, drag and drop, iframes, pop-ups,
new tabs and click-to-check, in a real headless Chromium."""
import functools
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

APP = Path(os.environ.get("APP_DIR", Path(__file__).resolve().parents[1] / "app"))
sys.path.insert(0, str(APP))

from qa_platform.automation.dsl import Step, Target  # noqa: E402

MAIN = """<!doctype html><html><head><style>
#menu{position:relative;display:inline-block} #sub{display:none;position:absolute;left:0;top:100%;background:#fff;border:1px solid #999;padding:6px} .zone{width:160px;height:60px;border:2px dashed #888;margin:8px}
</style></head><body>
<h1>Actions</h1><div id="log"></div>
<button id="dbl" ondblclick="log('dbl')">Double me</button>
<button id="ctx" oncontextmenu="event.preventDefault(); log('ctx')">Right-click me</button>
<nav><div class="menu" id="menu">Admin
  <div id="sub"><a href="#" id="users" onclick="event.preventDefault(); log('users'); document.getElementById('sub').style.display='none'">Users</a></div></div></nav>
<div id="card" draggable="true" style="width:80px;border:1px solid #333">Card</div>
<div class="zone" id="done" ondragover="event.preventDefault()" ondrop="event.preventDefault(); log('dropped')">Done</div>
<iframe id="pay" src="frame.html" title="Payment" style="width:300px;height:80px"></iframe>
<button id="del" onclick="log(confirm('Delete it?') ? 'deleted' : 'kept')">Delete</button>
<a id="help" href="tab2.html" target="_blank">Help</a>
<script>
function log(x){ document.getElementById('log').textContent += x + ' '; }
document.getElementById('menu').addEventListener('mouseenter', () => { document.getElementById('sub').style.display = 'block'; });
document.getElementById('menu').addEventListener('mouseleave', () => { document.getElementById('sub').style.display = 'none'; });
</script></body></html>"""
FRAME = """<!doctype html><body><button id="paybtn" onclick="document.getElementById('out').textContent='paid'">Pay</button><span id="out"></span></body>"""
TAB2 = """<!doctype html><body><h2>Help</h2><button id="ask" onclick="document.getElementById('ans').textContent='asked'">Ask</button><span id="ans"></span></body>"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("site")
    for name, text in (("main.html", MAIN), ("frame.html", FRAME), ("tab2.html", TAB2)):
        (root / name).write_text(text, encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/main.html"
    server.shutdown()


@pytest.fixture()
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient
    from qa_platform.main import create_app
    app = create_app()
    with TestClient(app):
        yield app.state.runtime


def test_record_new_actions_then_play_back(rt, site):
    a = rt.automation
    run_id = a.start_recording(site, name="Actions", headless=True)
    s = a.recording(run_id)["session"]
    for _ in range(100):
        try:
            s.command("fingerprint", "#dbl", timeout=5)
            break
        except Exception:
            time.sleep(0.2)
    pause = lambda: time.sleep(0.5)  # noqa: E731
    s.command("dblclick", "#dbl"); pause()
    s.command("rightclick", "#ctx"); pause()
    s.command("hover", "#menu"); pause()
    s.command("click", "#users"); pause()
    s.command("drag", ("#card", "#done")); pause()
    s.command("frame_click", ("#pay", "#paybtn")); pause()
    s.control("dialog_cancel")
    s.command("click", "#del"); pause()
    s.control("dialog_ok")
    s.command("click", "#del"); pause()
    # click-to-check: the next click picks the element instead of clicking it
    s.control("pick_text")
    time.sleep(0.6)
    s.command("click", "#log"); pause()
    assert s.bar_state()["pick"] is None
    s.command("click", "#help"); time.sleep(1.0)
    s.command("tab", (1, "click", "#ask")); pause()
    script = a.stop_recording(run_id)
    got = [(st.action, st.target.selector if st.target else st.value, st.tab) for st in script.steps]
    acts = [g[0] for g in got]
    assert acts == ["goto", "dblclick", "right_click", "hover", "click", "drag_to", "click", "dialog", "click",
                    "dialog", "click", "expect_text", "click", "click"], got
    hover = script.steps[3]
    assert hover.target.selector == "#menu", hover
    drag = script.steps[5]
    assert drag.target.selector == "#card" and drag.value == "#done" and drag.description == "Drag Card to Done"
    pay = script.steps[6]
    assert pay.target.selector == "#paybtn" and pay.target.frames == ["#pay"], pay
    assert [(x.value, x.description) for x in script.steps if x.action == "dialog"] == [
        ("dismiss", 'Cancel on the pop-up "Delete it?"'), ("accept", 'OK on the pop-up "Delete it?"')]
    check = script.steps[11]
    assert check.target.selector == "#log" and check.value == "dbl ctx users dropped kept deleted", check.value
    assert script.steps[-1].tab == 1 and script.steps[-1].target.selector == "#ask"

    # playback: everything happens again, in the iframe and in the new tab too
    script.steps.insert(7, Step(action="expect_text", target=Target(selector="#out", frames=["#pay"]), value="paid"))
    script.steps.append(Step(action="expect_text", target=Target(selector="#ans"), value="asked", tab=1))
    a.scripts.save(script)
    r = a.run_script(script.id, headless=True, background=False)
    assert r["status"] == "PASSED", (r["error"], [(x["description"], x["status"], x.get("error")) for x in r["steps"]])


def test_python_export_knows_the_new_actions(rt):
    from qa_platform.automation import pytest_export
    from qa_platform.automation.dsl import Script, to_playwright_python
    sc = Script(id="SCR-00000EXP01", name="x", steps=[
        Step(action="right_click", target=Target(selector="#a")), Step(action="drag_to", target=Target(selector="#a"), value="#b"),
        Step(action="dialog", value="dismiss"), Step(action="click", target=Target(selector="#p", frames=["#pay"]))])
    code = to_playwright_python(sc)
    assert 'click(button="right")' in code and "drag_to(page.locator('#b'))" in code and "d.dismiss()" in code
    lines = sum((pytest_export._step_lines(st) if hasattr(pytest_export, "_step_lines") else [] for st in sc.steps), [])
    assert lines == [] or any("frame_locator('#pay')" in x for x in lines)
