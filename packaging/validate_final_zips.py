"""Validates the two final deliverables: each zip opens, passes the CRC test, has every expected file, every file
matches SHA256_CHECKSUM.txt inside the zip, and the zip matches its .zip.sha256.

    python packaging/validate_final_zips.py <folder with both zips>
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

PATCH = "SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch"
EXT = "SkyGuard_Adaptive_AI_Browser_Extension_Final"

EXPECTED = {
    PATCH: ["APPLY_PATCH.cmd", "APPLY_PATCH.ps1", "ROLLBACK_PATCH.cmd", "VERIFY_PATCH.cmd", "install_patch.py",
            "PATCH_MANIFEST.json", "SHA256_CHECKSUM.txt", "INSTALLATION_GUIDE.md", "USER_GUIDE.md", "PATCH_CHANGELOG.md",
            "PATCH_COMPATIBILITY.md", "SECURITY_AUDIT.md", "REGRESSION_TEST_REPORT.md",
            "OFFLINE_INSTALLATION_TEST_REPORT.md", "FINAL_RELEASE_NOTES.md",
            "app/qa_platform/__init__.py", "app/qa_platform/opsagent/service.py", "app/qa_platform/api/opsagent.py",
            "app/web/js/pages/ops-agent.js", "app/skyguard_agent/agent.py", "app/skyguard_agent/executor.py",
            "app/skyguard_agent/vault.py", "app/skyguard_agent/secrets_guard.py",
            "tests/test_ops_agent_core.py", "tests/test_ops_agent_e2e.py", "tests/test_ops_patch_install.py"],
    EXT: ["INSTALL.cmd", "UNINSTALL.cmd", "README.txt", "SHA256_CHECKSUM.txt",
          "extension/manifest.json", "extension/background.js", "extension/sidepanel.html", "extension/sidepanel.js",
          "extension/options.html", "extension/credential.html", "extension/mic.html", "extension/lib/agent_dom.js",
          "extension/lib/secret-check.js", "extension/icons/icon128.png",
          "companion/companion_main.py", "companion/skyguard_companion/server.py",
          "companion/skyguard_companion/security.py", "companion/skyguard_agent/agent.py", "companion/whisper_server.py",
          "offline/python.3.13.17.nupkg", "offline/llama-b10549-bin-win-cpu-x64.zip", "offline/fw-requirements.txt",
          "offline/OFFLINE_MANIFEST.json",
          "scripts/Install-SkyGuardAgent.ps1", "scripts/Uninstall-SkyGuardAgent.ps1", "scripts/Install-Models.ps1",
          "scripts/Check-Prerequisites.ps1", "scripts/SkyGuardAgent.cmd",
          "docs/INSTALL_WINDOWS11.md", "docs/INSTALL_CHROME.md", "docs/INSTALL_EDGE.md", "docs/COMPANION_SETUP.md",
          "docs/OFFLINE_DEPENDENCIES.md", "docs/SECURITY.md", "docs/TROUBLESHOOTING.md", "docs/UNINSTALL.md",
          "docs/ENTERPRISE_DEPLOYMENT.md", "docs/USER_GUIDE.md", "docs/TEST_REPORT.md", "docs/CHANGELOG.md",
          "tests/test_extension_e2e.py", "tests/test_companion_security.py"],
}


def check(folder: Path, name: str) -> dict:
    zpath = folder / f"{name}.zip"
    out: dict = {"zip": zpath.name, "problems": []}
    if not zpath.is_file():
        out["problems"].append("missing")
        return out
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    out["sha256"] = digest
    out["size_mb"] = round(zpath.stat().st_size / 1048576, 2)
    side = folder / f"{name}.zip.sha256"
    if not side.is_file() or side.read_text().split()[0] != digest:
        out["problems"].append(".zip.sha256 missing or different")
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            out["problems"].append(f"CRC error in {bad}")
        names = set(z.namelist())
        out["files"] = len(names)
        if any(not n.startswith(name + "/") for n in names):
            out["problems"].append("files outside the top folder")
        if any(".." in n.split("/") or n.startswith("/") for n in names):
            out["problems"].append("unsafe path")
        missing = [e for e in EXPECTED[name] if f"{name}/{e}" not in names]
        if missing:
            out["problems"].append("missing: " + ", ".join(missing))
        if any("__pycache__" in n or n.endswith(".pyc") for n in names):
            out["problems"].append("compiled caches included")
        sums = z.read(f"{name}/SHA256_CHECKSUM.txt").decode().splitlines()
        listed = {}
        for line in sums:
            h, rel = line.split("  ", 1)
            listed[rel] = h
        for rel, h in listed.items():
            if hashlib.sha256(z.read(f"{name}/{rel}")).hexdigest() != h:
                out["problems"].append(f"checksum differs: {rel}")
        unlisted = sorted(n[len(name) + 1:] for n in names if not n.endswith("/"))
        unlisted = [r for r in unlisted if r not in listed and r != "SHA256_CHECKSUM.txt"]
        if unlisted:
            out["problems"].append("not in SHA256_CHECKSUM.txt: " + ", ".join(unlisted[:5]))
        out["checksummed_files"] = len(listed)
        if name == PATCH:
            manifest = json.loads(z.read(f"{name}/PATCH_MANIFEST.json"))
            for rel, h in manifest["files"].items():
                if hashlib.sha256(z.read(f"{name}/app/{rel}")).hexdigest() != h:
                    out["problems"].append(f"PATCH_MANIFEST differs: {rel}")
            out["patch_app_files"] = len(manifest["files"])
        if name == EXT:
            m = json.loads(z.read(f"{name}/extension/manifest.json"))
            if m.get("manifest_version") != 3:
                out["problems"].append("not MV3")
            out["wheels"] = len([n for n in names if n.startswith(f"{name}/offline/wheels/") and n.endswith(".whl")])
    out["ok"] = not out["problems"]
    return out


def main() -> int:
    folder = Path(sys.argv[1])
    zips = sorted(p.name for p in folder.glob("*.zip"))
    report = {"zips_in_folder": zips, "results": [check(folder, PATCH), check(folder, EXT)]}
    report["ok"] = all(r["ok"] for r in report["results"]) and zips == sorted([f"{PATCH}.zip", f"{EXT}.zip"])
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
