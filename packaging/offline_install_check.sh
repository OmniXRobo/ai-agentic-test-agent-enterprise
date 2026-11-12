#!/usr/bin/env bash
# Offline installation check of the two final zips, run with networking disabled (a new network namespace that has
# only loopback). Linux stand-in for a Windows 11 PC: the PowerShell wrappers are not run, the Python installer they
# call is. Usage: packaging/offline_install_check.sh <folder with both zips> <work folder>
set -euo pipefail
ZIPS=$(cd "$1" && pwd); WORK=$2; REPO=$(cd "$(dirname "$0")/.." && pwd)
BASELINE=53e80093fe99272f27dc424392d1fd3d746b3c7c
P=SkyGuard_Adaptive_AI_Application_Learning_Operations_Agent_Final_Patch
E=SkyGuard_Adaptive_AI_Browser_Extension_Final
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"
LO_UP="import fcntl, socket, struct; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); fcntl.ioctl(s.fileno(), 0x8914, struct.pack('16sH14x', b'lo', 0x49))"
offline() { unshare -rn bash -c "python3 -c \"$LO_UP\" && $*"; }
say() { printf '\n== %s\n' "$*"; }

say "Network check inside the namespace"
offline "python3 -c \"import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=5)\" 2>/dev/null && echo 'INTERNET REACHABLE' || echo 'no internet (as intended)'"

say "Zip checksums"
(cd "$ZIPS" && sha256sum -c "$P.zip.sha256" "$E.zip.sha256")

say "Extract and check every file against SHA256_CHECKSUM.txt"
unzip -q "$ZIPS/$P.zip"; unzip -q "$ZIPS/$E.zip"
(cd "$P" && sha256sum -c --quiet SHA256_CHECKSUM.txt && echo "$P: $(wc -l < SHA256_CHECKSUM.txt) files OK")
(cd "$E" && sha256sum -c --quiet SHA256_CHECKSUM.txt && echo "$E: $(wc -l < SHA256_CHECKSUM.txt) files OK")

say "Patch: a SkyGuard QA 23.4.0 installation (git archive of the baseline commit) with a database"
mkdir -p inst/app inst/data
git -C "$REPO" archive "$BASELINE" qa_platform web | tar -x -C inst/app
grep __version__ inst/app/qa_platform/__init__.py
(cd inst/app && QA_HOME="$WORK/inst/data" python3 -c "import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; from qa_platform.storage.db import Database; d = Database(Paths().ensure().db_file); print('schema', d.query_one('SELECT MAX(version) AS v FROM schema_version')['v']); d.close()")

say "Patch: apply (offline)"
offline "python3 $P/install_patch.py apply --app inst/app --data inst/data --skip-running-check"
say "Patch: verify (offline)"
offline "python3 $P/install_patch.py verify --app inst/app --data inst/data"

say "Patch: start the patched SkyGuard (offline) and call it"
offline "cd inst/app && QA_HOME=$WORK/inst/data python3 -c 'import sys; sys.path.insert(0, \".\"); from qa_platform.main import run; run()' > $WORK/skyguard.log 2>&1 &
  for i in \$(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8765/api/health && break; sleep 1; done
  echo -n 'GET /api/health -> '; curl -s http://127.0.0.1:8765/api/health; echo
  echo -n 'GET /api/ops/info from a browser without sign-in -> HTTP '; curl -s -o /dev/null -w '%{http_code}' -H 'Sec-Fetch-Site: same-origin' -H 'Origin: http://127.0.0.1:8765' http://127.0.0.1:8765/api/ops/info; echo
  echo -n 'GET /api/ops/info from a local program (no browser headers; trusted since 23.3.34, as every SkyGuard API) -> HTTP '; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/ops/info; echo
  echo -n 'GET / (dashboard) -> HTTP '; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/; echo
  kill %1; wait 2>/dev/null || true"

say "Patch: rollback, then apply again (offline)"
offline "python3 $P/install_patch.py rollback --app inst/app --data inst/data --skip-running-check"
grep __version__ inst/app/qa_platform/__init__.py
offline "python3 $P/install_patch.py apply --app inst/app --data inst/data --skip-running-check" | tail -2

say "Extension package: offline runtimes"
unzip -l "$E/offline/python.3.13.17.nupkg" | grep -E "tools/python(w)?\.exe$"
unzip -l "$E/offline/llama-b10549-bin-win-cpu-x64.zip" | grep -E "llama-server\.exe$"
echo "wheels: $(ls "$E/offline/wheels" | wc -l)"
say "Extension package: Faster-Whisper wheels resolve offline with --require-hashes for win_amd64 / Python 3.13"
offline "python3 -m pip install --no-index --find-links $E/offline/wheels --require-hashes --only-binary=:all: --no-deps \
  --platform win_amd64 --python-version 3.13 --implementation cp --target $WORK/fw-lib --disable-pip-version-check \
  -r $E/offline/fw-requirements.txt" | tail -1
ls "$WORK/fw-lib" | grep -E "^(faster_whisper|ctranslate2|onnxruntime|av|tokenizers|numpy)$" | tr '\n' ' '; echo

say "Extension package: companion from the extracted package (offline, the installer's layout)"
H="$WORK/agent-home"; mkdir -p "$H"; cp -r "$E/companion" "$H/app"; cp -r "$E/extension" "$H/extension"
offline "export SKYGUARD_AGENT_HOME=$H; mkdir -p $H/data; echo '{\"port\": 18790}' > $H/data/config.json
  python3 -E -s $H/app/companion_main.py version
  python3 -E -s $H/app/companion_main.py pair | grep -c 'Pairing code' | sed 's/^/pairing code shown: /'
  python3 -E -s $H/app/companion_main.py check > $WORK/check.json || true
  python3 -c \"import json; r = json.load(open('$WORK/check.json')); [print(' ', 'OK  ' if c['ok'] else ('MISSING' if c['required'] else 'absent (optional)'), c['check'], '-', c.get('detail', '')) for c in r['checks']]\"
  python3 -E -s $H/app/companion_main.py serve > $WORK/companion.out 2>&1 &
  for i in \$(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:18790/health && break; sleep 1; done
  echo -n 'GET /health -> '; curl -s http://127.0.0.1:18790/health; echo
  echo -n 'GET /v1/hello from a web page origin -> HTTP '; curl -s -o /dev/null -w '%{http_code}' -H 'Origin: https://evil.example' http://127.0.0.1:18790/v1/hello; echo
  echo -n 'GET /v1/hello with a foreign Host (DNS rebinding) -> HTTP '; curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example:18790' -H 'Origin: chrome-extension://fmiiaecpdgbhhhbbkkfhnjfkbmllladb' http://127.0.0.1:18790/v1/hello; echo
  echo -n 'GET /v1/hello from the extension origin -> '; curl -s -H 'Origin: chrome-extension://fmiiaecpdgbhhhbbkkfhnjfkbmllladb' http://127.0.0.1:18790/v1/hello; echo
  echo -n 'GET /v1/applications without pairing -> HTTP '; curl -s -o /dev/null -w '%{http_code}' -H 'Origin: chrome-extension://fmiiaecpdgbhhhbbkkfhnjfkbmllladb' http://127.0.0.1:18790/v1/applications; echo
  python3 -E -s $H/app/companion_main.py status
  kill %1; wait 2>/dev/null || true"
say "DONE"
