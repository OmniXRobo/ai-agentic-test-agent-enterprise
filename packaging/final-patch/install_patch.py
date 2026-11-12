"""SkyGuard QA final consolidated upgrade patch 23.4.0: offline installer, verifier and rollback.

Runs with the application's own Python (no packages beyond the standard library are needed for the installer itself).
Every step is printed; nothing is downloaded.

    python install_patch.py apply    --app <app folder> [--data <data folder>] [--python <app python>]
    python install_patch.py verify   --app <app folder> [--data <data folder>] [--python <app python>]
    python install_patch.py rollback --app <app folder> [--backup <backup folder>] [--restore-database]

apply:
  1. detects the installation and its version (it must be 23.3.35, or 23.4.0 to apply again),
  2. checks every file of the patch against PATCH_MANIFEST.json (SHA-256),
  3. checks free disk space and that the application and data folders can be written,
  4. checks the application is not running (its port does not answer),
  5. backs up every application file it replaces, the database (SQLite online backup) and settings.json,
  6. copies the new files (each written to a temporary name, then renamed),
  7. migrates the database (migrations 17 and 18 only add tables and one column; no row is changed or deleted),
  8. verifies: the new version loads, every migration is recorded, PRAGMA integrity_check is ok and no table has fewer
     rows than before,
  9. on any failure restores the previous files and the database copy taken in step 5 (the half-migrated database is
     kept next to it, renamed, never deleted).

rollback (after a successful apply): restores the previous application files and removes the files the patch added.
The database is kept as it is by default: the patch only added tables and a column, which 23.3.35 ignores, so nothing
created after the upgrade is lost. --restore-database puts back the copy taken before the upgrade instead (the
current database is kept next to it, renamed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PATCH_VERSION = "23.4.0"
BASELINES = ("23.3.35", PATCH_VERSION)
MIGRATION_COUNT = 18
MARGIN_MB = 300


class PatchError(Exception):
    pass


def say(msg: str) -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_of(app: Path) -> str:
    text = (app / "qa_platform" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "unknown"


def locate(app_arg: str, data_arg: str | None) -> tuple[Path, Path, Path]:
    """(app, root, data) the same way the application finds them: an installed copy keeps data next to app."""
    base = Path(app_arg).expanduser().resolve()
    if (base / "app" / "qa_platform" / "__init__.py").exists():
        base = base / "app"
    if not (base / "qa_platform" / "__init__.py").exists():
        raise PatchError(f"No SkyGuard QA installation in {app_arg} (qa_platform\\__init__.py not found).")
    installed = (base / "INSTALLED.json").exists()
    root = base.parent if installed else base
    data = Path(data_arg).expanduser().resolve() if data_arg else Path(os.environ.get("QA_HOME") or (root / "data" if installed else base / "data")).resolve()
    return base, root, data


def free_mb(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free // (1024 * 1024)


def tree_mb(paths: list[Path]) -> float:
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total / (1024 * 1024)


def writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".patch-write-test-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def port_of(data: Path) -> int:
    try:
        return int(json.loads((data / "settings.json").read_text(encoding="utf-8-sig")).get("server", {}).get("port") or 8765)
    except (OSError, ValueError, AttributeError):
        return 8765


def running(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def row_counts(db: Path) -> dict[str, int]:
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        conn.close()


def sqlite_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a database in WAL mode (the online backup API includes the WAL's content)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def payload_files(patch_dir: Path) -> list[str]:
    root = patch_dir / "app"
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())


def check_payload(patch_dir: Path) -> list[str]:
    manifest = json.loads((patch_dir / "PATCH_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("version") != PATCH_VERSION:
        raise PatchError(f"PATCH_MANIFEST.json is for {manifest.get('version')}, not {PATCH_VERSION}.")
    files = manifest["files"]
    bad = [rel for rel, digest in files.items() if not (patch_dir / "app" / rel).is_file()
           or sha256(patch_dir / "app" / rel) != digest]
    extra = sorted(set(payload_files(patch_dir)) - set(files))
    if bad or extra:
        raise PatchError("The patch folder is damaged or changed: "
                         + ", ".join((bad + extra)[:10]) + (" ..." if len(bad) + len(extra) > 10 else "")
                         + ". Extract the zip again.")
    return sorted(files)


def run_py(python: str, app: Path, data: Path, code: str) -> str:
    env = {**os.environ, "QA_HOME": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([python, "-E", "-s", "-c", code], cwd=str(app), env=env, capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        raise PatchError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else
                         f"exit code {r.returncode}")
    return r.stdout.strip()


MIGRATE = ("import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; "
           "from qa_platform.storage.db import Database; p = Paths().ensure(); d = Database(p.db_file); "
           "print(d.query_one('SELECT MAX(version) AS v FROM schema_version')['v']); d.close()")
LOADS = "import sys; sys.path.insert(0, '.'); import qa_platform, qa_platform.main; print(qa_platform.__version__)"


def verify(app: Path, data: Path, python: str, before_counts: dict[str, int] | None = None) -> dict:
    report: dict = {"checks": []}

    def check(name: str, ok: bool, detail: str) -> None:
        report["checks"].append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        say(f"  {name:<34} {'PASS' if ok else 'FAIL'}  {detail}")

    v = version_of(app)
    check("Version file", v == PATCH_VERSION, v)
    try:
        loaded = run_py(python, app, data, LOADS)
        check("Application loads", loaded.splitlines()[-1] == PATCH_VERSION, loaded.splitlines()[-1])
    except PatchError as exc:
        check("Application loads", False, str(exc))
    db = data / "qa_platform.sqlite3"
    if db.exists():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            cols = [r[1] for r in conn.execute("PRAGMA table_info(app_credentials)")]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        check("Database migrations", ver == MIGRATION_COUNT, f"schema version {ver} (expected {MIGRATION_COUNT})")
        check("Database integrity", integrity == "ok", integrity)
        check("Comparison tables", {"cmp_projects", "cmp_records", "cmp_audit"} <= tables,
              "present" if {"cmp_projects", "cmp_records", "cmp_audit"} <= tables else "missing")
        check("Login sign-in method column", "auth_method" in cols, "present" if "auth_method" in cols else "missing")
        if before_counts is not None:
            after = row_counts(db)
            lost = {t: (n, after.get(t, 0)) for t, n in before_counts.items() if after.get(t, 0) < n}
            check("No data lost", not lost, "every table kept its rows" if not lost else
                  "; ".join(f"{t}: {a} -> {b}" for t, (a, b) in lost.items()))
    else:
        check("Database", True, "no database yet (created at first start)")
    missing = [rel for rel in json.loads((app / "PATCH_FILES_23.4.0.json").read_text(encoding="utf-8"))["files"]
               if not (app / rel).exists()] if (app / "PATCH_FILES_23.4.0.json").exists() else ["PATCH_FILES_23.4.0.json"]
    check("Patch files present", not missing, "all" if not missing else ", ".join(missing[:5]))
    report["ok"] = all(c["status"] == "PASS" for c in report["checks"])
    return report


def restore_files(app: Path, backup: Path, added: list[str]) -> None:
    files_dir = backup / "files"
    if files_dir.exists():
        for src in files_dir.rglob("*"):
            if src.is_file():
                dst = app / src.relative_to(files_dir)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    for rel in added:  # files this patch added (application code only, never data)
        p = app / rel
        if p.is_file():
            p.unlink()
        parent = p.parent
        while parent != app and parent.is_dir() and not any(parent.iterdir()):  # folders the patch created
            parent.rmdir()
            parent = parent.parent
    for cache in app.joinpath("qa_platform").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def apply(args: argparse.Namespace) -> int:
    patch_dir = Path(__file__).resolve().parent
    python = args.python or sys.executable
    step(f"SkyGuard QA final consolidated patch {PATCH_VERSION}")
    app, root, data = locate(args.app, args.data)
    before = version_of(app)
    say(f"Application folder : {app}\nData folder        : {data}  (kept)\nInstalled version  : {before} -> {PATCH_VERSION}")
    if before not in BASELINES:
        raise PatchError(f"This patch installs on 23.3.35 (found {before}). Bring the installation to 23.3.35 first.")

    step("Checking the patch files (SHA-256)")
    files = check_payload(patch_dir)
    say(f"{len(files)} files verified.")

    step("Checking disk space, permissions and that the application is stopped")
    db = data / "qa_platform.sqlite3"
    need = tree_mb([patch_dir / "app"]) + tree_mb([app / rel for rel in files]) + tree_mb([db]) + MARGIN_MB
    have = free_mb(root)
    say(f"Free space: {have} MB, needed: {need:.0f} MB")
    if have < need:
        raise PatchError(f"Not enough free disk space on {root}: {have} MB free, {need:.0f} MB needed.")
    for folder in (app, data, root / "patch-backups"):
        if not writable(folder):
            raise PatchError(f"The folder {folder} cannot be written. Run as the user who installed SkyGuard QA.")
    port = port_of(data)
    if running(port) and not args.skip_running_check:
        raise PatchError(f"SkyGuard QA is still running (port {port} answers). Close it, then run the patch again.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = root / "patch-backups" / f"{before}-to-{PATCH_VERSION}-{stamp}"
    step("Backing up")
    (backup / "files").mkdir(parents=True, exist_ok=True)
    replaced, added = [], []
    for rel in files:
        src = app / rel
        if src.exists():
            dst = backup / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            replaced.append(rel)
        else:
            added.append(rel)
    before_counts = row_counts(db)
    if db.exists():
        sqlite_copy(db, backup / "qa_platform.sqlite3")
    if (data / "settings.json").exists():
        shutil.copy2(data / "settings.json", backup / "settings.json")
    info = {"from": before, "to": PATCH_VERSION, "created": stamp, "app": str(app), "data": str(data),
            "replaced": replaced, "added": added, "database": db.exists(), "row_counts": before_counts}
    (backup / "BACKUP.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    say(f"Backup: {backup}  ({len(replaced)} files replaced, {len(added)} new, database {'copied' if db.exists() else 'not present yet'})")

    try:
        step("Copying the new files")
        for cache in app.joinpath("qa_platform").rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        for rel in files:
            src, dst = patch_dir / "app" / rel, app / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".patch-tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        (app / "PATCH_FILES_23.4.0.json").write_text(json.dumps({"version": PATCH_VERSION, "files": files}, indent=1),
                                                     encoding="utf-8")
        say(f"{len(files)} files copied.")
        if args.fail_after_copy:  # test hook: proves the automatic rollback
            raise PatchError("simulated failure after copying (--fail-after-copy)")

        step("Migrating the database")
        v = run_py(python, app, data, MIGRATE).splitlines()[-1]
        say(f"Schema version {v}.")

        step("Verifying")
        report = verify(app, data, python, before_counts)
        if not report["ok"]:
            raise PatchError("verification failed: " + "; ".join(c["name"] for c in report["checks"] if c["status"] == "FAIL"))
    except Exception as exc:  # noqa: BLE001 - every failure rolls back
        step("Restoring the previous version")
        restore_files(app, backup, added)
        (app / "PATCH_FILES_23.4.0.json").unlink(missing_ok=True)
        if db.exists() and (backup / "qa_platform.sqlite3").exists():
            kept = db.with_name(f"qa_platform.failed-upgrade-{stamp}.sqlite3")
            for suffix in ("-wal", "-shm"):
                side = Path(str(db) + suffix)
                if side.exists():
                    os.replace(side, Path(str(kept) + suffix))
            os.replace(db, kept)
            shutil.copy2(backup / "qa_platform.sqlite3", db)
            say(f"Database restored from the backup; the database as it was after the failed step is kept as {kept.name}.")
        say(f"Previous version {version_of(app)} restored.")
        raise PatchError(f"{exc}. Nothing was lost; the previous version was restored. Backup: {backup}") from exc

    history = app / "PATCH_HISTORY.json"
    entries = json.loads(history.read_text(encoding="utf-8")) if history.exists() else []
    entries.append({"version": PATCH_VERSION, "from": before, "applied": stamp, "backup": str(backup)})
    history.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    (backup / "VERIFY.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    step("Result")
    say(f"Patched {before} -> {PATCH_VERSION}. Backup for rollback: {backup}")
    return 0


def rollback(args: argparse.Namespace) -> int:
    app, root, data = locate(args.app, args.data)
    backups = sorted((root / "patch-backups").glob(f"*-to-{PATCH_VERSION}-*")) if not args.backup else [Path(args.backup)]
    if not backups:
        raise PatchError(f"No {PATCH_VERSION} backup found in {root / 'patch-backups'}.")
    backup = backups[-1]
    info = json.loads((backup / "BACKUP.json").read_text(encoding="utf-8"))
    port = port_of(data)
    if running(port) and not args.skip_running_check:
        raise PatchError(f"SkyGuard QA is still running (port {port} answers). Close it first.")
    step(f"Rolling back {PATCH_VERSION} -> {info['from']} from {backup}")
    restore_files(app, backup, info["added"])
    (app / "PATCH_FILES_23.4.0.json").unlink(missing_ok=True)
    db = data / "qa_platform.sqlite3"
    if args.restore_database and (backup / "qa_platform.sqlite3").exists():
        kept = db.with_name(f"qa_platform.before-rollback-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3")
        if db.exists():
            sqlite_copy(db, kept)
            say(f"Current database kept as {kept.name}.")
        for suffix in ("-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        shutil.copy2(backup / "qa_platform.sqlite3", db)
        say("Database restored from the copy taken before the upgrade.")
    else:
        say("Database kept as it is (23.3.35 ignores the tables and column the patch added; nothing is lost).")
    say(f"Version now {version_of(app)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("apply", "verify", "rollback"))
    ap.add_argument("--app", required=True)
    ap.add_argument("--data")
    ap.add_argument("--python")
    ap.add_argument("--backup")
    ap.add_argument("--restore-database", action="store_true")
    ap.add_argument("--skip-running-check", action="store_true")
    ap.add_argument("--fail-after-copy", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        if args.command == "apply":
            return apply(args)
        if args.command == "rollback":
            return rollback(args)
        app, _root, data = locate(args.app, args.data)
        step(f"Verifying {app}")
        return 0 if verify(app, data, args.python or sys.executable)["ok"] else 1
    except PatchError as exc:
        print(f"\nPATCH FAILED: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
