"""Build the all-in-one offline Windows package (SkyGuard QA, 23.3.32).

Run once on a 64-bit Windows PC with Internet (any Python 3.10+):

    py -3 packaging\\build_offline_package.py [--zip] [--no-models]

The result, ``dist_offline\\SGQA\\`` (and with --zip ``dist_offline\\SGQA-<version>.zip`` + SHA256SUMS.txt), installs
and runs on a PC without Internet, without administrator rights and without patches:

    INSTALL.cmd              per-user install with SkyGuard QA shortcuts (robot icon, no console window)
    VALIDATE.cmd             checks the package on the target PC with the network blocked
    README.txt, SECURITY.txt, sbom.cdx.json
    payload\\                 the application with every runtime:
        runtime\\python                Python 3.13 with every package (pip removed) + Microsoft C++ runtime files
        runtime\\ms-playwright         Chromium (Firefox and WebKit are not included)
        runtime\\jre, apache-jmeter-*  Java 17 and JMeter 5.6.3
        runtime\\ai                    llama-server + models\\qwen2.5-1.5b-instruct-q4_k_m.gguf
        runtime\\faster-whisper        Faster-Whisper packages (lib) + models\\small.en
        runtime\\speech                Vosk small English (backup recognizer)
        MANIFEST.json, DEPENDENCIES.md, sbom.cdx.json

Every download is pinned in packaging/offline-lock.json and checked against its SHA-256 (models: against the checksum
Hugging Face publishes for the revision). Python packages are installed with --require-hashes. Chromium comes from
Playwright's own installer for the pinned Playwright version. The build fails if any path in the package is too long
to extract with Windows Explorer.
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
FW_REQUIREMENTS = REPO / "packaging" / "requirements-faster-whisper-win.txt"
PACKAGE_DIR = "SGQA"  # short: Explorer's Extract All fails on paths over 260 characters
# Longest path allowed inside the package folder, so "C:\\Users\\<name>\\Downloads\\SGQA-<version>\\SGQA\\" (about
# 60-70 characters) plus this stays under Windows' 260-character limit.
MAX_RELATIVE_PATH = 185
# Microsoft Visual C++ runtime (app-local copy, allowed by the Visual Studio redistribution terms): llama-server,
# CTranslate2, ONNX Runtime and Vosk load on a PC without the Visual C++ Redistributable, without administrator rights.
VC_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
           "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll", "concrt140.dll")
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
    return python


def remove_pip(python: Path) -> None:
    """pip is not needed at run time and is removed, so nothing can be installed on the offline PC later."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and not k.startswith("PIP_")}
    run([str(python), "-E", "-s", "-m", "pip", "uninstall", "-y", "pip"], env=env)


def stage_faster_whisper(payload: Path, python: Path) -> list[dict]:
    """Faster-Whisper and its packages (hash-checked) in runtime\\faster-whisper\\lib, where the application's speech
    server loads them. hf-xet (a download accelerator) is left out; --no-deps because every package is listed."""
    lib = payload / "runtime" / "faster-whisper" / "lib"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and not k.startswith("PIP_")}
    reqs = payload.parent / "fw-requirements.txt"
    keep, skip = [], False
    for line in FW_REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if re.match(r"^hf-xet==", line):
            skip = True
            continue
        if skip and line.startswith((" ", "\t")):
            continue
        skip = False
        keep.append(line)
    reqs.write_text("\n".join(keep) + "\n", encoding="utf-8")
    log("Installing Faster-Whisper packages (hash-checked)")
    run([str(python), "-E", "-s", "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
         "--require-hashes", "--only-binary=:all:", "--no-deps", "--no-compile", "--target", str(lib),
         "--no-warn-script-location", "-r", str(reqs)], env=env)
    reqs.unlink()
    for scripts in (lib / "bin", lib / "Scripts"):
        shutil.rmtree(scripts, ignore_errors=True)  # command-line tools are not used
    code = ("import json,sys,importlib.metadata as m;sys.path.insert(0,sys.argv[1]);"
            "print(json.dumps([{'name':d.metadata['Name'].lower(),'version':d.version} for d in "
            "m.distributions(path=[sys.argv[1]])]))")
    out = subprocess.run([str(python), "-E", "-s", "-c", code, str(lib)], capture_output=True, text=True, check=True)
    return [{"name": p["name"], "version": p["version"], "license": "", "where": "runtime/faster-whisper/lib"}
            for p in json.loads(out.stdout)]


def _git_blob_sha1(path: Path) -> str:
    h = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def _json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_hf(key: str, cache: Path) -> tuple[dict[str, Path], dict]:
    """The files of a Hugging Face model at the pinned revision (or the current one), each checked against the
    checksum Hugging Face publishes for it, and against the SHA-256 pinned in offline-lock.json when there is one."""
    item = LOCK[key]
    repo, revision = item["repo"], item.get("revision") or ""
    if not revision:
        revision = _json(f"https://huggingface.co/api/models/{repo}")["sha"]
        log(f"{item['name']}: no revision pinned; using the current one, {revision}")
    tree = {e["path"]: e for e in _json(f"https://huggingface.co/api/models/{repo}/tree/{revision}")}
    folder = cache / key / revision[:12]
    folder.mkdir(parents=True, exist_ok=True)
    out, pinned, record = {}, item.get("sha256") or {}, {}
    for name in item["files"]:
        entry = tree.get(name)
        if entry is None:
            raise SystemExit(f"{repo}@{revision} has no file {name}.")
        target = folder / name
        lfs = (entry.get("lfs") or {}).get("oid") or (entry.get("lfs") or {}).get("sha256")

        def ok() -> bool:
            if not target.exists():
                return False
            if lfs:
                return sha256(target) == lfs
            return _git_blob_sha1(target) == entry.get("oid")

        if not ok():
            log(f"Downloading {repo}/{name} ({int(entry.get('size') or 0) / 1048576:,.0f} MB)")
            partial = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/{revision}/{name}", timeout=300) as resp, \
                    partial.open("wb") as f:
                shutil.copyfileobj(resp, f, 1 << 20)
            partial.replace(target)
            if not ok():
                target.unlink()
                raise SystemExit(f"Checksum mismatch for {repo}/{name} at {revision}. Not used.")
        digest = sha256(target)
        if pinned.get(name) and pinned[name] != digest:
            raise SystemExit(f"{repo}/{name} differs from the SHA-256 pinned in offline-lock.json. Not used.")
        record[name] = digest
        out[name] = target
        log(f"Verified {repo}/{name} (sha256 {digest[:16]}…)")
    if not item.get("revision"):
        log(f"To pin {key} in offline-lock.json: revision {revision}, sha256 {json.dumps(record)}")
    return out, {"repo": repo, "revision": revision, "sha256": record}


def stage_models(payload: Path, cache: Path) -> list[dict]:
    """The On-Premise AI model and the Faster-Whisper speech model, ready to use without First-Run Setup choices."""
    comps = []
    for key, dest in (("llm_model", payload / "runtime" / "ai" / "models"),
                      ("fw_model", payload / "runtime" / "faster-whisper" / "models" / LOCK["fw_model"]["version"])):
        files, meta = fetch_hf(key, cache)
        dest.mkdir(parents=True, exist_ok=True)
        for name, src in files.items():
            shutil.copy2(src, dest / name)
        item = LOCK[key]
        for name, digest in meta["sha256"].items():
            comps.append({"name": f"{item['name']}: {name}", "version": f"{item['version']} @ {meta['revision'][:12]}",
                          "license": item["license"], "sha256": digest,
                          "source": f"https://huggingface.co/{meta['repo']}/resolve/{meta['revision']}/{name}"})
        log(f"{item['name']} staged in {dest}")
    return comps


def stage_vc_runtime(payload: Path) -> list[dict]:
    """App-local Microsoft C++ runtime beside python.exe (CTranslate2, ONNX Runtime, Vosk load through it) and beside
    llama-server.exe, taken from this Windows build PC."""
    system = Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32"
    targets = [payload / "runtime" / "python"] + [p.parent for p in (payload / "runtime" / "ai").rglob("llama-server.exe")]
    comps = []
    for dll in VC_DLLS:
        src = system / dll
        if not src.is_file():
            if dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
                raise SystemExit(f"{src} is missing on the build PC: install the Visual C++ Redistributable there.")
            continue
        for folder in targets:
            if not (folder / dll).exists():
                shutil.copy2(src, folder / dll)
        comps.append({"name": f"Microsoft Visual C++ runtime {dll}", "version": _file_version(src),
                      "license": "Microsoft Visual C++ Redistributable (app-local distribution)", "sha256": sha256(src),
                      "source": "Visual C++ 2015-2022 Redistributable (x64) on the build PC"})
    log(f"Microsoft C++ runtime files copied beside {', '.join(t.name for t in targets)}")
    return comps


def _file_version(path: Path) -> str:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Item '{path}').VersionInfo.FileVersion"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_path_lengths(package: Path) -> None:
    """Refuse a package whose paths are too long to extract on Windows."""
    longest = max((p for p in package.rglob("*")), key=lambda p: len(str(p.relative_to(package.parent))))
    rel = str(longest.relative_to(package.parent))
    log(f"Longest path in the zip: {len(rel)} characters ({rel})")
    if len(rel) > MAX_RELATIVE_PATH:
        raise SystemExit(f"{rel} is {len(rel)} characters, over {MAX_RELATIVE_PATH}: Windows could not extract it.")


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
    lines = [f"# Dependencies of SkyGuard QA {info['version']} (offline package)", "",
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
    """The files beside the payload. Short names: INSTALL.cmd, VALIDATE.cmd, README.txt, SECURITY.txt."""
    shutil.copy2(TEMPLATES / "INSTALL_OFFLINE.cmd", package / "INSTALL.cmd")
    shutil.copy2(TEMPLATES / "VALIDATE_OFFLINE_PACKAGE.cmd", package / "VALIDATE.cmd")
    shutil.copy2(TEMPLATES / "README_OFFLINE.txt", package / "README.txt")
    shutil.copy2(TEMPLATES / "SECURITY.txt", package / "SECURITY.txt")
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
    ap.add_argument("--no-models", action="store_true", help="leave out the AI and Faster-Whisper models (quick test builds)")
    args = ap.parse_args(argv)
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("Build the offline package on 64-bit Windows: it bundles Windows Python, Java and Chromium.")

    package = args.out / PACKAGE_DIR
    payload = package / "payload"
    if package.exists():
        log(f"Removing the previous build {package}")
        shutil.rmtree(package)
    payload.mkdir(parents=True)
    cache = args.cache or args.out / "downloads"

    stage_app(payload)
    python = stage_python(payload, cache)
    fw_packages = stage_faster_whisper(payload, python)
    remove_pip(python)
    stage_java_and_jmeter(payload, cache)
    stage_speech(payload, cache)
    stage_llama_server(payload, cache)
    components = [{**{k: LOCK[key][k] for k in ("name", "version", "license", "sha256")},
                   "source": LOCK[key]["url"]} for key in ("python", "jre", "jmeter", "speech_model", "llama_server")]
    components += stage_vc_runtime(payload)
    components += stage_chromium(payload, python)
    if not args.no_models:
        components += stage_models(payload, cache)
    components += stage_ai(payload, args.ai_dir)
    info = {"name": "SkyGuard QA", "version": __version__, "built_at": now_iso(),
            "python": LOCK["python"]["version"], "components": components,
            "packages": packages(python) + fw_packages, "faster_whisper": not args.no_models}
    (payload / "DEPENDENCIES.md").write_text(inventory_md(info), encoding="utf-8")
    (payload / "sbom.cdx.json").write_text(json.dumps(sbom(info, payload), indent=1), encoding="utf-8")
    manifest = mf.write(payload, info)
    write_package_files(package)
    check_path_lengths(package)
    log(f"Package written: {package} ({len(manifest['files'])} files)")

    if not args.no_validate:
        env = {**os.environ, "QA_NO_PAUSE": "1"}
        run(["cmd", "/c", str(package / "VALIDATE.cmd")], env=env)
    if args.zip:
        archive = Path(shutil.make_archive(str(args.out / f"{PACKAGE_DIR}-{__version__}"), "zip", root_dir=args.out,
                                           base_dir=package.name))
        sums = write_checksums(archive)
        log(f"Zip written: {archive} ({archive.stat().st_size / 1e9:.2f} GB, sha256 {sha256(archive)}); "
            f"checksum in {sums}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
