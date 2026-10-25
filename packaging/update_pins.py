"""Refresh the pinned Java runtime and llama-server in packaging/offline-lock.json (maintainers only).

    python packaging/update_pins.py            # show what would change
    python packaging/update_pins.py --write    # update offline-lock.json

Each candidate is downloaded once and its SHA-256 is checked against the checksum its publisher states (Adoptium's
API for Temurin, the GitHub release asset digest for llama.cpp) before it is pinned. Only releases at least
--min-age-days old are taken, so a pin never points at a build published minutes ago. Python itself is pinned by
hand from nuget.org (its catalog states a SHA-512 for every package). The offline build always re-checks the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / "offline-lock.json"
UA = {"User-Agent": "ai-agentic-test-agent-enterprise-pins"}


def get_json(url: str, headers: dict[str, str] | None = None):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(headers or {})}), timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url: str, dest: Path) -> str:
    h = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, dest.open("wb") as out:
        while block := r.read(1 << 20):
            h.update(block)
            out.write(block)
    return h.hexdigest()


def old_enough(stamp: str, days: int) -> bool:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) <= datetime.now(timezone.utc) - timedelta(days=days)


def temurin(days: int, work: Path) -> dict:
    releases = get_json("https://api.adoptium.net/v3/assets/feature_releases/17/ga?architecture=x64&image_type=jre"
                        "&os=windows&jvm_impl=hotspot&heap_size=normal&vendor=eclipse&page_size=10&sort_order=DESC")
    for rel in releases:
        pkg = next((b["package"] for b in rel["binaries"] if b["package"]["name"].endswith(".zip")), None)
        if pkg and old_enough(rel["timestamp"], days):
            actual = download(pkg["link"], work / pkg["name"])
            if actual != pkg["checksum"]:
                raise SystemExit(f"Temurin {pkg['name']}: downloaded SHA-256 {actual} != published {pkg['checksum']}")
            ver = rel["version_data"]["openjdk_version"].removesuffix("-LTS")
            return {"name": "Eclipse Temurin JRE (Windows x64)", "version": ver, "url": pkg["link"], "sha256": actual,
                    "publisher": "Eclipse Adoptium", "checksum_source": "api.adoptium.net package checksum, re-verified on download",
                    "license": "GPL-2.0-with-classpath-exception"}
    raise SystemExit("No Temurin 17 JRE release old enough was found.")


def llama_server(days: int, work: Path) -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    # llama.cpp publishes several builds a day, so the first old-enough one can be many pages back.
    releases = (rel for page in range(1, 11)
                for rel in get_json(f"https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100&page={page}", headers))
    for rel in releases:
        if rel["prerelease"] or rel["draft"] or not old_enough(rel["published_at"], days):
            continue
        asset = next((a for a in rel["assets"] if a["name"].endswith("-bin-win-cpu-x64.zip")), None)
        if not asset or not (asset.get("digest") or "").startswith("sha256:"):
            print(f"[pins] {rel['tag_name']}: no Windows CPU x64 zip with a digest ({', '.join(a['name'] for a in rel['assets'])[:300]})")
            continue
        path = work / asset["name"]
        actual = download(asset["browser_download_url"], path)
        if actual != asset["digest"].split(":", 1)[1]:
            raise SystemExit(f"llama.cpp {asset['name']}: downloaded SHA-256 {actual} != GitHub digest {asset['digest']}")
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        if not any(n.rsplit("/", 1)[-1] == "llama-server.exe" for n in names):
            continue
        print(f"[pins] {asset['name']} contains: {', '.join(sorted(n for n in names if n.endswith(('.exe', '.dll'))))}")
        return {"name": "llama.cpp llama-server (Windows x64, CPU)", "version": rel["tag_name"],
                "url": asset["browser_download_url"], "sha256": actual, "publisher": "ggml-org (llama.cpp)",
                "checksum_source": "GitHub release asset digest, re-verified on download", "license": "MIT"}
    raise SystemExit("No llama.cpp release with a Windows CPU x64 build old enough was found.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="update offline-lock.json")
    ap.add_argument("--min-age-days", type=int, default=14)
    ap.add_argument("--work", type=Path, default=Path(os.environ.get("RUNNER_TEMP", ".")) / "pins")
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    fresh = {"jre": temurin(args.min_age_days, args.work), "llama_server": llama_server(args.min_age_days, args.work)}
    for key, item in fresh.items():
        old = lock.get(key, {})
        same = old.get("sha256") == item["sha256"]
        print(f"[pins] {key}: {old.get('version', '-')} -> {item['version']}{' (unchanged)' if same else ''}")
        lock[key] = item
    print(json.dumps(fresh, indent=2))
    if args.write:
        LOCK_PATH.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        print(f"[pins] wrote {LOCK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
