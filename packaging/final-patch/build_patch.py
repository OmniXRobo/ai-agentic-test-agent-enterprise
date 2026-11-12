"""Builds QA_Enterprise_AI_Final_Upgrade_Patch.zip: every application file changed or added since the 23.3.35
baseline commit, the installer, the Windows wrappers, the release documents and SHA256_CHECKSUM.txt.

    python packaging/final-patch/build_patch.py --baseline <23.3.35 commit> --out <folder>
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
NAME = "QA_Enterprise_AI_Final_Upgrade_Patch"
PREFIXES = ("qa_platform/", "web/", "docs/")
DOCS = ["INSTALLATION_GUIDE.md", "PATCH_CHANGELOG.md", "PATCH_COMPATIBILITY.md", "SECURITY_AUDIT.md",
        "REGRESSION_TEST_REPORT.md", "OFFLINE_INSTALLATION_TEST_REPORT.md", "FINAL_RELEASE_NOTES.md"]
TOOLS = ["APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py"]


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    changed = set(git("diff", "--name-only", "--diff-filter=AMR", args.baseline)) | set(git("ls-files", "--others", "--exclude-standard"))
    files = sorted(f for f in changed if f.startswith(PREFIXES) and "__pycache__" not in f and (REPO / f).is_file()
                   and not f.startswith("docs/final-patch/evidence/"))
    stage = Path(args.out) / NAME
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "app").mkdir(parents=True)
    for rel in files:
        dst = stage / "app" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst)
    manifest = {"version": "23.4.0", "baseline": "23.3.35", "files": {rel: sha(stage / "app" / rel) for rel in files}}
    (stage / "PATCH_MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for t in TOOLS:
        shutil.copy2(HERE / t, stage / t)
    for d in DOCS:
        shutil.copy2(REPO / "docs" / "final-patch" / d, stage / d)
    evidence = REPO / "docs" / "final-patch" / "evidence"
    if evidence.exists():
        shutil.copytree(evidence, stage / "evidence")
    lines = [f"{sha(p)}  {p.relative_to(stage).as_posix()}" for p in sorted(stage.rglob("*"))
             if p.is_file() and p.name != "SHA256_CHECKSUM.txt"]
    (stage / "SHA256_CHECKSUM.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    zpath = Path(args.out) / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                z.write(p, f"{NAME}/{p.relative_to(stage).as_posix()}")
    print(json.dumps({"zip": str(zpath), "sha256": sha(zpath), "app_files": len(files),
                      "size_mb": round(zpath.stat().st_size / 1048576, 2)}))


if __name__ == "__main__":
    main()
