"""Build the offline portable Windows package.

Run once on a 64-bit Windows PC with Internet (any Python 3.10+):

    py -3 packaging\\build_offline_package.py [--ai-dir D:\\models\\qwen] [--zip]

The result, ``dist_offline\\AIAgenticTestAgent-Offline-<version>\\``, runs on PCs without Internet:

    INSTALL_OFFLINE.cmd      per-user install, no administrator rights
    VALIDATE_OFFLINE.cmd     checks the package on the target PC
    README_OFFLINE.txt
    sbom.cdx.json            CycloneDX SBOM of everything bundled (also in payload)
    payload\\                 the application with runtime\\python, runtime\\jre, runtime\\apache-jmeter,
                             runtime\\ms-playwright, runtime\\speech (offline voice commands),
                             runtime\\ai\\llama.cpp (llama-server for the local LLM; models are added on the PC),
                             MANIFEST.json, DEPENDENCIES.md, sbom.cdx.json

With --zip, the zip is written next to the folder with SHA256SUMS.txt, so the file a security team scans can be
checked again on the target PC.

Every download is pinned in packaging/offline-lock.json and checked against its SHA-256; Python packages are
installed from packaging/requirements-offline-win.txt with --require-hashes. Chromium is fetched by Playwright's
own installer for the pinned Playwright version. Models are never downloaded: --ai-dir copies a folder you provide
(for example a Qwen .gguf) into runtime\\ai.
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
import re
import tarfile
import urllib.request
import uuid
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


def stage_llama_server(payload: Path, cache: Path) -> None:
    """llama.cpp's llama-server (CPU build), found automatically by Managed Local LLM and First-Run Setup."""
    dest = payload / "runtime" / "ai" / "llama.cpp"
    extract_zip(fetch("llama_server", cache), dest)
    servers = list(dest.rglob("llama-server.exe"))
    if not servers:
        raise SystemExit("The pinned llama.cpp archive has no llama-server.exe.")
    # The Microsoft C runtime ships with the bundled Python; a copy beside llama-server means it runs on a PC
    # without the Visual C++ Redistributable installed (which would need administrator rights).
    for dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        src = payload / "runtime" / "python" / dll
        if src.exists() and not (servers[0].parent / dll).exists():
            shutil.copy2(src, servers[0].parent / dll)
    log(f"llama-server staged in {servers[0].parent}")


def stage_chromium(payload: Path, python: Path) -> list[dict]:
    browsers = payload / "runtime" / "ms-playwright"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    log("Downloading Chromium with Playwright's installer")
    run([str(python), "-E", "-s", "-m", "playwright", "install", "chromium"], env=env)
    return [{"name": f"Playwright browser {d.name}", "version": chromium_version(d) or d.name.rsplit("-", 1)[-1],
             "source": "Playwright installer (playwright install chromium)", "license": "BSD-3-Clause (Chromium)"}
            for d in sorted(browsers.iterdir()) if d.is_dir()]


def chromium_version(folder: Path) -> str:
    """Chromium's own version (e.g. 141.0.7390.37) from the <version>.manifest file beside chrome.exe."""
    for m in folder.rglob("*.manifest"):
        if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", m.stem):
            return m.stem
    return ""


def stage_ai(payload: Path, ai_dir: Path | None) -> list[dict]:
    if not ai_dir:
        return []
    models = sorted(ai_dir.rglob("*.gguf"))
    if not models:
        raise SystemExit(f"{ai_dir} contains no .gguf model.")
    shutil.copytree(ai_dir, payload / "runtime" / "ai", dirs_exist_ok=True)
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


def _jar_components(payload: Path) -> list[dict]:
    """Every jar JMeter brings, by file name and SHA-256, so scanners can match them."""
    out = []
    for jar in sorted((payload / "runtime").glob("apache-jmeter-*/lib/**/*.jar")):
        m = re.fullmatch(r"(.+?)-(\d[\w.+-]*)", jar.stem)
        name, version = (m.group(1), m.group(2)) if m else (jar.stem, "")
        out.append({"type": "library", "name": name, "version": version,
                    "hashes": [{"alg": "SHA-256", "content": sha256(jar)}],
                    "properties": [{"name": "path", "value": jar.relative_to(payload).as_posix()}]})
    return out


def sbom(info: dict, payload: Path) -> dict:
    """CycloneDX 1.5 SBOM of the bundled runtimes, browser, server, Python packages and JMeter libraries."""
    def lic(text: str) -> list[dict]:
        return [{"license": {"name": text}}] if text else []

    comps: list[dict] = []
    for c in info["components"]:
        item = {"type": "application", "name": c["name"], "version": c.get("version", ""), "licenses": lic(c.get("license", ""))}
        if c.get("sha256"):
            item["hashes"] = [{"alg": "SHA-256", "content": c["sha256"]}]
        if str(c.get("source", "")).startswith("https://"):
            item["externalReferences"] = [{"type": "distribution", "url": c["source"]}]
        comps.append(item)
    comps += [{"type": "library", "name": p["name"], "version": p["version"], "purl": f"pkg:pypi/{p['name']}@{p['version']}",
               "licenses": lic(p.get("license", ""))} for p in info["packages"]]
    comps += _jar_components(payload)
    for i, c in enumerate(comps):
        c["bom-ref"] = f"c{i}-{c['name']}@{c.get('version', '')}"
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": info["built_at"],
                         "component": {"type": "application", "name": info["name"], "version": info["version"],
                                       "bom-ref": "app"},
                         "tools": {"components": [{"type": "application", "name": "packaging/build_offline_package.py"}]}},
            "components": comps}


def write_checksums(archive: Path) -> Path:
    sums = archive.parent / "SHA256SUMS.txt"
    sums.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
    return sums


def write_package_files(package: Path) -> None:
    shutil.copy2(TEMPLATES / "INSTALL_OFFLINE.cmd", package / "INSTALL_OFFLINE.cmd")
    shutil.copy2(TEMPLATES / "VALIDATE_OFFLINE_PACKAGE.cmd", package / "VALIDATE_OFFLINE.cmd")
    shutil.copy2(TEMPLATES / "README_OFFLINE.txt", package / "README_OFFLINE.txt")
    shutil.copy2(package / "payload" / "sbom.cdx.json", package / "sbom.cdx.json")


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
    stage_llama_server(payload, cache)
    components = [{**{k: LOCK[key][k] for k in ("name", "version", "license", "sha256")},
                   "source": LOCK[key]["url"]} for key in ("python", "jre", "jmeter", "speech_model", "llama_server")]
    components += stage_chromium(payload, python)
    components += stage_ai(payload, args.ai_dir)
    info = {"name": "AI Agentic Test Agent Enterprise", "version": __version__, "built_at": now_iso(),
            "python": LOCK["python"]["version"], "components": components, "packages": packages(python)}
    (payload / "DEPENDENCIES.md").write_text(inventory_md(info), encoding="utf-8")
    (payload / "sbom.cdx.json").write_text(json.dumps(sbom(info, payload), indent=1), encoding="utf-8")
    manifest = mf.write(payload, info)
    write_package_files(package)
    log(f"Package written: {package} ({len(manifest['files'])} files)")

    if not args.no_validate:
        env = {**os.environ, "QA_NO_PAUSE": "1"}
        run(["cmd", "/c", str(package / "VALIDATE_OFFLINE.cmd")], env=env)
    if args.zip:
        archive = Path(shutil.make_archive(str(package), "zip", root_dir=args.out, base_dir=package.name))
        sums = write_checksums(archive)
        log(f"Zip written: {archive} (sha256 {sha256(archive)}); checksum in {sums}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
