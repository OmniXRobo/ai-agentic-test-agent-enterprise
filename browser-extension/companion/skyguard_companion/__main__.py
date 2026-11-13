"""python -m skyguard_companion [serve | pair | check | status | version]

    serve    run the companion (started at sign-in by the per-user Run entry the installer adds)
    pair     show a new pairing code for the extension (valid 10 minutes, single use)
    check    prerequisite check (Windows version, browsers, runtimes, models, port, vault)
    status   is the companion running on this PC?
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import urllib.request

from . import NAME, VERSION
from .config import Config, Paths


def _status(paths: Paths) -> dict:
    port = int(Config(paths).get("port"))
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return {"running": True, "port": port, **json.loads(r.read().decode("utf-8"))}
    except OSError:
        return {"running": False, "port": port}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"
    paths = Paths().ensure()
    if cmd == "version":
        print(f"{NAME} {VERSION}")
        return 0
    if cmd == "status":
        print(json.dumps(_status(paths), indent=1))
        return 0
    if cmd == "check":
        from .prereq import run_checks

        report = run_checks(paths)
        print(json.dumps(report, indent=1))
        return 0 if report["ok"] else 1
    if cmd == "pair":
        from .security import Pairing

        code = Pairing(paths.devices_file, paths.pairing_file).new_code()
        print(f"\n  Pairing code for the SkyGuard extension:  {code[:4]}-{code[4:]}\n"
              "  Type it in the extension (Options > Pair with companion). Valid for 10 minutes, single use.\n")
        return 0
    if cmd != "serve":
        print(__doc__)
        return 2
    from .server import Companion, serve, setup_logging

    setup_logging(paths)
    if _status(paths)["running"]:
        print("The companion is already running.")
        return 0
    companion = Companion(paths)
    httpd = serve(companion)
    from .security import Pairing

    if not Pairing(paths.devices_file, paths.pairing_file).devices():
        code = companion.pairing.new_code()
        print(f"Pairing code for the extension: {code[:4]}-{code[4:]} (valid 10 minutes)")
    companion.start_models()
    stop = threading.Event()

    def _quit(*_: object) -> None:
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _quit)
        except (ValueError, OSError):
            pass
    print(f"{NAME} {VERSION} listening on http://127.0.0.1:{httpd.server_address[1]} (this PC only)")
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        companion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
