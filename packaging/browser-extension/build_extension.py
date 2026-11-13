"""Builds SkyGuard_Adaptive_AI_Browser_Extension_Final.zip: the unpacked MV3 extension, the local companion with the
shared agent core, the offline runtimes (Python 3.13.17, llama-server b10549 CPU, Faster-Whisper wheels, all pinned by
SHA-256 in packaging/offline-lock.json and packaging/requirements-faster-whisper-win.txt), per-user install scripts,
documentation, tests and SHA256_CHECKSUM.txt.

    python packaging/browser-extension/build_extension.py --out <folder> [--cache <download cache>]

Downloads (only at build time, never on the user's PC) are verified before use; a cached file is verified again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NAME = "SkyGuard_Adaptive_AI_Browser_Extension_Final"
LOCK = json.loads((REPO / "packaging" / "offline-lock.json").read_text(encoding="utf-8"))
FW_REQ = REPO / "packaging" / "requirements-faster-whisper-win.txt"
TESTS = ["tests/test_companion_security.py", "tests/test_extension_static.py", "tests/test_extension_e2e.py",
         "tests/transport_site.py", "tests/ops_agent_fixtures.py", "tests/test_ops_agent_core.py", "tests/test_ops_windows.py"]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(key: str, cache: Path) -> Path:
    item = LOCK[key]
    dest = cache / item["url"].rsplit("/", 1)[1]
    if not dest.exists() or sha(dest) != item["sha256"]:
        print(f"downloading {item['url']}", flush=True)
        with urllib.request.urlopen(item["url"], timeout=300) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)
    if sha(dest) != item["sha256"]:
        raise SystemExit(f"{dest.name}: SHA-256 does not match the pinned value; refusing to package it.")
    return dest


def fw_requirements(stage_file: Path) -> None:
    keep, skip = [], False
    for line in FW_REQ.read_text(encoding="utf-8").splitlines():
        if re.match(r"^hf-xet==", line):  # download accelerator, not needed offline
            skip = True
            continue
        if skip and line.startswith((" ", "\t")):
            continue
        skip = False
        keep.append(line)
    stage_file.write_text("\n".join(keep) + "\n", encoding="utf-8")


def wheels(req: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-cache-dir", "--disable-pip-version-check",
                    "--require-hashes", "--only-binary=:all:", "--no-deps", "--platform", "win_amd64",
                    "--python-version", "3.13", "--implementation", "cp", "-d", str(dest), "-r", str(req)], check=True)


def crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=str(Path.home() / ".cache" / "skyguard-extension-build"))
    args = ap.parse_args()
    out, cache = Path(args.out), Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    stage = out / NAME
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "browser-extension" / "extension", stage / "extension", ignore=ignore)
    comp = stage / "companion"
    shutil.copytree(REPO / "browser-extension" / "companion", comp, ignore=ignore)
    shutil.copytree(REPO / "skyguard_agent", comp / "skyguard_agent", ignore=ignore)
    shutil.copy2(REPO / "qa_platform" / "voice" / "whisper_server.py", comp / "whisper_server.py")
    # the shared files must be the same as SkyGuard's
    for a, b in ((REPO / "skyguard_agent" / "js" / "agent_dom.js", stage / "extension" / "lib" / "agent_dom.js"),
                 (REPO / "web" / "js" / "secret-check.js", stage / "extension" / "lib" / "secret-check.js"),
                 (REPO / "web" / "js" / "wav-recorder.js", stage / "extension" / "lib" / "wav-recorder.js")):
        if a.read_bytes() != b.read_bytes():
            raise SystemExit(f"{b} differs from {a}")
    off = stage / "offline"
    off.mkdir()
    for key in ("python", "llama_server"):
        shutil.copy2(fetch(key, cache), off)
    fw_requirements(off / "fw-requirements.txt")
    wheels(off / "fw-requirements.txt", cache / "wheels")
    names = {re.split(r"[=\s]", ln, maxsplit=1)[0].lower().replace("_", "-") for ln in
             (off / "fw-requirements.txt").read_text().splitlines() if "==" in ln}
    (off / "wheels").mkdir()
    for w in sorted((cache / "wheels").glob("*.whl")):
        if w.name.split("-")[0].lower().replace("_", "-") in names:
            shutil.copy2(w, off / "wheels")
    (off / "OFFLINE_MANIFEST.json").write_text(json.dumps({
        "python": {k: LOCK["python"][k] for k in ("version", "url", "sha256", "license")},
        "llama_server": {k: LOCK["llama_server"][k] for k in ("version", "url", "sha256", "license")},
        "faster_whisper_wheels": {w.name: sha(w) for w in sorted((off / "wheels").glob("*.whl"))},
        "models_not_bundled": {"llm_model": LOCK["llm_model"], "fw_model": LOCK["fw_model"]},
    }, indent=1), encoding="utf-8")
    scripts = stage / "scripts"
    shutil.copytree(REPO / "browser-extension" / "scripts", scripts, ignore=ignore)
    for cmd in ("INSTALL.cmd", "UNINSTALL.cmd"):
        shutil.move(str(scripts / cmd), stage / cmd)
    for p in [*scripts.glob("*.ps1"), *scripts.glob("*.cmd"), stage / "INSTALL.cmd", stage / "UNINSTALL.cmd"]:
        crlf(p)
    shutil.copytree(REPO / "browser-extension" / "docs", stage / "docs", ignore=ignore)
    shutil.copy2(REPO / "browser-extension" / "README.txt", stage / "README.txt")
    for rel in TESTS:
        if (REPO / rel).is_file():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, stage / rel)
    lines = [f"{sha(p)}  {p.relative_to(stage).as_posix()}" for p in sorted(stage.rglob("*"))
             if p.is_file() and p.name != "SHA256_CHECKSUM.txt"]
    (stage / "SHA256_CHECKSUM.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zpath = out / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, f"{NAME}/{p.relative_to(stage).as_posix()}")
    (out / f"{NAME}.zip.sha256").write_text(f"{sha(zpath)}  {zpath.name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(zpath), "sha256": sha(zpath), "files": len(lines),
                      "size_mb": round(zpath.stat().st_size / 1048576, 2)}))


if __name__ == "__main__":
    main()
