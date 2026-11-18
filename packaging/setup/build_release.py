"""Builds the two release zips of SkyGuard QA 23.12.0 (run on a build machine with internet; installing needs none):

    SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch.zip
        SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe   the wizard (Welcome, Detect, Backup, Install, Verify,
                                                            Finish); per user, no administrator rights, offline
        README_FIRST.txt, RELEASE_INCLUSION_MANIFEST.md, INSTALLATION_GUIDE.md, SECURITY_REPORT.md, sbom.cdx.json,
        TEST_REPORT.md + test-results/, KNOWN_LIMITATIONS.md, the other release documents, SHA256SUMS.txt
        Manual_Install/                                     the same upgrade without the .exe (APPLY_PATCH.cmd), for
                                                            PCs where a downloaded .exe may not be started
    SkyGuard_Offline_Edge_Chrome_Agent_Extension.zip
        the MV3 extension 1.3.1 with its local companion and offline runtimes (new installs: INSTALL.cmd) and the
        update for an installed companion (UPDATE_EXTENSION.cmd)

    python packaging/setup/build_release.py --out <folder> --ocr-wheels <folder> [--test-results <folder>]
                                            [--extension-cache <folder>] [--skip-extension]
                                            [--patch-name <name>] [--extension-name <name>]

--patch-name and --extension-name rename the two zips (and their top folders), for example to the names a release
request asks for: SkyGuard_SGQA-23.10.0_AI_Agent_Final_Patch and SkyGuard_SGQA-23.10.0_Edge_Chrome_Extension.

makensis (NSIS 3.x) must be on PATH. Every downloaded file is checked against its pinned SHA-256 by the builders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import os
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VERSION = "23.12.0"
EXT_VERSION = "1.4.0"
PATCH_NAME = "SkyGuard_SGQA-23.3.35_to_Latest_Cumulative_Patch"
SETUP_NAME = "SkyGuard_SGQA-23.3.35_to_Latest_Offline_Setup.exe"
EXT_NAME = "SkyGuard_Offline_Edge_Chrome_Agent_Extension"
STAGE_NAME = "SkyGuard_SGQA-23.3.35_to_23.12.0_Patch_Files"  # build_patch.py
DOCS = REPO / "docs" / "release-23.12.0"
TOP_DOCS = ("RELEASE_INCLUSION_MANIFEST.md", "INSTALLATION_GUIDE.md", "SECURITY_REPORT.md", "TEST_REPORT.md",
            "KNOWN_LIMITATIONS.md", "FINAL_RELEASE_REPORT.md", "SOURCE_ARCHITECTURE_AUDIT.md", "PATCH_CHANGELOG.md",
            "USER_GUIDE.md", "ROOT_CAUSE_ANALYSIS.md")
SETUP_PAYLOAD = ("install_patch.py", "PATCH_MANIFEST.json", "stop_skyguard.ps1", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd",
                 "VERIFY_PATCH.cmd")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def zip_folder(folder: Path, zpath: Path) -> None:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, f"{folder.name}/{p.relative_to(folder).as_posix()}")
    zpath.with_name(zpath.name + ".sha256").write_text(f"{sha(zpath)}  {zpath.name}\n", encoding="utf-8")


def sums(folder: Path, name: str = "SHA256SUMS.txt") -> None:
    lines = [f"{sha(p)}  {p.relative_to(folder).as_posix()}" for p in sorted(folder.rglob("*"))
             if p.is_file() and p.name != name]
    (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def wheel_component(w: Path) -> dict:
    name, version = w.name.split("-")[:2]
    return {"type": "library", "name": name.replace("_", "-").lower(), "version": version,
            "purl": f"pkg:pypi/{name.replace('_', '-').lower()}@{version}",
            "hashes": [{"alg": "SHA-256", "content": sha(w)}], "properties": [{"name": "skyguard:file", "value": w.name}]}


def sbom(stage: Path, setup: Path, ext_manifest: dict | None, ext_name: str = EXT_NAME) -> dict:
    """CycloneDX 1.5: what the cumulative patch ships (application, OCR runtime wheels, extension update) and the
    build tool of Setup.exe. The full offline package (Python, Chromium, JMeter, AI runtimes) has its own SBOM."""
    comps = [{"type": "application", "name": "SkyGuard QA", "version": VERSION,
              "hashes": [{"alg": "SHA-256", "content": sha(stage / "PATCH_MANIFEST.json")}],
              "properties": [{"name": "skyguard:manifest", "value": "PATCH_MANIFEST.json (SHA-256 of every file)"}]},
             {"type": "application", "name": "SkyGuard QA Setup", "version": VERSION,
              "hashes": [{"alg": "SHA-256", "content": sha(setup)}],
              "properties": [{"name": "skyguard:built-with", "value": _nsis_version()}]},
             {"type": "application", "name": "SkyGuard Adaptive AI Browser Extension", "version": EXT_VERSION},
             {"type": "application", "name": "SkyGuard Adaptive AI Companion", "version": EXT_VERSION}]
    comps += [wheel_component(w) for w in sorted((stage / "ocr-runtime" / "wheels").glob("*.whl"))]
    if ext_manifest:
        for key in ("python", "llama_server"):
            item = ext_manifest.get(key) or {}
            comps.append({"type": "application", "name": key.replace("_", "-"), "version": item.get("version"),
                          "hashes": [{"alg": "SHA-256", "content": item.get("sha256")}],
                          "licenses": [{"license": {"name": item.get("license") or "see vendor"}}],
                          "properties": [{"name": "skyguard:in", "value": ext_name}]})
        for name, digest in (ext_manifest.get("faster_whisper_wheels") or {}).items():
            n, v = name.split("-")[:2]
            comps.append({"type": "library", "name": n.replace("_", "-").lower(), "version": v,
                          "purl": f"pkg:pypi/{n.replace('_', '-').lower()}@{v}",
                          "hashes": [{"alg": "SHA-256", "content": digest}],
                          "properties": [{"name": "skyguard:in", "value": ext_name}]})
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "component": {"type": "application", "name": "SkyGuard QA cumulative upgrade",
                                       "version": VERSION}},
            "components": comps}


def _nsis_version() -> str:
    try:
        return "NSIS " + subprocess.run(["makensis", "-VERSION"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "NSIS"


def build_setup(stage: Path, work: Path, out_exe: Path) -> None:
    payload = work / "setup-payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    shutil.copytree(stage / "app", payload / "app")
    shutil.copytree(stage / "ocr-runtime", payload / "ocr-runtime")
    for f in SETUP_PAYLOAD:
        shutil.copy2(stage / f, payload / f)
    run(["makensis", "-V2", "-NOCD", f"-DVERSION={VERSION}", f"-DPAYLOAD={payload}", f"-DOUTFILE={out_exe}",
         str(HERE / "SkyGuardSetup.nsi")], cwd=str(HERE))
    sign_setup(out_exe)


def sign_setup(exe: Path) -> bool:
    """Authenticode-sign Setup.exe when a code-signing certificate is supplied (optional).

    Windows SmartScreen shows "Unknown publisher" for any unsigned program; the publisher name in the file's
    properties does not change that. Only a signature from a code-signing certificate issued to the publisher does.
    The certificate is read from SKYGUARD_SIGN_PFX (a .pfx file) and its password from SKYGUARD_SIGN_PASSWORD; the
    password is passed to osslsigncode through a file that is deleted afterwards and is never printed or logged.
    """
    pfx, password = os.environ.get("SKYGUARD_SIGN_PFX"), os.environ.get("SKYGUARD_SIGN_PASSWORD", "")
    if not pfx:
        print("Setup.exe is not signed (no SKYGUARD_SIGN_PFX): Windows will show 'Unknown publisher'.")
        return False
    if not shutil.which("osslsigncode"):
        raise SystemExit("SKYGUARD_SIGN_PFX is set but osslsigncode is not installed")
    signed = exe.with_suffix(".signed.exe")
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as pw:
        pw.write(password)
    try:
        cmd = ["osslsigncode", "sign", "-pkcs12", pfx, "-readpass", pw.name, "-n", "SkyGuard QA " + VERSION + " Setup",
               "-h", "sha256", "-in", str(exe), "-out", str(signed)]
        ts = os.environ.get("SKYGUARD_SIGN_TIMESTAMP_URL")
        if ts:
            cmd[2:2] = ["-ts", ts]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            raise SystemExit("signing Setup.exe failed (check the certificate and its password)")
    finally:
        os.unlink(pw.name)
    signed.replace(exe)
    print("Setup.exe signed with", Path(pfx).name)
    return True


def build_extension(out: Path, cache: str | None, name: str = EXT_NAME) -> tuple[Path, dict]:
    """The full extension package (fresh installs) plus the update of an installed companion."""
    work = out / "ext-work"
    cmd = [sys.executable, str(REPO / "packaging" / "browser-extension" / "build_extension.py"), "--out", str(work)]
    if cache:
        cmd += ["--cache", cache]
    run(cmd)
    built = work / "SkyGuard_Adaptive_AI_Browser_Extension_Final"
    folder = out / name
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(built, folder)
    upd = folder / "browser-extension-update"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "browser-extension" / "extension", upd / "extension", ignore=ignore)
    shutil.copytree(REPO / "browser-extension" / "companion", upd / "app", ignore=ignore)
    shutil.copytree(REPO / "skyguard_agent", upd / "app" / "skyguard_agent", ignore=ignore)
    shutil.copy2(REPO / "qa_platform" / "voice" / "whisper_server.py", upd / "app" / "whisper_server.py")
    for t in ("UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1"):
        shutil.copy2(REPO / "packaging" / "cumulative-patch" / t, folder / t)
        p = folder / t
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    shutil.copy2(HERE / "README_FIRST_EXTENSION.txt", folder / "README_FIRST.txt")
    # UPDATE_EXTENSION.ps1 checks the update files against SHA256_CHECKSUM.txt: rewrite it over the whole folder
    (folder / "SHA256_CHECKSUM.txt").unlink(missing_ok=True)
    sums(folder, "SHA256_CHECKSUM.txt")
    manifest = json.loads((folder / "offline" / "OFFLINE_MANIFEST.json").read_text(encoding="utf-8"))
    if json.loads((folder / "extension" / "manifest.json").read_text())["version"] != EXT_VERSION:
        raise SystemExit("the extension in the package is not " + EXT_VERSION)
    zpath = out / f"{name}.zip"
    zip_folder(folder, zpath)
    return zpath, manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--ocr-wheels", required=True)
    ap.add_argument("--test-results", help="folder with junit XML and logs of the test runs, copied to test-results/")
    ap.add_argument("--extension-cache")
    ap.add_argument("--skip-extension", action="store_true")
    ap.add_argument("--patch-name", default=PATCH_NAME)
    ap.add_argument("--extension-name", default=EXT_NAME)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    run([sys.executable, str(REPO / "packaging" / "cumulative-patch" / "build_patch.py"), "--out", str(work),
         "--ocr-wheels", args.ocr_wheels])
    stage = work / STAGE_NAME
    ext_zip, ext_manifest = (None, None) if args.skip_extension else build_extension(out, args.extension_cache,
                                                                                        args.extension_name)

    rel = out / args.patch_name
    if rel.exists():
        shutil.rmtree(rel)
    rel.mkdir()
    setup = rel / SETUP_NAME
    build_setup(stage, work, setup)
    shutil.copy2(HERE / "README_FIRST.txt", rel / "README_FIRST.txt")
    for d in TOP_DOCS:
        shutil.copy2(DOCS / d, rel / d)
    if (DOCS / "evidence").is_dir():
        shutil.copytree(DOCS / "evidence", rel / "evidence")
    if args.test_results:
        shutil.copytree(args.test_results, rel / "test-results")
    shutil.copytree(stage, rel / "Manual_Install")
    (rel / "sbom.cdx.json").write_text(json.dumps(sbom(stage, setup, ext_manifest, args.extension_name), indent=1), encoding="utf-8")
    for p in (rel / "README_FIRST.txt",):
        p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    sums(rel)
    zpath = out / f"{args.patch_name}.zip"
    zip_folder(rel, zpath)
    result = {"patch_zip": str(zpath), "patch_sha256": sha(zpath), "setup_sha256": sha(setup),
              "size_mb": round(zpath.stat().st_size / 1048576, 1)}
    if ext_zip:
        result.update(extension_zip=str(ext_zip), extension_sha256=sha(ext_zip),
                      extension_size_mb=round(ext_zip.stat().st_size / 1048576, 1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
