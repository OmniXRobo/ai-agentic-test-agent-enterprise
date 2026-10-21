"""MANIFEST.json: the package's file hashes and dependency inventory.

The manifest sits at the root of the application folder (the package's ``payload``, later ``<install>/app``).
Every file under that folder is listed with its SHA-256, except the manifest itself and files the application
writes at run time (``data/``, ``__pycache__``), so a copy can be checked for damage or tampering offline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

MANIFEST_NAME = "MANIFEST.json"
SKIP_DIRS = {"__pycache__", "data", ".pytest_cache"}
SKIP_FILES = {MANIFEST_NAME, "INSTALLED.json"}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or (path.is_dir() and path.name in SKIP_DIRS):
            continue
        if path.is_file() and not path.is_symlink() and rel.as_posix() not in SKIP_FILES and path.suffix != ".pyc":
            yield path


def hash_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in iter_files(root)}


def write(root: Path, info: dict[str, Any]) -> dict[str, Any]:
    """Hash ``root`` and write its manifest. ``info`` carries name, version, built_at and components."""
    manifest = {**info, "files": hash_tree(root)}
    (root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=False), encoding="utf-8")
    return manifest


def load(root: Path) -> dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify(root: Path, manifest: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Compare ``root`` with its manifest: files missing, changed, or not listed."""
    manifest = manifest if manifest is not None else load(root)
    if manifest is None:
        raise FileNotFoundError(f"{root / MANIFEST_NAME} was not found.")
    expected: dict[str, str] = manifest["files"]
    missing, changed = [], []
    for rel, digest in expected.items():
        path = root / rel
        if not path.is_file():
            missing.append(rel)
        elif sha256_file(path) != digest:
            changed.append(rel)
    extra = [p.relative_to(root).as_posix() for p in iter_files(root) if p.relative_to(root).as_posix() not in expected]
    return {"missing": missing, "changed": changed, "extra": extra}
