"""Build the offline portable Windows package.

Run once on a 64-bit Windows PC with Internet (any Python 3.10+):

    py -3 packaging\\build_offline_package.py [--ai-dir D:\\models\\qwen] [--zip]

The result, ``dist_offline\\AIAgenticTestAgent-Offline-<version>\\``, runs on PCs without Internet:

    INSTALL_OFFLINE.cmd      per-user install, no administrator rights
    VALIDATE_OFFLINE.cmd     checks the package on the target PC
    README_OFFLINE.txt
    payload\\                 the application with runtime\\python, runtime\\jre, runtime\\apache-jmeter,
                             runtime\\ms-playwright, runtime\\speech (offline voice commands)
                             (and runtime\\ai if --ai-dir is given), MANIFEST.json, DEPENDENCIES.md

Every download is pinned in packaging/offline-lock.json and checked against its SHA-256; Python packages are
installed from packaging/requirements-offline-win.txt with --require-hashes. Chromium is fetched by Playwright's
own installer for the pinned Playwright version. Models are never downloaded: --ai-dir copies a folder you provide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from qa_platform import __version__  # noqa: E402
from qa_platform.clock import now_iso  # noqa: E402
from qa_platform.offline import manifest as mf  # noqa: E402

LOCK = json.loads((REPO / "packaging" / "offline-lock.json").read_text(encoding="utf-8"))
REQUIREMENTS = REPO / "packaging" / "requirements-offline-win.txt"
TEMPLATES = REPO / "packaging" / "templates"
APP_ITEMS = ("qa_platform", "web", "docs", "README.md", "requirements.txt", "START_QA_PLATFORM.cmd")
# Pure-Python packages published only as source (hash-checked like the rest); vosk needs srt.
SOURCE_ONLY = ("srt",)
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def fetch(key: str, cache: Path) -> Path:
    """Download a pinned component once into the cache and verify its checksum every time."""
    item = LOCK[key]
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / item["url"].rsplit("/", 1)[1].replace("%2B", "+")
    if not target.exists() or sha256(target) != item["sha256"]:
        log(f"Downloading {item['name']} {item['version']}")
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(item["url"], timeout=120) as resp, partial.open("wb") as out:
            shutil.copyfileobj(resp, out)
        partial.replace(target)
    actual = sha256(target)
    if actual != item["sha256"]:
        target.unlink()
        raise SystemExit(f"Checksum mismatch for {item['name']}: expected {item['sha256']}, got {actual}. Not used.")
    log(f"Verified {target.name} (sha256 {actual[:16]}…)")
    return target


def _safe_members(names: list[str], dest: Path) -> None:
    root = dest.resolve()
    for name in names:
        if not (dest / name).resolve().is_relative_to(root):
            raise SystemExit(f"Refusing archive entry outside the target folder: {name}")


def extract_zip(archive: Path, dest: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(archive) as z:
        members = [m for m in z.namelist() if m.startswith(prefix) and not m.endswith("/")]
        _safe_members([m[len(prefix):] for m in members], dest)
        for m in members:
            out = dest / m[len(prefix):]
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def single_root(folder: Path) -> Path:
    children = [c for c in folder.iterdir() if c.is_dir()]
    if len(children) != 1:
        raise SystemExit(f"Expected one folder inside {folder}, found {[c.name for c in children]}")
    return children[0]


def stage_app(payload: Path) -> None:
    for item in APP_ITEMS:
        src = REPO / item
        if src.is_dir():
            shutil.copytree(src, payload / item, ignore=IGNORE)
        else:
            shutil.copy2(src, payload / item)
    shutil.copy2(TEMPLATES / "VALIDATE_OFFLINE.cmd", payload / "VALIDATE_OFFLINE.cmd")


def stage_python(payload: Path, cache: Path) -> Path:
    runtime = payload / "runtime" / "python"
    extract_zip(fetch("python", cache), runtime, prefix="tools/")
    python = runtime / "python.exe"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and not k.startswith("PIP_")}
    run([str(python), "-E", "-s", "-m", "ensurepip", "--default-pip"], env=env)
    log("Installing Python packages (hash-checked)")
    run([str(python), "-E", "-s", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
         "--require-hashes", "--only-binary=:all:", *(f"--no-binary={p}" for p in SOURCE_ONLY),
         "--no-warn-script-location", "-r", str(REQUIREMENTS)], env=env)
    run([str(python), "-E", "-s", "-m", "pip", "uninstall", "-y", "pip"], env=env)  # not needed at run time
    return python


def stage_java_and_jmeter(payload: Path, cache: Path) -> None:
    runtime = payload / "runtime"
    tmp = runtime / "_jre"
    extract_zip(fetch("jre", cache), tmp)
    single_root(tmp).rename(runtime / "jre")
    tmp.rmdir()
    archive = fetch("jmeter", cache)
    with tarfile.open(archive) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(runtime, filter="data")
        else:  # Python without extraction filters: check every member ourselves
            members = [m for m in t.getmembers() if m.isfile() or m.isdir()]
            _safe_members([m.name for m in members], runtime)
            t.extractall(runtime, members=members)
    log(f"Java and JMeter staged in {runtime}")


def stage_speech(payload: Path, cache: Path) -> None:
    """The small offline speech model, so voice commands work without a speech server or Internet."""
    speech = payload / "runtime" / "speech"
    extract_zip(fetch("speech_model", cache), speech)
    log(f"Offline speech model staged in {speech}")


def stage_chromium(payload: Path, python: Path) -> list[dict]:
    browsers = payload / "runtime" / "ms-playwright"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    log("Downloading Chromium with Playwright's installer")
    run([str(python), "-E", "-s", "-m", "playwright", "install", "chromium"], env=env)
    return [{"name": f"Playwright browser {d.name}", "version": d.name.rsplit("-", 1)[-1],
             "source": "Playwright installer (playwright install chromium)", "license": "BSD-3-Clause (Chromium)"}
            for d in sorted(browsers.iterdir()) if d.is_dir()]


def stage_ai(payload: Path, ai_dir: Path | None) -> list[dict]:
    if not ai_dir:
        return []
    models = sorted(ai_dir.rglob("*.gguf"))
    if not models:
        raise SystemExit(f"{ai_dir} contains no .gguf model.")
    shutil.copytree(ai_dir, payload / "runtime" / "ai")
    log(f"Included On-Premise AI files from {ai_dir} ({', '.join(m.name for m in models)})")
    return [{"name": f"On-Premise AI model {m.name}", "version": "", "source": "provided with --ai-dir", "license": "see model card"}
            for m in models]


def packages(python: Path) -> list[dict]:
    code = ("import json,importlib.metadata as m;print(json.dumps(sorted([{'name':d.metadata['Name'].lower(),"
            "'version':d.version,'license':(d.metadata.get('License-Expression') or d.metadata.get('License') or '')"
            ".splitlines()[0][:60] if (d.metadata.get('License-Expression') or d.metadata.get('License')) else ''}"
            " for d in m.distributions()],key=lambda x:x['name'])))")
    out = subprocess.run([str(python), "-E", "-s", "-c", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def inventory_md(info: dict) -> str:
    lines = [f"# Dependencies of AI Agentic Test Agent Enterprise {info['version']} (offline package)", "",
             f"Built {info['built_at']} with Python {info['python']}.", "", "## Runtime components", "",
             "| Component | Version | Source | Checksum | License |", "|---|---|---|---|---|"]
    for c in info["components"]:
        lines.append(f"| {c['name']} | {c.get('version', '')} | {c.get('source', '')} | {c.get('sha256', '')[:16]} | {c.get('license', '')} |")
    lines += ["", "## Python packages", "", "| Package | Version | License |", "|---|---|---|"]
    lines += [f"| {p['name']} | {p['version']} | {p['license']} |" for p in info["packages"]]
    lines += ["", "Python packages were installed from `packaging/requirements-offline-win.txt` with `--require-hashes`.", ""]
    return "\n".join(lines)


def write_package_files(package: Path) -> None:
    shutil.copy2(TEMPLATES / "INSTALL_OFFLINE.cmd", package / "INSTALL_OFFLINE.cmd")
    shutil.copy2(TEMPLATES / "VALIDATE_OFFLINE_PACKAGE.cmd", package / "VALIDATE_OFFLINE.cmd")
    shutil.copy2(TEMPLATES / "README_OFFLINE.txt", package / "README_OFFLINE.txt")


def run(cmd: list[str], **kw) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the offline portable Windows package.")
    ap.add_argument("--out", type=Path, default=REPO / "dist_offline")
    ap.add_argument("--cache", type=Path, help="download cache (default: <out>/downloads)")
    ap.add_argument("--ai-dir", type=Path, help="folder with llama-server and a .gguf model to include (copied, never downloaded)")
    ap.add_argument("--zip", action="store_true", help="also write a .zip of the package")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args(argv)
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build the offline package on 64-bit Windows: it bundles Windows Python, Java and Chromium.")

    package = args.out / f"AIAgenticTestAgent-Offline-{__version__}"
    payload = package / "payload"
    if package.exists():
        log(f"Removing the previous build {package}")
        shutil.rmtree(package)
    payload.mkdir(parents=True)
    cache = args.cache or args.out / "downloads"

    stage_app(payload)
    python = stage_python(payload, cache)
    stage_java_and_jmeter(payload, cache)
    stage_speech(payload, cache)
    components = [{**{k: LOCK[key][k] for k in ("name", "version", "license", "sha256")},
                   "source": LOCK[key]["url"]} for key in ("python", "jre", "jmeter", "speech_model")]
    components += stage_chromium(payload, python)
    components += stage_ai(payload, args.ai_dir)
    info = {"name": "AI Agentic Test Agent Enterprise", "version": __version__, "built_at": now_iso(),
            "python": LOCK["python"]["version"], "components": components, "packages": packages(python)}
    (payload / "DEPENDENCIES.md").write_text(inventory_md(info), encoding="utf-8")
    manifest = mf.write(payload, info)
    write_package_files(package)
    log(f"Package written: {package} ({len(manifest['files'])} files)")

    if not args.no_validate:
        env = {**os.environ, "QA_NO_PAUSE": "1"}
        run(["cmd", "/c", str(package / "VALIDATE_OFFLINE.cmd")], env=env)
    if args.zip:
        archive = shutil.make_archive(str(package), "zip", root_dir=args.out, base_dir=package.name)
        log(f"Zip written: {archive} (sha256 {sha256(Path(archive))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
