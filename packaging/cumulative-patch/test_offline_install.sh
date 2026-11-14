#!/usr/bin/env bash
# Offline installation check of the 23.6.1 cumulative patch, run with networking disabled (a new network namespace
# with only loopback) and as an ordinary user (no root, no administrator). Linux stand-in for a Windows 11 PC: the
# PowerShell wrappers are not run here, the Python installer they call is.
# Usage: packaging/cumulative-patch/test_offline_install.sh <folder with the zip> <work folder>
set -euo pipefail
ZIPS=$(cd "$1" && pwd); WORK=$2; REPO=$(cd "$(dirname "$0")/../.." && pwd)
P=SkyGuard_Guided_Playback_Components_Fix_Patch_23.6.1
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"
LO_UP="import fcntl, socket, struct; s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); fcntl.ioctl(s.fileno(), 0x8914, struct.pack('16sH14x', b'lo', 0x49))"
offline() { unshare -rn bash -c "python3 -c \"$LO_UP\" && $*"; }
say() { printf '\n== %s\n' "$*"; }

say "Network check inside the namespace"
offline "python3 -c \"import urllib.request; urllib.request.urlopen('https://pypi.org', timeout=5)\" 2>/dev/null && echo 'INTERNET REACHABLE' || echo 'no internet (as intended)'"

say "Zip checksum"
(cd "$ZIPS" && sha256sum -c "$P.zip.sha256")

say "Extract and check every file against SHA256_CHECKSUM.txt"
unzip -q "$ZIPS/$P.zip"
(cd "$P" && sha256sum -c --quiet SHA256_CHECKSUM.txt && echo "$P: $(wc -l < SHA256_CHECKSUM.txt) files OK")

for V in 23.3.35:6d448fa 23.4.0:53e80093fe99272f27dc424392d1fd3d746b3c7c 23.5.0:900e4e0 23.6.0:c229f6b; do
  VER=${V%%:*}; COMMIT=${V#*:}; I="$WORK/inst-$VER"
  say "SkyGuard QA $VER installation (git archive of the release commit) with a database"
  mkdir -p "$I/app" "$I/data"
  git -C "$REPO" archive "$COMMIT" qa_platform web $( [ "$VER" = "23.5.0" ] || [ "$VER" = "23.6.0" ] && echo skyguard_agent ) | tar -x -C "$I/app"
  grep __version__ "$I/app/qa_platform/__init__.py"
  (cd "$I/app" && QA_HOME="$I/data" python3 -c "import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; from qa_platform.storage.db import Database; d = Database(Paths().ensure().db_file); print('schema', d.query_one('SELECT MAX(version) AS v FROM schema_version')['v']); d.close()")
  say "$VER: apply (offline)"
  offline "python3 $P/install_patch.py apply --app $I/app --data $I/data --skip-running-check" | sed "s#$WORK#<work>#g"
  say "$VER: verify (offline)"
  offline "python3 $P/install_patch.py verify --app $I/app --data $I/data" | sed "s#$WORK#<work>#g"
done

I="$WORK/inst-23.3.35"
say "Start the patched SkyGuard (offline) and call it"
offline "cd $I/app && QA_HOME=$I/data python3 -c 'import sys; sys.path.insert(0, \".\"); from qa_platform.main import run; run()' > $WORK/skyguard.log 2>&1 &
  for i in \$(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8765/api/health && break; sleep 1; done
  echo -n 'GET /api/health -> '; curl -s http://127.0.0.1:8765/api/health | sed 's#$WORK#<work>#g'; echo
  echo -n 'GET / (dashboard) -> HTTP '; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/; echo
  echo -n 'GET /api/automation/scripts/FWS-B211AC4E14 -> '; curl -s http://127.0.0.1:8765/api/automation/scripts/FWS-B211AC4E14; echo
  kill %1; wait 2>/dev/null || true"

say "Rollback to 23.3.35, then apply again (offline)"
offline "python3 $P/install_patch.py rollback --app $I/app --data $I/data --skip-running-check" | sed "s#$WORK#<work>#g"
grep __version__ "$I/app/qa_platform/__init__.py"
offline "python3 $P/install_patch.py apply --app $I/app --data $I/data --skip-running-check" | tail -2 | sed "s#$WORK#<work>#g"
say "No administrator rights: a fresh 23.4.0 installation owned by an ordinary user (nobody), patched as that user"
if [ "$(id -u)" = "0" ] && command -v setpriv >/dev/null; then
  U="$WORK/inst-user"; mkdir -p "$U/app" "$U/data"
  git -C "$REPO" archive 53e80093fe99272f27dc424392d1fd3d746b3c7c qa_platform web | tar -x -C "$U/app"
  chown -R nobody "$U"; chmod -R a+rX "$WORK/$P"; chmod a+x "$WORK"
  setpriv --reuid=nobody --regid=nogroup --clear-groups env HOME="$U" QA_HOME="$U/data" bash -c "id -un; cd $U/app && python3 -c \"import sys; sys.path.insert(0, '.'); from qa_platform.config import Paths; from qa_platform.storage.db import Database; Database(Paths().ensure().db_file).close()\" && python3 $WORK/$P/install_patch.py apply --app $U/app --data $U/data --skip-running-check | tail -2" | sed "s#$WORK#<work>#g"
else
  echo "already an ordinary user: $(id -un)"
fi
say "DONE"
