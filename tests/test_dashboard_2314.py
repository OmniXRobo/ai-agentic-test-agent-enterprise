"""23.3.14: dashboard statistics come from recorded data (analytics insights) and the pages carry the new parts."""
import json, os, socket, sqlite3, subprocess, sys, time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get("APP_DIR", ROOT / "app"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]


def test_insights_and_pages(tmp_path):
    port = free_port()
    (tmp_path / "settings.json").write_text(json.dumps({"server": {"host": "127.0.0.1", "port": port}}))
    env = {**os.environ, "QA_HOME": str(tmp_path), "PYTHONPATH": str(APP)}
    proc = subprocess.Popen([sys.executable, "-m", "qa_platform"], cwd=APP, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                httpx.get(base + "/api/health", timeout=1); break
            except httpx.HTTPError:
                time.sleep(0.5)
        db = sqlite3.connect(tmp_path / "qa_platform.sqlite3")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        for i, st in enumerate(["PASSED", "PASSED", "FAILED"]):
            db.execute("INSERT INTO execution_results(run_id,script_id,status,started_at) VALUES(?,?,?,?)", (f"RUN-{i}", f"S{i}", st, now))
            db.execute("INSERT INTO runs(run_id,kind,title,status,started_at) VALUES(?,?,?,?,?)", (f"TDATA-{i}", "design", "t", st, now))
        for kind in ["valid", "valid", "invalid"]:
            db.execute("INSERT INTO test_data(test_case_ref,tfs_test_case_id,name,kind,dataset,created_at,updated_at)"
                       " VALUES('TC7',7,'r',?,'{}',?,?)", (kind, now, now))
        db.commit(); db.close()
        ins = httpx.get(base + "/api/analytics/summary", timeout=30).json()["insights"]
        assert ins["test_data"]["rows"] == 3 and ins["test_data"]["valid"] == 2 and ins["test_data"]["test_cases"] == 1
        assert ins["executions"]["total"] == 3 and ins["executions"]["failed"] == 1 and len(ins["executions"]["trend"]) == 7
        assert ins["runs_by_kind"][0] == {"kind": "TDATA", "passed": 2, "failed": 1, "other": 0}
        html = httpx.get(base + "/").text
        assert "css/defense.css" in html
    finally:
        proc.terminate(); proc.wait(10)


def test_web_files():
    web = APP / "web" / "js"
    dash = (web / "pages" / "dashboard.js").read_text()
    assert "Agent Squadron" not in dash and "recentRuns" not in dash and "QA progress" in dash
    cc = (web / "pages" / "command-center.js").read_text()
    assert "AGENT SQUADRON" not in cc and "COMMAND HANDOFF" in cc and "RADIO CONFIGURATION" in cc
    card = (web / "agent-card.js").read_text()
    assert 'data-f="rem"' in card and 'data-f="total"' in card
    assert "unit-pilot" not in card and "robotSvg" in card
