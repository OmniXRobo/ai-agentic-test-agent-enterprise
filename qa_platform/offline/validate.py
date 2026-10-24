"""VALIDATE_OFFLINE: prove an offline copy is complete and runs on this PC without Internet.

Run with the copy's own Python: ``runtime\\python\\python.exe -m qa_platform.offline.validate``.
Every check runs with outbound network connections blocked for this process, so a PASS means the copy
did not need the Internet. The report lists versions and paths only, never credentials.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import importlib.metadata
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..clock import now
from ..config import APP_ROOT, DEFAULT_SETTINGS
from . import manifest as mf

WINDOWS = sys.platform == "win32"
REQUIRED_MODULES = {"fastapi": "fastapi", "uvicorn": "uvicorn", "httpx": "httpx", "pydantic": "pydantic",
                    "keyring": "keyring", "playwright": "playwright", "pytest": "pytest", "vosk": "vosk"}


@dataclass
class Check:
    name: str
    status: str  # PASS | WARN | FAIL | INFO
    detail: str


# ---- network guard ---------------------------------------------------------------------------------


class NetworkBlocked(OSError):
    pass


def _is_loopback(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    return isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127."))


@contextmanager
def network_blocked() -> Iterator[None]:
    """Refuse outbound connections from this process except to this PC (127.0.0.1)."""
    original = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> object:
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
            raise NetworkBlocked(f"Outbound connection to {address!r} blocked during offline validation.")
        return original(self, address)

    socket.socket.connect = guarded  # type: ignore[method-assign]
    try:
        yield
    finally:
        socket.socket.connect = original  # type: ignore[method-assign]


# ---- helpers ---------------------------------------------------------------------------------------


def _bundled(app: Path, prefix: str) -> Path | None:
    runtime = app / "runtime"
    if not runtime.is_dir():
        return None
    return next((c for c in sorted(runtime.iterdir()) if c.is_dir() and c.name.lower().startswith(prefix)), None)


def _java(app: Path) -> Path | None:
    for prefix in ("jre", "jdk"):
        home = _bundled(app, prefix)
        exe = home / "bin" / ("java.exe" if WINDOWS else "java") if home else None
        if exe and exe.is_file():
            return exe
    return None


def _run(cmd: list[str], timeout: int = 90, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, creationflags=flags)


def is_admin() -> bool | None:
    try:
        if WINDOWS:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        return os.geteuid() == 0
    except (AttributeError, OSError):
        return None


# ---- checks ----------------------------------------------------------------------------------------


class Validator:
    def __init__(self, app: Path, data: Path | None, manifest: dict | None, quick: bool = False) -> None:
        self.app = app
        self.data = data
        self.manifest = manifest
        self.quick = quick

    def checks(self) -> list[tuple[str, Callable[[], Check]]]:
        items = [
            ("Operating system", self.check_os),
            ("Administrator rights", self.check_admin),
            ("Package files", self.check_files),
            ("Python runtime", self.check_python),
            ("Python packages", self.check_packages),
            ("Java runtime", self.check_java),
            ("JMeter", self.check_jmeter),
            ("Chromium for Playwright", self.check_chromium),
            ("Credential store", self.check_credentials),
            ("Data folder", self.check_data),
            ("Dashboard port", self.check_port),
            ("On-Premise AI files", self.check_ai_files),
            ("Offline speech model", self.check_speech_files),
        ]
        if self.quick:  # used by the installer before switching versions: skip the slow launches
            items = [i for i in items if i[0] not in ("JMeter", "Chromium for Playwright")]
        return items

    def run(self) -> list[Check]:
        results = []
        with network_blocked():
            for name, fn in self.checks():
                try:
                    results.append(fn())
                except Exception as exc:  # a broken check must not stop the others
                    results.append(Check(name, "FAIL", f"{type(exc).__name__}: {exc}"))
        results.append(Check("Offline operation", "PASS" if not any(r.status == "FAIL" for r in results) else "INFO",
                             "All checks ran with outbound network connections blocked for this process."))
        return results

    def check_os(self) -> Check:
        bits = platform.architecture()[0]
        desc = f"{platform.system()} {platform.release()} ({platform.machine() or bits})"
        if WINDOWS and sys.maxsize > 2**32:
            return Check("Operating system", "PASS", desc)
        return Check("Operating system", "WARN", f"{desc}. The offline package is built for 64-bit Windows.")

    def check_admin(self) -> Check:
        admin = is_admin()
        if admin:
            return Check("Administrator rights", "WARN", "Running as administrator. This is not needed; a normal user account is enough.")
        return Check("Administrator rights", "PASS", "Running as a normal user; no administrator rights are needed.")

    def check_files(self) -> Check:
        if self.manifest is None:
            return Check("Package files", "WARN", f"No {mf.MANIFEST_NAME} in {self.app}; file integrity was not checked.")
        diff = mf.verify(self.app, self.manifest)
        total = len(self.manifest["files"])
        if diff["missing"] or diff["changed"]:
            sample = ", ".join((diff["missing"] + diff["changed"])[:5])
            return Check("Package files", "FAIL", f"{len(diff['missing'])} missing and {len(diff['changed'])} changed of "
                                                  f"{total} files (for example {sample}). Copy the package again.")
        note = f"; {len(diff['extra'])} files added since the build" if diff["extra"] else ""
        return Check("Package files", "PASS", f"All {total} files match the manifest{note}.")

    def check_python(self) -> Check:
        v = sys.version_info
        exe = Path(sys.executable).resolve()
        bundled = (self.app / "runtime").resolve() in exe.parents
        detail = f"Python {v.major}.{v.minor}.{v.micro} at {exe}"
        if v < (3, 10):
            return Check("Python runtime", "FAIL", detail + " (3.10 or newer is required)")
        expected = (self.manifest or {}).get("python")
        if expected and platform.python_version() != expected:
            return Check("Python runtime", "FAIL", f"{detail}; the package was built with Python {expected}.")
        return Check("Python runtime", "PASS" if bundled or self.manifest is None else "WARN",
                     detail + ("" if bundled else " (not the package's own Python)"))

    def check_packages(self) -> Check:
        expected = {p["name"].lower(): p["version"] for p in (self.manifest or {}).get("packages", [])}
        problems, found = [], []
        for module, dist in REQUIRED_MODULES.items():
            try:
                importlib.import_module(module)
                version = importlib.metadata.version(dist)
            except Exception as exc:
                problems.append(f"{module} ({type(exc).__name__})")
                continue
            if expected.get(dist) and expected[dist] != version:
                problems.append(f"{dist} {version} (built with {expected[dist]})")
            found.append(f"{dist} {version}")
        if problems:
            return Check("Python packages", "FAIL", "Not usable: " + ", ".join(problems))
        return Check("Python packages", "PASS", ", ".join(found))

    def check_java(self) -> Check:
        java = _java(self.app)
        if not java:
            return Check("Java runtime", "FAIL", f"No Java runtime in {self.app / 'runtime'} (needed by JMeter).")
        out = _run([str(java), "-version"], timeout=30)
        text = (out.stderr or out.stdout).strip().splitlines()
        if out.returncode != 0:
            return Check("Java runtime", "FAIL", f"{java} did not run: {' '.join(text[-2:])}")
        return Check("Java runtime", "PASS", f"{text[0] if text else 'Java'} ({java})")

    def check_jmeter(self) -> Check:
        home = _bundled(self.app, "apache-jmeter")
        jar = home / "bin" / "ApacheJMeter.jar" if home else None
        if not jar or not jar.is_file():
            return Check("JMeter", "FAIL", f"No JMeter in {self.app / 'runtime'}.")
        java = _java(self.app)
        if not java:
            return Check("JMeter", "FAIL", "JMeter needs the bundled Java runtime, which is missing.")
        with tempfile.TemporaryDirectory() as tmp:
            out = _run([str(java), "-jar", str(jar), "--version", "-j", str(Path(tmp) / "jmeter.log")], timeout=120)
        if out.returncode != 0:
            return Check("JMeter", "FAIL", f"JMeter did not start (exit {out.returncode}): {(out.stderr or out.stdout).strip()[-300:]}")
        return Check("JMeter", "PASS", f"{home.name} starts with the bundled Java ({home})")

    def check_chromium(self) -> Check:
        browsers = self.app / "runtime" / "ms-playwright"
        if not browsers.is_dir():
            return Check("Chromium for Playwright", "FAIL", f"No browsers in {browsers}.")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content("<p id='x'>offline</p>")
                text = page.inner_text("#x")
                version = browser.version
            finally:
                browser.close()
        if text != "offline":
            return Check("Chromium for Playwright", "FAIL", "Chromium started but did not render a page.")
        return Check("Chromium for Playwright", "PASS", f"Chromium {version} launched headless from {browsers}")

    def check_credentials(self) -> Check:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        name = f"{type(backend).__module__}.{type(backend).__name__}"
        if isinstance(backend, fail.Keyring):
            return Check("Credential store", "FAIL" if WINDOWS else "WARN",
                         "No credential store is available, so TFS PATs and application passwords cannot be saved.")
        label = "Windows Credential Manager" if "Windows" in name else name
        return Check("Credential store", "PASS", f"{label} (per user; secrets are never written to files)")

    def check_data(self) -> Check:
        if self.data is None:
            return Check("Data folder", "INFO", "Not checked (no data folder given).")
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.data, suffix=".sqlite3", delete=False) as tmp:
                probe = Path(tmp.name)
            try:
                con = sqlite3.connect(probe)
                con.execute("CREATE TABLE t (x)")
                con.close()
            finally:
                probe.unlink(missing_ok=True)
        except OSError as exc:
            return Check("Data folder", "FAIL", f"{self.data} is not writable ({exc}).")
        return Check("Data folder", "PASS", f"Writable, SQLite works: {self.data}")

    def _port(self) -> int:
        try:
            settings = json.loads((self.data / "settings.json").read_text(encoding="utf-8")) if self.data else {}
            return int(settings.get("server", {}).get("port") or DEFAULT_SETTINGS["server"]["port"])
        except (OSError, ValueError):
            return int(DEFAULT_SETTINGS["server"]["port"])

    def check_port(self) -> Check:
        port = self._port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            busy = s.connect_ex(("127.0.0.1", port)) == 0
        if not busy:
            return Check("Dashboard port", "PASS", f"Port {port} on 127.0.0.1 is free.")
        try:
            import httpx

            ok = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=3, trust_env=False).status_code == 200
        except Exception:
            ok = False
        if ok:
            return Check("Dashboard port", "PASS", f"Port {port} is used by a running copy of the dashboard.")
        return Check("Dashboard port", "FAIL", f"Port {port} is used by another program. Change the port in "
                                               f"{(self.data or Path('data')) / 'settings.json'} (server.port).")

    def check_ai_files(self) -> Check:
        ai = self.app / "runtime" / "ai"
        models = sorted(ai.rglob("*.gguf")) if ai.is_dir() else []
        if not models:
            return Check("On-Premise AI files", "INFO", "No model is bundled. Point On-Premise AI settings at your "
                                                        "llama.cpp (Qwen GGUF) server; nothing is downloaded.")
        sizes = ", ".join(f"{m.name} ({m.stat().st_size / 1e9:.1f} GB)" for m in models)
        server = next((p for p in ai.rglob("llama-server*") if p.is_file()), None)
        return Check("On-Premise AI files", "PASS", f"Bundled model: {sizes}"
                                                    + (f"; server: {server.name}" if server else "; no llama-server binary bundled"))

    def check_speech_files(self) -> Check:
        speech = self.app / "runtime" / "speech"
        models = sorted(p.parent for p in speech.glob("*/am")) if speech.is_dir() else []
        if not models:
            return Check("Offline speech model", "INFO", "No speech model is bundled. Voice commands can be typed, "
                                                         "or use a local speech server.")
        return Check("Offline speech model", "PASS", f"Voice commands are recognized on this PC with {models[0].name}.")


# ---- report ----------------------------------------------------------------------------------------


def render(results: list[Check], app: Path, manifest: dict | None) -> str:
    width = max(len(r.name) for r in results)
    head = ["AI Agentic Test Agent Enterprise - offline validation",
            f"Version: {(manifest or {}).get('version', 'unknown')}   Built: {(manifest or {}).get('built_at', 'unknown')}",
            f"Folder: {app}", f"Checked: {now().strftime('%Y-%m-%d %H:%M:%S')} (system clock)", ""]
    lines = [f"[{r.status:<4}] {r.name:<{width}}  {r.detail}" for r in results]
    failed = sum(r.status == "FAIL" for r in results)
    tail = ["", "RESULT: PASS - ready to use offline." if not failed else f"RESULT: FAIL - {failed} check(s) failed."]
    return "\n".join(head + lines + tail) + "\n"


def validate(app: Path = APP_ROOT, data: Path | None = None, quick: bool = False) -> tuple[list[Check], dict | None]:
    manifest = mf.load(app)
    return Validator(app, data, manifest, quick).run(), manifest


def default_data(app: Path) -> Path:
    """Installed copies keep data beside the app folder (it survives upgrades); a portable copy keeps it inside."""
    return app.parent / "data" if (app / "INSTALLED.json").exists() else app / "data"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate an offline copy of AI Agentic Test Agent Enterprise.")
    ap.add_argument("--app", type=Path, default=APP_ROOT)
    ap.add_argument("--data", type=Path)
    ap.add_argument("--report", type=Path, help="report file (default: logs\\validation-latest.txt of an install, "
                                                 "or validation-report.txt beside a package)")
    ap.add_argument("--quick", action="store_true", help="skip launching JMeter and Chromium")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    app = args.app.resolve()
    results, manifest = validate(app, (args.data or default_data(app)).resolve(), args.quick)
    text = render(results, app, manifest)
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=1))
    else:
        print(text, end="")
    report = args.report or (app.parent / "logs" / "validation-latest.txt" if (app / "INSTALLED.json").exists()
                             else app.parent / "validation-report.txt")
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        print(f"Report saved to {report}")
    except OSError as exc:  # e.g. the package is on a read-only share
        print(f"The report could not be saved to {report} ({exc}).")
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
