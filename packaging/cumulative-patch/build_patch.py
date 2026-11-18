"""Builds the SkyGuard QA 23.11.2 cumulative patch folder and zip (23.3.35 to 23.11.2: every release in between):
every application file changed or added since the 23.3.35 baseline commit (the merge of PR #39), the offline OCR
runtime wheels (RapidOCR, ONNX Runtime, OpenCV, NumPy, Pillow and their dependencies for 64-bit Windows and Python 3.13,
listed with SHA-256 in ocr-runtime/WHEELS.json), the installer and its Windows wrappers, the browser extension update,
the release documents (earlier notes under previous-*), the regression and acceptance tests with their fixture sites,
and SHA256_CHECKSUM.txt. Writes the zip and <zip>.sha256. packaging/setup/build_release.py wraps this folder into
Setup.exe and the release zip.

    python packaging/cumulative-patch/build_patch.py --out <folder> --ocr-wheels <folder of .whl> [--baseline 6d448fa]

The wheels are downloaded once on a connected build machine with
    pip download --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp -d <folder> rapidocr==3.10.0 onnxruntime
and carried in the patch: the installation itself never downloads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
NAME = "SkyGuard_SGQA-23.3.35_to_23.11.2_Patch_Files"
VERSION = "23.11.2"
BASELINE_COMMIT = "6d448fa"  # SkyGuard QA 23.3.35 (merge of PR #39)
PREFIXES = ("qa_platform/", "web/", "skyguard_agent/")
DOCS = REPO / "docs" / "release-23.11.2"
PREVIOUS_DOCS = {"previous-23.11.1": REPO / "docs" / "release-23.11.1", "previous-23.11.0": REPO / "docs" / "release-23.11.0", "previous-23.10.0": REPO / "docs" / "release-23.10.0", "previous-23.9.0": REPO / "docs" / "integration-23.9.0", "previous-23.8.0": REPO / "docs" / "integration-23.8.0", "previous-23.7.2": REPO / "docs" / "automation-23.7.2", "previous-23.7.1": REPO / "docs" / "playback-23.7.1", "previous-23.7.0": REPO / "docs" / "autolearn-23.7.0",
                 "previous-23.6.1": REPO / "docs" / "adaptive-23.6.1"}
TOOLS = ["APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py",
         "UPDATE_EXTENSION.cmd", "UPDATE_EXTENSION.ps1", "DIAGNOSE_SITE.cmd", "DIAGNOSE_SITE.ps1",
         "VERIFY_LIVE_SITE.cmd", "VERIFY_LIVE_SITE.ps1", "../setup/stop_skyguard.ps1"]
TESTS = ["tests/test_passive_learning_23700.py", "tests/passive_session.py", "tests/transport_site.py",
         "tests/test_autolearn_patch_23700.py", "tests/test_ops_agent_e2e.py", "tests/test_extension_static.py",
         "tests/test_companion_security.py", "tests/test_adaptive_components_23600.py",
         "tests/test_ops_agent_choices_23600.py", "tests/test_healing_stable_23600.py",
         "tests/test_tripcom_playback_23610.py", "tests/test_integration_workspace_23800.py",
         "tests/site/integration/style.css", "tests/site/integration/app_a.html", "tests/site/integration/app_b.html",
         "tests/site/integration/app_c.html", "tests/test_site_diagnostic_23710.py", "tests/test_live_check_23710.py",
         "tests/test_ai_automation_steps_23720.py", "tests/site/saucedemo/app.js", "tests/site/saucedemo/index.html",
         "tests/site/saucedemo/inventory.html", "tests/site/saucedemo/cart.html",
         "tests/site/saucedemo/checkout-step-one.html", "tests/site/saucedemo/checkout-step-two.html",
         "tests/site/saucedemo/checkout-complete.html",
         "tests/site/tripcom/index.html",
         "tests/site/tripcom/trip.js", "tests/site/tripcom/trip.css", "tests/site/tripcom/flights.html",
         "tests/site/tripcom/flights.js", "tests/site/components/kit.js", "tests/site/components/travel.html",
         "tests/site/components/widgets.html",
         "tests/test_integration_ocr_browser_23900.py", "tests/test_integration_ai_browser_23900.py",
         "tests/test_cumulative_patch_23900.py", "tests/conftest.py", "tests/site_server.py", "tests/auth_helper.py",
         "tests/test_autopilot_231000.py", "tests/test_agent_simple_api_231000.py", "tests/test_guided_comparison_231000.py",
         "tests/report_site.py", "tests/test_extension_120.py", "tests/test_cumulative_patch_231000.py",
         "tests/test_setup_wizard_231000.py", "tests/test_auth_23334.py", "tests/ops_agent_fixtures.py",
         "tests/test_cumulative_patch_231100.py", "tests/test_smart_wait_231100.py", "tests/ajax_site.py",
         "tests/test_python_editor_231100.py", "tests/test_comparison_tfs_231100.py", "tests/test_ui_231100.py",
         "tests/test_defects.py", "tests/fake_ado.py", "tests/test_ai_agent_repair_231101.py",
         "tests/shop_site.py", "tests/transport_flow_site.py", "tests/test_extension_130.py", "tests/test_ui_231101.py",
         "tests/test_cumulative_patch_231101.py",
         "tests/test_missing_info_231102.py", "tests/test_ui_231102.py", "tests/test_cumulative_patch_231102.py"]
TEST_SITES = ["tests/site/integration_ocr", "tests/site/integration_live"]  # whole folders (fixtures of 23.9.0)


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=BASELINE_COMMIT)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ocr-wheels", required=True, help="folder with the Windows / Python 3.13 OCR wheels")
    args = ap.parse_args()
    changed = set(git("diff", "--name-only", "--diff-filter=AMR", args.baseline)) | set(
        git("ls-files", "--others", "--exclude-standard"))
    files = sorted(f for f in changed if f.startswith(PREFIXES) and "__pycache__" not in f and (REPO / f).is_file())
    stage = Path(args.out) / NAME
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "app").mkdir(parents=True)
    for rel in files:
        dst = stage / "app" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst)
    ext = stage / "browser-extension-update"
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(REPO / "browser-extension" / "extension", ext / "extension", ignore=ignore)
    shutil.copytree(REPO / "browser-extension" / "companion", ext / "app", ignore=ignore)
    shutil.copytree(REPO / "skyguard_agent", ext / "app" / "skyguard_agent", ignore=ignore)
    shutil.copy2(REPO / "qa_platform" / "voice" / "whisper_server.py", ext / "app" / "whisper_server.py")
    for a, b in ((REPO / "skyguard_agent" / "js" / "agent_dom.js", ext / "extension" / "lib" / "agent_dom.js"),
                 (REPO / "web" / "js" / "secret-check.js", ext / "extension" / "lib" / "secret-check.js"),
                 (REPO / "web" / "js" / "wav-recorder.js", ext / "extension" / "lib" / "wav-recorder.js")):
        if a.read_bytes() != b.read_bytes():  # the extension must use the same DOM reader as SkyGuard
            raise SystemExit(f"{b} differs from {a}")
    wheels = sorted(Path(args.ocr_wheels).glob("*.whl"))
    names = {w.name.split("-")[0].lower().replace("_", "-") for w in wheels}
    need = {"rapidocr", "onnxruntime", "opencv-python-headless", "numpy", "pillow", "msvc-runtime"}
    if not need <= names or any("win_amd64" not in w.name and "none-any" not in w.name for w in wheels):
        raise SystemExit(f"--ocr-wheels must hold the win_amd64 / py3-none-any wheels of {sorted(need)}; "
                         f"missing {sorted(need - names)}")
    (stage / "ocr-runtime" / "wheels").mkdir(parents=True)
    for w in wheels:
        shutil.copy2(w, stage / "ocr-runtime" / "wheels" / w.name)
    (stage / "ocr-runtime" / "WHEELS.json").write_text(json.dumps({w.name: sha(w) for w in wheels}, indent=1),
                                                     encoding="utf-8")
    manifest = {"version": VERSION, "baseline": ["23.3.35", "23.4.0", "23.5.0", "23.6.0", "23.6.1", "23.7.0", "23.7.1",
                                              "23.7.2", "23.8.0", "23.9.0", "23.10.0", "23.11.0", "23.11.1"], "ocr_wheels": len(wheels),
                "extension_version": json.loads((ext / "extension" / "manifest.json").read_text())["version"], "baseline_commit": args.baseline,
                "files": {rel: sha(stage / "app" / rel) for rel in files}}
    (stage / "PATCH_MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for t in TOOLS:
        name = Path(t).name
        shutil.copy2(HERE / t, stage / name)
        if name.endswith((".cmd", ".ps1")):  # Windows line endings for cmd.exe and PowerShell 5.1
            crlf(stage / name)
    for d in sorted(DOCS.glob("*.md")):
        shutil.copy2(d, stage / d.name)
    if (DOCS / "evidence").is_dir():
        shutil.copytree(DOCS / "evidence", stage / "evidence")
    for folder, src in PREVIOUS_DOCS.items():
        for d in sorted(src.glob("*.md")):
            (stage / folder).mkdir(exist_ok=True)
            shutil.copy2(d, stage / folder / d.name)
    for folder in TEST_SITES:
        shutil.copytree(REPO / folder, stage / folder, ignore=ignore)
    for rel in TESTS:
        if (REPO / rel).is_file():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, stage / rel)
    lines = [f"{sha(p)}  {p.relative_to(stage).as_posix()}" for p in sorted(stage.rglob("*"))
             if p.is_file() and p.name != "SHA256_CHECKSUM.txt"]
    (stage / "SHA256_CHECKSUM.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zpath = Path(args.out) / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, f"{NAME}/{p.relative_to(stage).as_posix()}")
    (Path(args.out) / f"{NAME}.zip.sha256").write_text(f"{sha(zpath)}  {zpath.name}\n", encoding="utf-8")
    print(json.dumps({"zip": str(zpath), "sha256": sha(zpath), "app_files": len(files),
                      "size_mb": round(zpath.stat().st_size / 1048576, 2)}))


if __name__ == "__main__":
    main()
