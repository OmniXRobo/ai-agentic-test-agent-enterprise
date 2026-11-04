"""AC30 smoke: every page of the dashboard loads in 23.3.0 with no new script errors compared with 23.2.2."""
import io, json, os, subprocess, sys, tarfile, time
from pathlib import Path
import httpx, pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get("APP_DIR", ROOT / "app"))
# The 23.2.2 application to compare with: a base-app folder next to the patch, BASE_APP_DIR, or (in the repository)
# the 23.2.2 baseline commit taken from git.
BASE_COMMIT = "97210e9a94cf99291d397abbbe0763179700d98a"


def base_app(tmp_path: Path) -> Path:
    for p in (os.environ.get("BASE_APP_DIR"), ROOT / "base-app"):
        if p and Path(p).is_dir():
            return Path(p)
    repo = Path(__file__).resolve().parents[1]
    if subprocess.run(["git", "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"], cwd=repo, capture_output=True).returncode:
        subprocess.run(["git", "fetch", "--depth", "1", "origin", BASE_COMMIT], cwd=repo, check=True, capture_output=True)
    dest = tmp_path / "base-app"
    dest.mkdir()
    archive = subprocess.run(["git", "archive", "--format=tar", BASE_COMMIT], cwd=repo, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return dest
sys.path.insert(0, str(Path(__file__).parent))
from test_voice_ui import FAKE_MEDIA, free_port  # noqa: E402


def start(app: Path, home: Path):
    port = free_port()
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"server": {"host": "127.0.0.1", "port": port}, "voice": {"reply_style": "plain"}}))
    proc = subprocess.Popen([sys.executable, "-m", "qa_platform"], cwd=app, env={**os.environ, "QA_HOME": str(home), "PYTHONPATH": str(app)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            httpx.get(base + "/api/health", timeout=1); return proc, base
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("no server")


def crawl(base):
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(); ctx.add_init_script(FAKE_MEDIA)
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(base + "/#/dashboard"); page.wait_for_timeout(1500)
        routes = page.evaluate("[...document.querySelectorAll('.nav-link')].map(a => a.dataset.route)")
        for r in routes:
            errs.clear()
            page.evaluate(f"location.hash = '#/{r}'"); page.wait_for_timeout(700)
            failed = page.locator("text=Could not load this page").count()
            out[r] = {"errors": list(errs), "failed": failed, "title": page.inner_text("#pageTitle")}
        b.close()
    return out


def test_every_page_loads_like_before(tmp_path):
    p1, base_old = start(base_app(tmp_path), tmp_path / "old")
    p2, base_new = start(APP, tmp_path / "new")
    try:
        old, new = crawl(base_old), crawl(base_new)
    finally:
        p1.terminate(); p2.terminate()
    # 23.3.21 and 23.3.22: new pages, they must load without errors
    added = {"scheduled-runs", "email-settings", "perf-scenarios"}
    assert set(old) == set(new) - added and len(new) >= 30
    for r in added & set(new):
        assert not new[r]["failed"] and not new[r]["errors"], (r, new[r])
    report = {r: (old[r], new[r]) for r in old if new[r] != old[r]}
    print(json.dumps({"pages": len(new), "differences": report}, indent=1))
    for r in old:
        assert new[r]["failed"] <= old[r]["failed"], r
        assert len(new[r]["errors"]) <= len(old[r]["errors"]), (r, new[r]["errors"])
        assert new[r]["title"] == old[r]["title"]
