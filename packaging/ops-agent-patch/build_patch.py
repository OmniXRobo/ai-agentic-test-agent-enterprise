"""Builds SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch.zip: every application file changed or
added since the 23.4.0 baseline commit (the merge of PR #42), the installer and its Windows wrappers, the release
documents, the Operations Agent tests, and SHA256_CHECKSUM.txt.

    python packaging/ops-agent-patch/build_patch.py --out <folder> [--baseline 53e8009]
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
NAME = "SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch"
BASELINE_COMMIT = "53e80093fe99272f27dc424392d1fd3d746b3c7c"  # SkyGuard QA 23.4.0 (PR #41 + PR #42)
PREFIXES = ("qa_platform/", "web/", "skyguard_agent/")
DOCS = REPO / "docs" / "ops-agent"
TOOLS = ["APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py"]
TESTS = ["tests/transport_site.py", "tests/ops_agent_fixtures.py", "tests/test_ops_agent_api.py",
         "tests/test_ops_agent_e2e.py", "tests/test_ops_agent_core.py", "tests/test_regression_pages.py",
         "tests/test_ops_patch_install.py"]


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
    manifest = {"version": "23.5.0", "baseline": "23.4.0", "baseline_commit": args.baseline,
                "files": {rel: sha(stage / "app" / rel) for rel in files}}
    (stage / "PATCH_MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for t in TOOLS:
        shutil.copy2(HERE / t, stage / t)
        if t.endswith((".cmd", ".ps1")):  # Windows line endings for cmd.exe and PowerShell 5.1
            crlf(stage / t)
    for d in sorted(DOCS.glob("*.md")):
        shutil.copy2(d, stage / d.name)
    if (DOCS / "evidence").is_dir():
        shutil.copytree(DOCS / "evidence", stage / "evidence")
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
