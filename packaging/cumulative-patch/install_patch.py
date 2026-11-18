r"""SkyGuard QA 23.12.0 cumulative upgrade from 23.3.35 (includes 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2,
23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1 and 23.11.2). 23.12.0 adds API Testing (Automation Studio > API Testing:
authorized targets, Test Connection diagnostics, offline OpenAPI / Swagger import, generated tests, chains, pytest + httpx
scripts, failure classification and Defect Review drafts), API tests and Integration Comparisons in Scheduled Runs with
Visible / Headless / Smart Auto execution modes, the new Agent Workflow agents with real availability, and a larger
Python editor (full screen, adjustable height). From 23.11.2, which fixes the AI Agent's missing-information follow-up: an answer
such as "First Name; muhammad" is read field by field, kept across turns, and only what is still missing is asked
again; a postal code is no longer taken for a secret code; an application that never shows the signed-in user name
can run operations that no role restricts after it accepts the account's own sign-in. From 23.11.1, which repairs the AI Agent: the intent of a command is decided first (a navigation
opens a page and never runs a task), a plan is tied to its request and application, the confirmation is checked by
the server, learning covers multi-step and multi-role record flows and is tested after Stop & save, suggestions come
from what was learned, and the browser extension files (now 1.3.1; Start/Stop learning, reconnect). From 23.11.0: editable Python tests in Guided Browser Automation (versions, validation, the saved
version is the one run and downloaded), the Smart Wait service shared by the runner and the exported Python (Wait
settings, Insert waits, + Wait), one Defect Review for every failure (drafts that need approval before anything is sent
to TFS; publishing confirmed by reading the work item back, with retry and reconciliation), Integration Comparison
defects sent through the same publisher; and from 23.10.0: the simple AI Agent (connect, learn while browsing, plain-language and voice commands, one preview,
explicit confirmation for consequential operations), guided recording for the Integration Comparison (Start Guided
Recording, Stop & Save, Edit Recorded Steps, Replay & Verify, editable filters, every page, master-detail), and the
browser extension files. Offline installer, verifier and rollback. No administrator rights are needed.

Runs with the application's own Python (no packages beyond the standard library are needed for the installer itself).
Every step is printed; nothing is downloaded.

    python install_patch.py apply    --app <app folder> [--data <data folder>] [--python <app python>]
                                     [--side-by-side <folder>]
    python install_patch.py verify   --app <app folder> [--data <data folder>] [--python <app python>]
    python install_patch.py rollback --app <app folder> [--backup <backup folder>] [--restore-database]

apply:
  1. detects the installation and its version (23.3.35 to 23.11.2; 23.12.0 to apply again); any other version is
     refused before anything is changed (``detect`` prints what was found as JSON, for the Setup wizard),
  2. checks every file of the patch against PATCH_MANIFEST.json (SHA-256), the OCR wheels included,
  3. checks free disk space and that the application and data folders can be written. A protected application folder
     (for example a machine-wide programs folder, which a standard user cannot change) is never forced: the installer stops and
     explains, or with --side-by-side <folder> copies the application to a folder of the user (by default
     %LOCALAPPDATA%\SkyGuardQA\app-23.12.0), patches that copy and writes START_SKYGUARD_23.12.0.cmd next to it; the
     original folder and the data are not changed,
  4. checks the application is not running (its port does not answer),
  5. backs up every application file it replaces, the database and the agent's knowledge database (SQLite online
     backup) and settings.json,
  6. copies the new files (each written to a temporary name, then renamed),
  7. installs the offline OCR runtime (RapidOCR, ONNX Runtime, OpenCV, NumPy, Pillow: wheels for 64-bit Windows and
     Python 3.13, carried in the patch) into runtime\ocr\site-packages, or into %LOCALAPPDATA%\SkyGuardQA\ocr when
     the application folder cannot be written. The wheels are unpacked into a staging folder first; the folder in use
     is replaced only when the staging folder is complete, and the previous runtime is kept for rollback. Nothing is
     installed into the system Python and no registry key or PATH is changed,
  8. migrates: the main database is brought to 21 migrations (23.12.0 adds migration 21: the API Testing tables
     api_apps, api_tests, api_test_versions, api_suites, api_runs, api_results, api_scripts and api_audit; 23.11.0 adds
     migration 20: review columns on defect_drafts, defect_events, script_python and script_python_state; 23.9.0,
     23.10.0, 23.11.1 and 23.11.2 add none); the Operations Agent's knowledge database is created or
     migrated to schema 2 and gets the simple agent's conversation_state table (23.10.0),
  9. verifies: the new version loads, the agent core, the comparison workspace with filters and semantics, the OCR
     engine probe (on Windows with Python 3.13), every migration, PRAGMA integrity_check for both databases and no
     table has fewer rows than before,
 10. on any failure restores the previous files, the previous OCR runtime and the database copy taken in step 5 (the
     half-migrated database is kept next to it, renamed, never deleted).

rollback (after a successful apply): restores the previous application files, removes the files the patch added and
puts back the OCR runtime that was there before (or removes the one the patch installed). The databases are kept as
they are by default (23.10.0, 23.11.0 and 23.12.0 only add tables and columns that earlier versions ignore); --restore-database puts back the copy taken before the
upgrade instead (the current database is kept next to it, renamed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path

PATCH_VERSION = "23.12.0"
BASELINE = ("23.3.35, 23.4.0, 23.5.0, 23.6.0, 23.6.1, 23.7.0, 23.7.1, 23.7.2, 23.8.0, 23.9.0, 23.10.0, 23.11.0, 23.11.1 "
            "or 23.11.2")
RELEASES = ("23.3.35", "23.4.0", "23.5.0", "23.6.0", "23.6.1", "23.7.0", "23.7.1", "23.7.2", "23.8.0", "23.9.0", "23.10.0",
            "23.11.0", "23.11.1", "23.11.2")
BASELINES = (*RELEASES, PATCH_VERSION)
OCR_PACKAGES = ("rapidocr", "onnxruntime", "cv2", "numpy", "PIL")
MSVC_DIR = "_msvc"  # Microsoft C++ runtime DLLs for the OCR worker (see ocr_worker._load_msvc)
AGENT_SCHEMA = 2
MIGRATION_COUNT = 21
MARGIN_MB = 300


def _copy(src, dst):
    """Copy contents and timestamps. Unlike shutil.copy2 on Windows (CopyFile2), this does not carry alternate data
    streams such as the downloaded-file mark, and it also works where CopyFile2 is missing (Wine)."""
    shutil.copyfile(src, dst)
    shutil.copystat(src, dst)
    return dst


class PatchError(Exception):
    pass


def say(msg: str) -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(app: Path) -> str:
    text = (app / "qa_platform" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "unknown"


def locate(app_arg: str, data_arg: str | None) -> tuple[Path, Path, Path]:
    """(app, root, data) the same way the application finds them: an installed copy keeps data next to app."""
    base = Path(app_arg).expanduser().resolve()
    if (base / "app" / "qa_platform" / "__init__.py").exists():
        base = base / "app"
    if not (base / "qa_platform" / "__init__.py").exists():
        raise PatchError(f"No SkyGuard QA installation in {app_arg} (qa_platform\\__init__.py not found).")
    installed = (base / "INSTALLED.json").exists()
    root = base.parent if installed else base
    data = Path(data_arg).expanduser().resolve() if data_arg else Path(os.environ.get("QA_HOME") or (root / "data" if installed else base / "data")).resolve()
    return base, root, data


def free_mb(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free // (1024 * 1024)


def tree_mb(paths: list[Path]) -> float:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".patch-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def port_of(data: Path) -> int:
    try:
        return int(json.loads((data / "settings.json").read_text(encoding="utf-8-sig")).get("server", {}).get("port") or 8765)
    except (OSError, ValueError, AttributeError):
        return 8765


def running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def row_counts(db: Path) -> dict[str, int]:
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def sqlite_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a database in WAL mode (the online backup API includes the WAL's content)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def payload_files(patch_dir: Path) -> list[str]:
    root = patch_dir / "app"
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def check_payload(patch_dir: Path) -> list[str]:
    manifest = json.loads((patch_dir / "PATCH_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("version") != PATCH_VERSION:
        raise PatchError(f"PATCH_MANIFEST.json is for {manifest.get('version')}, not {PATCH_VERSION}.")
    files = manifest["files"]
    bad = [rel for rel, digest in files.items() if not (patch_dir / "app" / rel).is_file()
           or sha256(patch_dir / "app" / rel) != digest]
    extra = sorted(set(payload_files(patch_dir)) - set(files))
    if bad or extra:
        raise PatchError("The patch folder is damaged or changed: "
                         + ", ".join((bad + extra)[:10]) + (" ..." if len(bad) + len(extra) > 10 else "")
                         + ". Extract the zip again.")
    return sorted(files)


def run_py(python: str, app: Path, data: Path, code: str) -> str:
    env = {**os.environ, "QA_HOME": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([python, "-E", "-s", "-c", code], cwd=str(app), env=env, capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        raise PatchError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else
                         f"exit code {r.returncode}")
    return r.stdout.strip()


# ---- 23.9.0: the offline OCR runtime -----------------------------------------------------------------------------------

def user_dir() -> Path:
    """The per-user folder the application also looks in (qa_platform.comparison.ocr._user_dir)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "SkyGuardQA"


def ocr_wheels(patch_dir: Path) -> list[Path]:
    return sorted((patch_dir / "ocr-runtime" / "wheels").glob("*.whl"))


def check_ocr_wheels(patch_dir: Path) -> None:
    listed = patch_dir / "ocr-runtime" / "WHEELS.json"
    if not listed.exists():
        return
    want = json.loads(listed.read_text(encoding="utf-8"))
    have = {p.name: p for p in ocr_wheels(patch_dir)}
    bad = [n for n, d in want.items() if n not in have or sha256(have[n]) != d] + sorted(set(have) - set(want))
    if bad:
        raise PatchError("The OCR runtime files are damaged or changed: " + ", ".join(bad[:5]) + ". Extract the zip again.")


def python_tag(python: str) -> str:
    try:
        out = subprocess.run([python, "-E", "-s", "-c", "import sys, struct; print(sys.platform, sys.version_info[0], "
                              "sys.version_info[1], struct.calcsize('P') * 8)"], capture_output=True, text=True,
                             timeout=60).stdout.split()
        return f"{out[0]}-cp{out[1]}{out[2]}-{out[3]}bit"
    except (OSError, IndexError, subprocess.SubprocessError):
        return "unknown"


def extract_wheel(whl: Path, target: Path) -> None:
    """Unpacks a wheel the way an installer does (purelib/platlib of *.data go to the top). Scripts are not needed."""
    root = target.resolve()
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir():
                continue
            parts = name.split("/")
            if parts[0].endswith(".data"):
                if len(parts) == 4 and parts[1] == "data" and parts[2] == "Scripts":
                    continue
                if len(parts) == 3 and parts[1] == "data" and parts[2].lower().endswith(".dll"):
                    # 23.9.0: the Microsoft C++ runtime (msvc_runtime wheel) goes into a folder of its own that the
                    # OCR worker loads first, so an older msvcp140.dll in Windows\System32 is never used
                    parts = [MSVC_DIR, parts[2]]
                elif len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue
                else:
                    parts = parts[2:]
            dst = (root / Path(*parts)).resolve()
            if root not in dst.parents:  # never outside the runtime folder
                raise PatchError(f"{whl.name}: unsafe path {name}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, dst.open("wb") as out:
                shutil.copyfileobj(src, out)


def ocr_base(app: Path) -> Path:
    runtime = app / "runtime"
    return runtime / "ocr" if writable(runtime) else user_dir() / "ocr"


def install_ocr(patch_dir: Path, app: Path, python: str, stamp: str, force: bool = False) -> dict:
    """Unpack the OCR wheels into a staging folder, then swap it in. Returns what rollback needs."""
    wheels = ocr_wheels(patch_dir)
    if not wheels:
        say("No OCR runtime in this patch folder; the OCR engine is left as it is.")
        return {"installed": False, "reason": "no wheels"}
    tag = python_tag(python)
    if tag != "win32-cp313-64bit" and not force:
        say(f"The bundled OCR runtime is for 64-bit Windows with Python 3.13; the application's Python is {tag}. "
            "OCR runtime not installed (Windows OCR or Tesseract can still be chosen in Preferences > Comparison).")
        return {"installed": False, "reason": f"python {tag}"}
    base = ocr_base(app)
    base.mkdir(parents=True, exist_ok=True)
    staging = base / f"site-packages.staging-{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    say(f"Unpacking {len(wheels)} wheels into {staging}")
    for w in wheels:
        extract_wheel(w, staging)
    missing = [p for p in OCR_PACKAGES if not (staging / p).is_dir()]
    if any(w.name.lower().startswith("msvc_runtime") for w in wheels) and \
            not (staging / MSVC_DIR / "msvcp140.dll").is_file():
        missing.append(f"{MSVC_DIR}\\msvcp140.dll")
    if missing:
        shutil.rmtree(staging, ignore_errors=True)
        raise PatchError(f"The OCR runtime is incomplete after unpacking (missing {', '.join(missing)}).")
    (staging / "SKYGUARD_OCR_RUNTIME.json").write_text(json.dumps(
        {"patch": PATCH_VERSION, "python": tag, "wheels": [w.name for w in wheels]}, indent=1), encoding="utf-8")
    live = base / "site-packages"
    previous = None
    if live.exists():
        previous = base / f"site-packages.before-{PATCH_VERSION}-{stamp}"
        os.replace(live, previous)
    os.replace(staging, live)
    say(f"OCR runtime installed: {live}" + (f" (previous runtime kept as {previous.name})" if previous else ""))
    return {"installed": True, "site": str(live), "previous": str(previous) if previous else None}


def restore_ocr(info: dict | None) -> None:
    if not info or not info.get("installed"):
        return
    live = Path(info["site"])
    if live.exists():
        shutil.rmtree(live, ignore_errors=True)
    if info.get("previous") and Path(info["previous"]).exists():
        os.replace(info["previous"], live)
        say(f"Previous OCR runtime restored: {live}")
    else:
        say(f"OCR runtime removed: {live}")


def side_by_side(app: Path, data: Path, target: Path, python: str) -> Path:
    """A protected application folder: copy the application into a folder of the user and patch the copy."""
    if target.exists() and any(target.iterdir()):
        raise PatchError(f"{target} is not empty. Choose an empty folder for the side-by-side copy.")
    step(f"Copying the application to {target} (the original folder is not changed)")
    top = app.resolve()

    def skip(folder: str, names: list[str]) -> list[str]:  # the data folder and backups only at the top level
        out = [n for n in names if n == "__pycache__" or n.endswith(".pyc")]
        if Path(folder).resolve() == top:
            out += [n for n in names if n in ("data", "patch-backups", "logs")]
        return out
    shutil.copytree(app, target, ignore=skip, copy_function=_copy)
    launcher = target / f"START_SKYGUARD_{PATCH_VERSION}.cmd"
    lines = ["@echo off", f'set "QA_HOME={data}"', f'cd /d "{target}"', f'"{python}" -m qa_platform']
    launcher.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    say(f"Start the patched copy with {launcher.name}; it uses the same data folder {data}.")
    return target


MIGRATE = ("import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; "
           "from qa_platform.storage.db import Database; p = Paths().ensure(); d = Database(p.db_file); "
           "print(d.query_one('SELECT MAX(version) AS v FROM schema_version')['v']); d.close()")
# Every API module is imported too: create_app imports them only when the server starts, so a route that needs a
# package the runtime lacks (23.12.0 found python-multipart this way, in CI) would otherwise pass this check.
LOADS = ("import sys; sys.path.insert(0, '.'); import importlib, pkgutil, qa_platform, qa_platform.main, qa_platform.api as p; "
         "[importlib.import_module('qa_platform.api.' + m.name) for m in pkgutil.iter_modules(p.__path__)]; "
         "print(qa_platform.__version__)")
AGENT_LOADS = ("import sys; sys.path.insert(0, '.'); import skyguard_agent, skyguard_agent.agent, qa_platform.opsagent.service, "
               "qa_platform.api.opsagent; print(skyguard_agent.__version__)")
COMPONENTS_LOAD = ("import sys; sys.path.insert(0, '.'); from qa_platform.automation import components, semantic, runner, site_diagnostic, live_check; "
                   "from qa_platform.automation.browser import js; "
                   "print('ok' if 'window.__sgComp' in js('components') and components.stable_text('a, The price is AED 1k') == 'a' "
                   "and hasattr(components, 'set_count') and hasattr(semantic, 'suggestion_context') "
                   "and hasattr(runner.Runner, '_counter_row_click') and 'nearRows' in js('components') "
                   "and hasattr(runner.Runner, '_choice_field') and 'probe(want)' in js('components') "
                   "and hasattr(runner.Runner, '_semantic_candidates') and hasattr(runner.Runner, '_diagnose') "
                   "else 'component engine incomplete')")
STEPS_LOAD = ("import sys; sys.path.insert(0, '.'); from qa_platform.automation.framework import steps, walker, builder; "
              "p = steps.plan_steps([{'action': 'Log in', 'expected': 'Logged in'}, {'action': \"Enter 'Ann' in the First "
              "Name field.\", 'expected': \"The First Name field shows 'Ann'.\"}], 'The user is logged in and is on the "
              "checkout page.'); "
              "print('ok' if [i.kind for x in p for i in x.intents] == ['goto', 'login', 'reach', 'login', 'fill'] "
              "and hasattr(walker, 'StepWalker') and 'not_on_application' in builder.ACTIONS "
              "else 'step reader incomplete')")
LEARNING_LOAD = ("import sys; sys.path.insert(0, '.'); from skyguard_agent import journal, understanding, assistant, learning; "
                 "from skyguard_agent.assistant import canonical; "
                 "print('ok' if canonical('who authorises trips') == 'who authorize trips' and "
                 "hasattr(understanding, 'Miner') and hasattr(journal, 'Journal') and hasattr(learning, '_guided_only') "
                 "else 'learning engine incomplete')")
WORKSPACE_LOAD = ("import sys; sys.path.insert(0, '.'); from qa_platform.comparison import workspace, formula, screen_reader; "
                  "from qa_platform.api import workspace as api; "
                  "print('ok' if formula.evaluate('[B:A] / [B:T] * 100', lambda l, f: {'A': 1, 'T': 4}[f]) == 25 "
                  "and hasattr(workspace.Workspace, 'run') and hasattr(screen_reader, 'read_screen') "
                  "and any(r.path == '/api/comparison/workspace/{pid}/run' for r in api.router.routes) "
                  "else 'workspace incomplete')")
WORKSPACE_239_LOAD = ("import sys; sys.path.insert(0, '.'); from qa_platform.comparison import filters, semantics, workspace; "
                      "c = semantics.concept('Order Volume', 'number', ['12', '8']); "
                      "print('ok' if c['concept'] == 'quantity' and not semantics.can_combine([{'concept': 'money'}, "
                      "{'concept': 'percent'}], '+') and hasattr(filters, 'FilterSession') "
                      "and hasattr(workspace.Workspace, 'apply_fetch') and 'NOT_COMPARABLE' in workspace.ORDER "
                      "else 'comparison 23.9.0 incomplete')")
BROWSER_POLICY_LOAD = ("import sys; sys.path.insert(0, '.'); from qa_platform.automation import browser as b; "
                       "pb = {'window_mode': 'maximized'}; "
                       "print('ok' if '--start-maximized' in b.launch_args(False, pb) and "
                       "'--start-maximized' not in b.launch_args(True, pb) and "
                       "b.context_options(False, pb).get('no_viewport') else 'browser policy incomplete')")
OCR_PROBE = ("import sys; sys.path.insert(0, '.'); from qa_platform.comparison import ocr; d = ocr.diagnose(); "
             "e = next(x for x in d['engines'] if x['engine'] == 'rapidocr'); "
             "w = next((x for x in d['engines'] if x['engine'] == 'windows'), {}); "
             "print(e['status'] + ': ' + str(e.get('detail'))[:400] + ' | windows: ' + str(w.get('status')))")
AGENT_MIGRATE = ("import sys; sys.path.insert(0, '.'); from pathlib import Path; from qa_platform.config import Paths; "
                 "from skyguard_agent.store import Store; from skyguard_agent.autopilot import MIGRATION; "
                 "p = Paths().ensure(); s = Store(Path(p.root) / 'opsagent' / "
                 "'knowledge.sqlite3'); s._conn.executescript(MIGRATION); print(s.schema_version); s.close()")
SIMPLE_AGENT_LOAD = ("import sys; sys.path.insert(0, '.'); from pathlib import Path; "
                     "from skyguard_agent import autopilot, navrec; from qa_platform.api import agent_simple, workspace as w; "
                     "from qa_platform.comparison import workspace, guided_nav; "
                     "r = navrec.events_to_recording([{'kind': 'fill', 'url': 'http://a/login', 'value': '[secret]', 'secret': True, "
                     "'element': {'tag': 'input', 'label': 'Password', 'secret': True}}, "
                     "{'kind': 'click', 'url': 'http://a/login', 'element': {'tag': 'button', 'name': 'Sign in'}}, "
                     "{'kind': 'click', 'url': 'http://a/', 'element': {'tag': 'a', 'name': 'Reports'}}]); "
                     "print('ok' if autopilot.match_by_verb('Authorize all approved trips', [{'key': 'Approve trip', 'data': {'operation': 'approve', 'entity': 'trip'}}, "
                     "{'key': 'Authorize trip', 'data': {'operation': 'authorize', 'entity': 'trip'}}])['key'] == 'Authorize trip' "
                     "and [x.get('text') for x in r['steps']] == ['Reports'] "
                     "and any(x.path == '/api/ops/simple/command' for x in agent_simple.router.routes) "
                     "and any(x.path.endswith('/guided/import') for x in w.router.routes) "
                     "and hasattr(workspace.Workspace, 'guided_replay') and hasattr(guided_nav, 'Recorder') "
                     "and Path('web/js/pages/ai-agent.js').is_file() else 'simple agent or guided recording incomplete')")

RELEASE_2311_LOAD = ("import sys; sys.path.insert(0, '.'); from pathlib import Path; "
                     "from qa_platform.automation import smart_wait, python_scripts, pytest_export; "
                     "from qa_platform.defects import service as ds, triage; from qa_platform.api import defects as dapi; "
                     "v = python_scripts.validate('import subprocess\\n'); "
                     "print('ok' if smart_wait.clean_policy({'enabled': True, 'mode': 'smart'})['enabled'] "
                     "and '# --- SMART WAIT BEGIN' in smart_wait.embedded_source() and not v['ok'] "
                     "and 'publication_failed' in ds.OPEN_STATUSES and {'application_defect', 'automation_script', 'test_data', 'environment', 'timing', 'inconclusive'} <= set(triage.CLASSES) "
                     "and any(r.path.endswith('/publish') for r in dapi.router.routes) "
                     "and Path('web/js/python-editor.js').is_file() and Path('web/js/smart-wait-ui.js').is_file() "
                     "else 'Python editor, Smart Wait or Defect Review incomplete')")
AGENT_REPAIR_231101_LOAD = ("import sys; sys.path.insert(0, '.'); "
                            "from skyguard_agent.intent import classify; from skyguard_agent.autopilot import Autopilot; "
                            "from qa_platform.api import agent_simple as a; "
                            "paths = {r.path for r in a.router.routes}; "
                            "print('ok' if classify('navigate to online store', app_names=['Online Store']).kind == 'navigate' "
                            "and classify('cancel order 5').kind == 'cancel_delete' "
                            "and hasattr(Autopilot, 'verify_retrieval') and hasattr(Autopilot, 'suggestions') "
                            "and any(p.endswith('/suggestions') for p in paths) and any(p.endswith('/verify/{app_id}') for p in paths) "
                            "else 'AI Agent repair incomplete')")
MISSING_INFO_231102_LOAD = ("import sys; sys.path.insert(0, '.'); "
                            "from skyguard_agent.autopilot import parse_answer; from skyguard_agent import secrets_guard, sessions; "
                            "f = [{'label': 'First Name', 'name': 'firstName'}, {'label': 'Last Name', 'name': 'lastName'}, "
                            "{'label': 'Zip/Postal Code', 'name': 'postalCode'}]; "
                            "print('ok' if parse_answer('First Name; muhammad', f) == ({'First Name': 'muhammad'}, []) "
                            "and parse_answer('pre pare it', f) == ({}, []) "
                            "and not secrets_guard.inspect('Zip/Postal Code: 54000').contains_secret "
                            "and secrets_guard.inspect('the code is 482913').contains_secret "
                            "and 'sign_in_confirmed' in sessions.SessionState().to_dict() "
                            "else 'Missing information follow-up incomplete')")
API_TESTING_231200_LOAD = r"""
import sys
sys.path.insert(0, '.')
from qa_platform.apitesting import importers, safety, scriptgen, service
from qa_platform.agents.registry import AGENTS
spec = importers.import_openapi({'a.yaml': 'openapi: 3.0.3\ninfo: {title: T, version: "1"}\npaths:\n  /x:\n    get:\n'
                                           '      responses:\n        "200": {description: ok}\n'}, 'a.yaml')
pol = safety.Policy(['10.1.1.1:8080'], own_port=8765, resolver=lambda h, p: [h])
try:
    pol.check_url('http://127.0.0.1:8765/api/users')
    own_refused = False
except Exception:
    own_refused = True
ids = [a.id for a in AGENTS]
ok = (len(spec['endpoints']) == 1 and own_refused and pol.check_url('http://10.1.1.1:8080/x')
      and scriptgen.validate('import httpx\n')['ok'] and ids.count('api_testing') == 1 and len(ids) == len(set(ids)))
print('ok' if ok else 'API Testing incomplete')
"""


def verify(app: Path, data: Path, python: str, before_counts: dict[str, int] | None = None) -> dict:
    report: dict = {"checks": []}

    def check(name: str, ok: bool, detail: str) -> None:
        report["checks"].append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        say(f"  {name:<34} {'PASS' if ok else 'FAIL'}  {detail}")

    def warn(name: str, detail: str) -> None:
        report["checks"].append({"name": name, "status": "WARN", "detail": detail})
        say(f"  {name:<34} WARN  {detail}")

    v = version_of(app)
    check("Version file", v == PATCH_VERSION, v)
    try:
        loaded = run_py(python, app, data, LOADS)
        check("Application loads", loaded.splitlines()[-1] == PATCH_VERSION, loaded.splitlines()[-1])
    except PatchError as exc:
        check("Application loads", False, str(exc))
    try:
        agent_v = run_py(python, app, data, AGENT_LOADS).splitlines()[-1]
        check("Operations Agent loads", bool(agent_v), f"agent core {agent_v}")
    except PatchError as exc:
        check("Operations Agent loads", False, str(exc))
    try:
        comp = run_py(python, app, data, COMPONENTS_LOAD).splitlines()[-1]
        check("Component engine (23.7.1)", comp == "ok", comp)
    except PatchError as exc:
        check("Component engine (23.7.1)", False, str(exc))
    try:
        st = run_py(python, app, data, STEPS_LOAD).splitlines()[-1]
        check("Step reader (23.7.2)", st == "ok", st)
    except PatchError as exc:
        check("Step reader (23.7.2)", False, str(exc))
    try:
        ws = run_py(python, app, data, WORKSPACE_LOAD).splitlines()[-1]
        check("Integration Comparison (23.8.0)", ws == "ok", ws)
    except PatchError as exc:
        check("Integration Comparison (23.8.0)", False, str(exc))
    try:
        ws9 = run_py(python, app, data, WORKSPACE_239_LOAD).splitlines()[-1]
        check("Comparison 23.9.0 (filters, meaning)", ws9 == "ok", ws9)
    except PatchError as exc:
        check("Comparison 23.9.0 (filters, meaning)", False, str(exc))
    try:
        sa = run_py(python, app, data, SIMPLE_AGENT_LOAD).splitlines()[-1]
        check("AI Agent and guided recording (23.10.0)", sa == "ok", sa)
    except PatchError as exc:
        check("AI Agent and guided recording (23.10.0)", False, str(exc))
    try:
        r11 = run_py(python, app, data, RELEASE_2311_LOAD).splitlines()[-1]
        check("Python editor, Smart Wait, Defect Review (23.11.0)", r11 == "ok", r11)
    except PatchError as exc:
        check("Python editor, Smart Wait, Defect Review (23.11.0)", False, str(exc))
    try:
        r111 = run_py(python, app, data, AGENT_REPAIR_231101_LOAD).splitlines()[-1]
        check("AI Agent repair (23.11.1)", r111 == "ok", r111)
    except PatchError as exc:
        check("AI Agent repair (23.11.1)", False, str(exc))
    try:
        r112 = run_py(python, app, data, MISSING_INFO_231102_LOAD).splitlines()[-1]
        check("Missing information follow-up (23.11.2)", r112 == "ok", r112)
    except PatchError as exc:
        check("Missing information follow-up (23.11.2)", False, str(exc))
    try:
        r120 = run_py(python, app, data, API_TESTING_231200_LOAD).splitlines()[-1]
        check("API Testing and new agents (23.12.0)", r120 == "ok", r120)
    except PatchError as exc:
        check("API Testing and new agents (23.12.0)", False, str(exc))
    try:
        bp = run_py(python, app, data, BROWSER_POLICY_LOAD).splitlines()[-1]
        check("Maximized browser policy", bp == "ok", bp)
    except PatchError as exc:
        check("Maximized browser policy", False, str(exc))
    tag = python_tag(python)
    if tag == "win32-cp313-64bit":
        try:
            ocr_state = run_py(python, app, data, OCR_PROBE).splitlines()[-1]
        except PatchError as exc:
            ocr_state = f"broken: {exc}"
        if ocr_state.startswith("ready"):
            check("Offline OCR engine", True, ocr_state)
        elif ocr_state.startswith("broken") and "windows: available" in ocr_state:
            # the rest of the release works; screenshots are read by Windows OCR until the bundled engine runs
            warn("Offline OCR engine", ocr_state.split(" | ")[0] + " | screenshots will be read with Windows OCR")
        else:
            check("Offline OCR engine", False, ocr_state)
    else:
        say(f"  {'Offline OCR engine':<34} SKIP  bundled runtime is for Windows / Python 3.13 (this is {tag})")
    try:
        learn = run_py(python, app, data, LEARNING_LOAD).splitlines()[-1]
        check("Passive learning engine (23.7.0)", learn == "ok", learn)
    except PatchError as exc:
        check("Passive learning engine (23.7.0)", False, str(exc))
    kdb = data / "opsagent" / "knowledge.sqlite3"
    if kdb.exists():
        conn = sqlite3.connect(f"file:{kdb}?mode=ro", uri=True)
        try:
            kver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            kint = conn.execute("PRAGMA integrity_check").fetchone()[0]
            ktables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        want = {"applications", "accounts", "knowledge", "locators", "runs", "run_items", "audit", "observations",
                "app_model", "conversation_state"}
        check("Knowledge database", kver == AGENT_SCHEMA and kint == "ok" and want <= ktables,
              f"schema {kver}, integrity {kint}, tables {'present' if want <= ktables else 'missing'}")
    else:
        check("Knowledge database", True, "created at first start")
    db = data / "qa_platform.sqlite3"
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            cols = [r[1] for r in conn.execute("PRAGMA table_info(app_credentials)")]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        check("Database migrations", ver == MIGRATION_COUNT, f"schema version {ver} (expected {MIGRATION_COUNT})")
        check("Database integrity", integrity == "ok", integrity)
        rev = {"defect_events", "script_python", "script_python_state"}
        check("Review and Python tables", rev <= tables, "present" if rev <= tables else "missing: " + ", ".join(sorted(rev - tables)))
        cmp = {"cmp_projects", "cmp_records", "cmp_audit", "cmp_ws_screens", "cmp_ws_state", "cmp_ws_configs", "cmp_ws_decisions"}
        check("Comparison tables", cmp <= tables, "present" if cmp <= tables else "missing: " + ", ".join(sorted(cmp - tables)))
        check("Login sign-in method column", "auth_method" in cols, "present" if "auth_method" in cols else "missing")
        if before_counts is not None:
            after = row_counts(db)
            lost = {t: (n, after.get(t, 0)) for t, n in before_counts.items() if after.get(t, 0) < n}
            check("No data lost", not lost, "every table kept its rows" if not lost else
                  "; ".join(f"{t}: {a} -> {b}" for t, (a, b) in lost.items()))
    else:
        check("Database", True, "no database yet (created at first start)")
    missing = [rel for rel in json.loads((app / f"PATCH_FILES_{PATCH_VERSION}.json").read_text(encoding="utf-8"))["files"]
               if not (app / rel).exists()] if (app / f"PATCH_FILES_{PATCH_VERSION}.json").exists() else [f"PATCH_FILES_{PATCH_VERSION}.json"]
    check("Patch files present", not missing, "all" if not missing else ", ".join(missing[:5]))
    report["ok"] = all(c["status"] in ("PASS", "WARN") for c in report["checks"])
    return report


def restore_files(app: Path, backup: Path, added: list[str]) -> None:
    for pkg in ("qa_platform", "skyguard_agent"):
        if app.joinpath(pkg).is_dir():
            for cache in app.joinpath(pkg).rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)
    files_dir = backup / "files"
    if files_dir.exists():
        for src in files_dir.rglob("*"):
            if src.is_file():
                dst = app / src.relative_to(files_dir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                _copy(src, dst)
    for rel in added:  # files this patch added (application code only, never data)
        p = app / rel
        if p.is_file():
            p.unlink()
        parent = p.parent
        while parent != app and parent.is_dir() and not any(parent.iterdir()):  # folders the patch created
            parent.rmdir()
            parent = parent.parent


def apply(args: argparse.Namespace) -> int:
    patch_dir = Path(__file__).resolve().parent
    python = args.python or sys.executable
    step(f"SkyGuard QA {PATCH_VERSION}: cumulative upgrade from 23.3.35 "
         "(23.4.0 + 23.5.0 + 23.6.0 + 23.6.1 + 23.7.0 + 23.7.1 + 23.7.2 + 23.8.0 + 23.9.0 + 23.10.0 + 23.11.0 + 23.11.1 + 23.11.2 + 23.12.0)")
    app, root, data = locate(args.app, args.data)
    before = version_of(app)
    if not writable(app):
        if args.side_by_side is None:
            raise PatchError(f"The application folder {app} cannot be written by this user (a protected folder such "
                             "as the machine-wide programs folder). Nothing was changed. Either ask the person who installed SkyGuard QA "
                             "to apply the patch, or run the patch with --side-by-side to install a patched copy in "
                             f"{user_dir() / ('app-' + PATCH_VERSION)} (your original installation stays as it is).")
        if before not in BASELINES:
            raise PatchError(f"This upgrade installs on {BASELINE} (found {before}). Nothing was changed.")
        target = Path(args.side_by_side or (user_dir() / f"app-{PATCH_VERSION}")).expanduser().resolve()
        app = side_by_side(app, data, target, python)
        root = app
    say(f"Application folder : {app}\nData folder        : {data}  (kept)\nInstalled version  : {before} -> {PATCH_VERSION}")
    if before not in BASELINES:
        raise PatchError(f"This upgrade installs on {BASELINE} (found {before}). Nothing was changed. "
                         + ("Versions before 23.3.35 must first be brought to 23.3.35 with the earlier patches."
                            if _older(before) else "This version is not one of the SkyGuard QA releases it supports."))

    step("Checking the patch files (SHA-256)")
    files = check_payload(patch_dir)
    check_ocr_wheels(patch_dir)
    say(f"{len(files)} files and {len(ocr_wheels(patch_dir))} OCR runtime wheels verified.")

    step("Checking disk space, permissions and that the application is stopped")
    db = data / "qa_platform.sqlite3"
    need = tree_mb([patch_dir / "app"]) + tree_mb([app / rel for rel in files]) + tree_mb([db]) + MARGIN_MB + \
        3 * tree_mb(ocr_wheels(patch_dir))  # unpacked runtime + the previous one kept for rollback
    have = free_mb(root)
    say(f"Free space: {have} MB, needed: {need:.0f} MB")
    if have < need:
        raise PatchError(f"Not enough free disk space on {root}: {have} MB free, {need:.0f} MB needed.")
    for folder in (app, data, root / "patch-backups"):
        if not writable(folder):
            raise PatchError(f"The folder {folder} cannot be written. Run as the user who installed SkyGuard QA.")
    port = port_of(data)
    if running(port) and not args.skip_running_check:
        raise PatchError(f"SkyGuard QA is still running (port {port} answers). Close it, then run the patch again.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = root / "patch-backups" / f"{before}-to-{PATCH_VERSION}-{stamp}"
    step("Backing up")
    (backup / "files").mkdir(parents=True, exist_ok=True)
    replaced, added = [], []
    for rel in files:
        src = app / rel
        if src.exists():
            dst = backup / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _copy(src, dst)
            replaced.append(rel)
        else:
            added.append(rel)
    before_counts = row_counts(db)
    if db.exists():
        sqlite_copy(db, backup / "qa_platform.sqlite3")
    kdb = data / "opsagent" / "knowledge.sqlite3"
    if kdb.exists():  # an installation that already has 23.5.0 or later
        sqlite_copy(kdb, backup / "knowledge.sqlite3")
    if (data / "settings.json").exists():
        _copy(data / "settings.json", backup / "settings.json")
    info = {"from": before, "to": PATCH_VERSION, "created": stamp, "app": str(app), "data": str(data),
            "replaced": replaced, "added": added, "database": db.exists(), "row_counts": before_counts}
    (backup / "BACKUP.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    say(f"Backup: {backup}  ({len(replaced)} files replaced, {len(added)} new, database {'copied' if db.exists() else 'not present yet'})")

    ocr_info: dict | None = None
    try:
        step("Copying the new files")
        for pkg in ("qa_platform", "skyguard_agent"):
            if app.joinpath(pkg).is_dir():
                for cache in app.joinpath(pkg).rglob("__pycache__"):
                    shutil.rmtree(cache, ignore_errors=True)
        for rel in files:
            src, dst = patch_dir / "app" / rel, app / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".patch-tmp")
            _copy(src, tmp)
            os.replace(tmp, dst)
        (app / f"PATCH_FILES_{PATCH_VERSION}.json").write_text(json.dumps({"version": PATCH_VERSION, "files": files}, indent=1),
                                                     encoding="utf-8")
        say(f"{len(files)} files copied.")
        if args.fail_after_copy:  # test hook: proves the automatic rollback
            raise PatchError("simulated failure after copying (--fail-after-copy)")

        step("Installing the offline OCR runtime")
        ocr_info = install_ocr(patch_dir, app, python, stamp, force=args.force_ocr)
        info["ocr"] = ocr_info
        (backup / "BACKUP.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

        step("Migrating the database")
        v = run_py(python, app, data, MIGRATE).splitlines()[-1]
        say(f"Main database schema version {v} (expected {MIGRATION_COUNT}).")
        kv = run_py(python, app, data, AGENT_MIGRATE).splitlines()[-1]
        say(f"Operations Agent knowledge database schema version {kv} ({data / 'opsagent' / 'knowledge.sqlite3'}).")

        step("Verifying")
        report = verify(app, data, python, before_counts)
        if not report["ok"]:
            raise PatchError("verification failed: " + "; ".join(c["name"] for c in report["checks"] if c["status"] == "FAIL"))
    except Exception as exc:  # noqa: BLE001 - every failure rolls back
        step("Restoring the previous version")
        restore_files(app, backup, added)
        restore_ocr(ocr_info)
        (app / f"PATCH_FILES_{PATCH_VERSION}.json").unlink(missing_ok=True)
        if db.exists() and (backup / "qa_platform.sqlite3").exists():
            kept = db.with_name(f"qa_platform.failed-upgrade-{stamp}.sqlite3")
            for suffix in ("-wal", "-shm"):
                side = Path(str(db) + suffix)
                if side.exists():
                    os.replace(side, Path(str(kept) + suffix))
            os.replace(db, kept)
            _copy(backup / "qa_platform.sqlite3", db)
            say(f"Database restored from the backup; the database as it was after the failed step is kept as {kept.name}.")
        if kdb.exists() and not (backup / "knowledge.sqlite3").exists():
            kept = kdb.with_name(f"knowledge.failed-upgrade-{stamp}.sqlite3")
            for suffix in ("-wal", "-shm"):
                side = Path(str(kdb) + suffix)
                if side.exists():
                    os.replace(side, Path(str(kept) + suffix))
            os.replace(kdb, kept)
            say(f"The knowledge database created during the failed upgrade is kept as {kept.name}.")
        say(f"Previous version {version_of(app)} restored.")
        raise PatchError(f"{exc}. Nothing was lost; the previous version was restored. Backup: {backup}") from exc

    history = app / "PATCH_HISTORY.json"
    entries = json.loads(history.read_text(encoding="utf-8")) if history.exists() else []
    entries.append({"version": PATCH_VERSION, "from": before, "applied": stamp, "backup": str(backup)})
    history.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    (backup / "VERIFY.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    step("Result")
    say(f"Patched {before} -> {PATCH_VERSION}. Backup for rollback: {backup}")
    return 0


def rollback(args: argparse.Namespace) -> int:
    app, root, data = locate(args.app, args.data)
    backups = sorted((root / "patch-backups").glob(f"*-to-{PATCH_VERSION}-*")) if not args.backup else [Path(args.backup)]
    if not backups:
        raise PatchError(f"No {PATCH_VERSION} backup found in {root / 'patch-backups'}.")
    backup = backups[-1]
    info = json.loads((backup / "BACKUP.json").read_text(encoding="utf-8"))
    port = port_of(data)
    if running(port) and not args.skip_running_check:
        raise PatchError(f"SkyGuard QA is still running (port {port} answers). Close it first.")
    step(f"Rolling back {PATCH_VERSION} -> {info['from']} from {backup}")
    restore_files(app, backup, info["added"])
    restore_ocr(info.get("ocr"))
    (app / f"PATCH_FILES_{PATCH_VERSION}.json").unlink(missing_ok=True)
    db = data / "qa_platform.sqlite3"
    if args.restore_database and (backup / "qa_platform.sqlite3").exists():
        kept = db.with_name(f"qa_platform.before-rollback-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
        if db.exists():
            sqlite_copy(db, kept)
            say(f"Current database kept as {kept.name}.")
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        _copy(backup / "qa_platform.sqlite3", db)
        say("Database restored from the copy taken before the upgrade.")
    else:
        say(f"Databases kept as they are ({info['from']} reads them as they are; nothing is lost).")
    say(f"Version now {version_of(app)}.")
    return 0


class _Tee:
    def __init__(self, stream, fh) -> None:
        self.stream, self.fh = stream, fh

    def write(self, text: str) -> int:
        self.fh.write(text)
        self.fh.flush()
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()
        self.fh.flush()


def _tee(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)


def _older(v: str) -> bool:
    try:
        return tuple(int(x) for x in v.split(".")) < (23, 3, 35)
    except ValueError:
        return False


def find_python(app: Path) -> str | None:
    for rel in (("runtime", "python", "python.exe"), (".venv", "Scripts", "python.exe"), ("runtime", "python", "bin",
                                                                                          "python3"), (".venv", "bin", "python")):
        p = app.joinpath(*rel)
        if p.is_file():
            return str(p)
    return None


def detect(args: argparse.Namespace) -> int:
    """What the Setup wizard shows on its Detect page, as one line of JSON: never changes anything."""
    out: dict = {"patch_version": PATCH_VERSION, "supported_versions": list(RELEASES)}
    try:
        app, root, data = locate(args.app, args.data)
    except PatchError as exc:
        out.update(found=False, supported=False, message=str(exc))
        print(json.dumps(out))
        if args.ini:
            Path(args.ini).write_text(f"[detect]\r\nfound=0\r\nsupported=0\r\nmessage={exc}\r\n", encoding="utf-16")
        return 2
    v = version_of(app)
    python = args.python or find_python(app)
    can_write = writable(app)
    out.update(found=True, app=str(app), root=str(root), data=str(data), version=v, python=python,
               supported=v in BASELINES or v == PATCH_VERSION, already_latest=v == PATCH_VERSION, writable=can_write,
               running=running(port_of(data)), port=port_of(data), free_mb=free_mb(root),
               database=(data / "qa_platform.sqlite3").exists(),
               backup_folder=str(root / "patch-backups"))
    if v not in BASELINES and v != PATCH_VERSION:
        out["message"] = (f"SkyGuard QA {v} was found. This upgrade installs on {BASELINE}. "
                          + ("Bring it to 23.3.35 with the earlier patches first." if _older(v) else
                             "This version is not supported; nothing will be changed."))
    elif not python:
        out["supported"] = False
        out["message"] = f"The application's Python was not found in {app}\\runtime\\python or {app}\\.venv."
    elif v == PATCH_VERSION:
        out["message"] = f"SkyGuard QA {v} is already installed. Setup can apply it again (repair) or verify it."
    else:
        out["message"] = f"SkyGuard QA {v} was found and can be upgraded to {PATCH_VERSION}."
    if not can_write and out["supported"]:
        out["message"] += (" The application folder cannot be written by this user: Setup will install a patched copy "
                           f"in {user_dir() / ('app-' + PATCH_VERSION)} (side by side) and keep the original.")
    print(json.dumps(out))
    if args.ini:  # the Setup wizard reads this with ReadINIStr
        lines = ["[detect]"] + [f"{k}={'1' if v is True else '0' if v is False else '' if v is None else v}"
                                for k, v in out.items() if not isinstance(v, list)]
        Path(args.ini).write_text("\r\n".join(lines) + "\r\n", encoding="utf-16")
    return 0 if out["supported"] else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("apply", "verify", "rollback", "detect"))
    ap.add_argument("--app", required=True)
    ap.add_argument("--data")
    ap.add_argument("--python")
    ap.add_argument("--backup")
    ap.add_argument("--restore-database", action="store_true")
    ap.add_argument("--skip-running-check", action="store_true")
    ap.add_argument("--side-by-side", nargs="?", const="", default=None,
                    help="protected application folder: patch a copy in this folder (default %%LOCALAPPDATA%%\\SkyGuardQA)")
    ap.add_argument("--ini", help="detect: also write the result as an INI file (for the Setup wizard)")
    ap.add_argument("--log", help="also write every message to this file")
    ap.add_argument("--fail-after-copy", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--force-ocr", action="store_true", help=argparse.SUPPRESS)  # tests: unpack on another platform
    args = ap.parse_args(argv)
    if args.log:
        _tee(Path(args.log))
    try:
        if args.command == "apply":
            return apply(args)
        if args.command == "rollback":
            return rollback(args)
        if args.command == "detect":
            return detect(args)
        app, _root, data = locate(args.app, args.data)
        step(f"Verifying {app}")
        return 0 if verify(app, data, args.python or sys.executable)["ok"] else 1
    except PatchError as exc:
        print(f"\nPATCH FAILED: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
