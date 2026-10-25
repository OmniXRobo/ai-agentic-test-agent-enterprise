"""The automation framework workspace: a portable folder of Page Objects, flows, tests and data.

    <data>/automation-workspace/
        page_objects/  flows/  tests/  test_data/  fixtures/  utilities/  execution-results/
        conftest.py  pytest.ini  README.txt  RUN_TESTS.cmd
        .versions/     previous copies of every file the platform replaced
        .staging/      temporary copies used to validate a change before it is saved

Every path the platform stores is relative to the workspace, and no generated file
contains an absolute path, so the folder keeps working after it is copied to another
Windows PC.

Writes are atomic: a change is validated in ``.staging`` first, then each file is
written to a temporary file next to its target and moved into place, and the
previous content is kept under ``.versions``. A failed validation never touches the
last working copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator

from ...clock import now_iso
from ...errors import QAError

TEMPLATE = Path(__file__).parent / "template"
AUTOMATION = Path(__file__).resolve().parent.parent

FOLDERS = ("page_objects", "flows", "tests", "test_data", "fixtures", "utilities", "execution-results")
# Files owned by the platform: refreshed when the platform is upgraded (previous copy kept in .versions).
RUNTIME_FILES = {
    "conftest.py": TEMPLATE / "_conftest.py",
    "pytest.ini": TEMPLATE / "pytest.ini",
    "README.txt": TEMPLATE / "README.txt",
    "RUN_TESTS.cmd": TEMPLATE / "RUN_TESTS.cmd",
    "fixtures/__init__.py": TEMPLATE / "fixtures" / "__init__.py",
    "fixtures/browser.py": TEMPLATE / "fixtures" / "browser.py",
    "fixtures/data.py": TEMPLATE / "fixtures" / "data.py",
    "utilities/__init__.py": TEMPLATE / "utilities" / "__init__.py",
    "utilities/config.py": TEMPLATE / "utilities" / "config.py",
    "utilities/runtime_values.py": TEMPLATE / "utilities" / "runtime_values.py",
    "utilities/locators.py": TEMPLATE / "utilities" / "locators.py",
    "utilities/base_page.py": TEMPLATE / "utilities" / "base_page.py",
    "utilities/reporting.py": TEMPLATE / "utilities" / "reporting.py",
    # One scoring algorithm and one set of browser helpers for the whole platform.
    "utilities/healing.py": AUTOMATION / "healing.py",
    "utilities/js/fingerprint.js": AUTOMATION / "js" / "fingerprint.js",
    "utilities/js/overlay.js": AUTOMATION / "js" / "overlay.js",
    "utilities/js/calendar.js": AUTOMATION / "js" / "calendar.js",
}
# Created once, then owned by the user and the Automation Engineering Agent.
SEED_FILES = {
    "page_objects/__init__.py": TEMPLATE / "page_objects" / "__init__.py",
    "flows/__init__.py": TEMPLATE / "flows" / "__init__.py",
    "tests/README.txt": TEMPLATE / "tests" / "README.txt",
    "test_data/README.txt": TEMPLATE / "test_data" / "README.txt",
}
STATE_FILE = ".framework.json"
SKIP_COPY = {".versions", ".staging", "execution-results", "__pycache__", ".pytest_cache"}
_SAFE_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FrameworkWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    # ---- layout ----------------------------------------------------------------------

    def runtime_signature(self) -> str:
        digest = hashlib.sha256()
        for rel, src in sorted(RUNTIME_FILES.items()):
            digest.update(rel.encode())
            digest.update(src.read_bytes())
        return digest.hexdigest()[:16]

    def ensure(self) -> "FrameworkWorkspace":
        """Create the workspace, or refresh the platform-owned runtime files after an upgrade."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            for folder in FOLDERS:
                (self.root / folder).mkdir(exist_ok=True)
        except OSError as exc:
            raise QAError("PROJECT_PATH_INVALID", f"The automation workspace folder cannot be created: {exc}.",
                          status=500) from exc
        state = self._state()
        signature = self.runtime_signature()
        if state.get("runtime") != signature or not all((self.root / rel).exists() for rel in RUNTIME_FILES):
            for rel, src in RUNTIME_FILES.items():
                data = src.read_bytes()
                target = self.path(rel)
                if target.exists() and target.read_bytes() == data:
                    continue
                self.write(rel, data, reason="framework runtime refresh")
            state["runtime"] = signature
            state.setdefault("created_at", now_iso())
            state["updated_at"] = now_iso()
            self._save_state(state)
        for rel, src in SEED_FILES.items():
            if not self.path(rel).exists():
                self.write(rel, src.read_bytes(), backup=False)
        return self

    def _state(self) -> dict:
        try:
            return json.loads((self.root / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        self._atomic(self.root / STATE_FILE, json.dumps(state, indent=2).encode())

    # ---- paths -------------------------------------------------------------------------

    def rel(self, rel: str) -> str:
        """Validate a workspace-relative path (forward slashes, no '..', no drive or root)."""
        rel = (rel or "").replace("\\", "/").strip()
        parts = PurePosixPath(rel).parts
        if not rel or rel.startswith("/") or ":" in rel or not parts or any(
                p in ("..", ".") or not _SAFE_PART.match(p) for p in parts):
            raise QAError("PROJECT_PATH_INVALID", f"Invalid workspace path '{rel}'.", status=400)
        if parts[0] in (".versions", ".staging"):
            raise QAError("PROJECT_PATH_INVALID", f"'{rel}' is not a framework file.", status=400)
        return "/".join(parts)

    def path(self, rel: str) -> Path:
        return self.root / self.rel(rel)

    def exists(self, rel: str) -> bool:
        return self.path(rel).is_file()

    def read(self, rel: str) -> str:
        path = self.path(rel)
        if not path.is_file():
            raise QAError("FRAMEWORK_FILE_NOT_FOUND", f"{rel} does not exist in the automation workspace.", status=404)
        return path.read_text(encoding="utf-8")

    def files(self, folder: str, pattern: str = "*.py") -> list[str]:
        base = self.root / folder
        if not base.is_dir():
            return []
        return sorted(f"{folder}/{p.name}" for p in base.glob(pattern) if p.is_file() and p.name != "__init__.py")

    # ---- writes ------------------------------------------------------------------------

    def _atomic(self, target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise QAError("SCRIPT_WRITE_FAILED", f"Unable to write {target.name}: {exc}.", status=500) from exc

    def backup(self, rel: str) -> str | None:
        """Keep the current content of ``rel`` under .versions; returns the backup's relative path."""
        source = self.path(rel)
        if not source.is_file():
            return None
        folder = self.root / ".versions" / rel
        folder.mkdir(parents=True, exist_ok=True)
        n = len(list(folder.glob("v*"))) + 1
        backup = folder / f"v{n:04d}{source.suffix}"
        shutil.copy2(source, backup)
        return backup.relative_to(self.root).as_posix()

    def write(self, rel: str, content: str | bytes, *, reason: str = "", backup: bool = True) -> dict:
        """Atomic write; the previous content is kept. Returns the verified file facts."""
        rel = self.rel(rel)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target = self.root / rel
        previous = self.backup(rel) if backup and target.is_file() and target.read_bytes() != data else None
        self._atomic(target, data)
        if not target.is_file() or sha256(target.read_bytes()) != sha256(data):
            raise QAError("SCRIPT_WRITE_FAILED", f"{rel} could not be verified after writing.", status=500)
        return {"path": rel, "sha256": sha256(data), "bytes": len(data), "previous": previous, "reason": reason}

    def restore_backup(self, rel: str, backup_rel: str | None) -> None:
        """Undo a write: put the backup back, or remove a file that did not exist before."""
        target = self.path(rel)
        if backup_rel:
            self._atomic(target, (self.root / backup_rel).read_bytes())
        else:
            target.unlink(missing_ok=True)

    def history(self, rel: str) -> list[dict]:
        folder = self.root / ".versions" / self.rel(rel)
        if not folder.is_dir():
            return []
        return [{"version": int(p.stem[1:]), "path": p.relative_to(self.root).as_posix(),
                 "saved_at": p.stat().st_mtime} for p in sorted(folder.glob("v*")) if p.stem[1:].isdigit()]

    def read_backup(self, backup_rel: str) -> str:
        path = (self.root / backup_rel).resolve()
        versions = (self.root / ".versions").resolve()
        if versions not in path.parents or not path.is_file():
            raise QAError("FRAMEWORK_VERSION_NOT_FOUND", "That version does not exist.", status=404)
        return path.read_text(encoding="utf-8")

    # ---- staging -----------------------------------------------------------------------

    @contextmanager
    def staging(self, changes: dict[str, str]) -> Iterator[Path]:
        """A temporary copy of the workspace with ``changes`` applied, removed afterwards."""
        stage = self.root / ".staging" / uuid.uuid4().hex[:12]
        try:
            shutil.copytree(self.root, stage, ignore=lambda d, names: [n for n in names if n in SKIP_COPY or n == STATE_FILE
                                                                       or n.endswith(".tmp")])
            for rel, content in changes.items():
                target = stage / self.rel(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            yield stage
        except OSError as exc:
            raise QAError("SCRIPT_WRITE_FAILED", f"Unable to prepare the validation copy: {exc}.", status=500) from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)
