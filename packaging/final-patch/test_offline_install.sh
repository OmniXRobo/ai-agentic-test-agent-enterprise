#!/bin/bash
# Offline installation test of the 23.4.0 patch on a simulated 23.3.35 installation.
SP="${WORK:-/tmp/qa-patch-test}"   # needs $SP/build/QA_Enterprise_AI_Final_Upgrade_Patch.zip (build_patch.py --out $SP/build)
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
[ -n "${VENV:-}" ] && source "$VENV/bin/activate"
set -u
APP=$SP/sim/AIAgenticTestAgent
rm -rf $SP/sim $SP/extract; mkdir -p $APP/app $APP/data $SP/extract
(cd $REPO && git archive 6d448fa qa_platform web docs START_QA_PLATFORM.cmd requirements.txt) | tar -x -C $APP/app
echo '{"version":"23.3.35"}' > $APP/app/INSTALLED.json
echo '{"server":{"port":8799}}' > $APP/data/settings.json
(cd $APP/app && QA_HOME=$APP/data python -E -s -c "
import sys; sys.path.insert(0,'.')
import qa_platform; from qa_platform.config import Paths; from qa_platform.storage.db import Database
p=Paths().ensure(); d=Database(p.db_file)
d.execute(\"INSERT INTO app_credentials (id, application, role, username, updated_at) VALUES ('C1','Shop','admin','alice','2026-11-01')\")
d.execute(\"INSERT INTO app_credentials (id, application, role, username, updated_at) VALUES ('C2','Portal','default','qa.tester','2026-11-01')\")
print('Simulated install:', qa_platform.__version__, 'schema', d.query_one('SELECT MAX(version) v FROM schema_version')['v'], 'credentials', d.query_one('SELECT COUNT(*) n FROM app_credentials')['n'])")
python -I -c "import zipfile; zipfile.ZipFile('$SP/build/QA_Enterprise_AI_Final_Upgrade_Patch.zip').extractall('$SP/extract')"
P=$SP/extract/QA_Enterprise_AI_Final_Upgrade_Patch
snap() { (cd $APP/app && find qa_platform web -type f -not -path '*__pycache__*' -exec sha256sum {} + | sort -k2 | sha256sum | cut -c1-16); }
BASE=$(snap)
echo "Baseline file tree hash: $BASE"
echo; echo "### T1 SHA256_CHECKSUM.txt"; (cd $P && sha256sum -c SHA256_CHECKSUM.txt --quiet && echo "PASS: every file matches")
echo; echo "### T2 wrong baseline is refused"
sed -i 's/23.3.35/23.3.30/' $APP/app/qa_platform/__init__.py
python -I $P/install_patch.py apply --app $APP 2>&1 | tail -2; echo "exit $?"
sed -i 's/23.3.30/23.3.35/' $APP/app/qa_platform/__init__.py
echo; echo "### T3 tampered patch file is refused"
cp $P/app/qa_platform/__init__.py $SP/init.bak; echo "# changed" >> $P/app/qa_platform/__init__.py
python -I $P/install_patch.py apply --app $APP 2>&1 | tail -2
cp $SP/init.bak $P/app/qa_platform/__init__.py
echo; echo "### T4 application still running is refused"
python -I -m http.server 8799 --bind 127.0.0.1 >/dev/null 2>&1 & SRV=$!; sleep 1
python -I $P/install_patch.py apply --app $APP 2>&1 | tail -2
kill $SRV; wait $SRV 2>/dev/null
echo; echo "### T5 failure after copying rolls back automatically"
python -I $P/install_patch.py apply --app $APP --fail-after-copy 2>&1 | tail -5
echo "file tree after automatic rollback: $(snap) (baseline $BASE)"; [ "$(snap)" = "$BASE" ] && echo "PASS: identical to 23.3.35"
echo; echo "### T6 apply"
python -I $P/install_patch.py apply --app $APP --python $(which python) 2>&1 | sed -n '/== Verifying/,$p'
echo; echo "### T7 the patched application starts and serves /api/health"
(cd $APP/app && QA_EXTRA_HOSTS=testserver QA_HOME=$APP/data timeout 60 python -E -s -c "
import sys; sys.path.insert(0,'.')
from fastapi.testclient import TestClient
from qa_platform.main import create_app
from qa_platform.config import Paths
from qa_platform.credentials import MemoryBackend
with TestClient(create_app(Paths().ensure(), secret_backend=MemoryBackend())) as c:
    h = c.get('/api/health').json(); print('health', h.get('ok'), h.get('version'))
    creds = c.get('/api/automation/credentials').json()['items']
    print('credentials kept:', sorted(x['username'] for x in creds), 'auth_method', sorted({x['auth_method'] for x in creds}))
    print('comparison projects API:', c.get('/api/comparison/projects').status_code, '| AURA status:', c.get('/api/aura/status').status_code)
" 2>&1 | grep -v '^{' | tail -4)
echo; echo "### T8 verify"
python -I $P/install_patch.py verify --app $APP --python $(which python) 2>&1 | tail -9
echo; echo "### T9 rollback (database kept)"
python -I $P/install_patch.py rollback --app $APP 2>&1 | tail -3
echo "file tree after rollback: $(snap) (baseline $BASE)"; [ "$(snap)" = "$BASE" ] && echo "PASS: identical to 23.3.35"
(cd $APP/app && QA_HOME=$APP/data python -E -s -c "
import sys; sys.path.insert(0,'.')
import qa_platform; from qa_platform.config import Paths; from qa_platform.storage.db import Database
d=Database(Paths().db_file)
print('23.3.35 opens the kept database:', qa_platform.__version__, 'schema', d.query_one('SELECT MAX(version) v FROM schema_version')['v'], 'credentials', d.query_one('SELECT COUNT(*) n FROM app_credentials')['n'])")
echo; echo "### T10 apply again after the rollback"
python -I $P/install_patch.py apply --app $APP --python $(which python) 2>&1 | tail -2
echo; echo "### T11 rollback with --restore-database"
python -I $P/install_patch.py rollback --app $APP --restore-database 2>&1 | tail -4
ls $APP/data | grep sqlite3
