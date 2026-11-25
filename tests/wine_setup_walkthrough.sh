#!/usr/bin/env bash
# Walks through the Setup wizard under Wine (Xvfb), page by page, with screenshots. Used to produce the release
# evidence of the wizard; the real Windows run is the CI job "setup-exe-windows". Not part of pytest.
#
#   tests/wine_setup_walkthrough.sh <Setup.exe> <installation folder (Windows path)> <screenshot folder> [extra args]
#
# Needs: wine, Xvfb, xdotool, ImageMagick (import), and an installation whose runtime\python\python.exe runs in Wine.
set -u
SETUP="$1"; APPDIR="$2"; SHOTS="$3"; shift 3
mkdir -p "$SHOTS"
export WINEDEBUG=-all PYTHONLEGACYWINDOWSSTDIO=1 DISPLAY=:57
Xvfb :57 -screen 0 1100x800x24 >/dev/null 2>&1 &
XPID=$!
sleep 2
# Windows NumPy (the OCR check) crashes under Wine; without this, Wine's crash window waits for a click and holds the
# install until its time limit. Real Windows has no such window for it (NumPy starts there).
wine reg add "HKCU\\Software\\Wine\\WineDbg" /v ShowCrashDialog /t REG_DWORD /d 0 /f >/dev/null 2>&1
wine "$SETUP" "/APP=$APPDIR" /NOSTART "$@" >/dev/null 2>&1 &
WPID=$!

win() { xdotool search --name "SkyGuard QA" 2>/dev/null | tail -1; }
shot() { sleep "${2:-1}"; import -window root "$SHOTS/$1.png"; echo "shot $1"; }
next() { local w; w=$(win); xdotool windowactivate --sync "$w" >/dev/null 2>&1; xdotool key --window "$w" Return; }

for i in $(seq 1 60); do [ -n "$(win)" ] && break; sleep 1; done
shot 1-welcome 3
next; shot 2-detect 8
next; shot 3-backup 4
next; shot 4-install-running 6
# wait for the install to finish
UAPP=$(winepath -u "$APPDIR" 2>/dev/null)
for i in $(seq 1 300); do
  sleep 3
  # the Setup log (UTF-16, next to "app" or in the app folder): the install verifies once, then the Verify page
  # verifies again; both end with "Patch files present". A failed install ends with "PATCH FAILED" instead.
  LOGTXT=$(cat "$UAPP"/../logs/setup-*.txt "$UAPP"/logs/setup-*.txt 2>/dev/null | tr -d '\000')
  if [ "$(grep -c "Patch files present" <<<"$LOGTXT")" -ge 2 ] || grep -qs "PATCH FAILED" <<<"$LOGTXT"; then
    sleep 10; break
  fi
  [ "$i" -eq 40 ] && shot 4b-install-progress 0
done
# the install page moves on by itself to Verify when it ends
shot 5-verify 4
next; shot 6-finish 4
next; sleep 3
kill $WPID $XPID 2>/dev/null
wait $WPID 2>/dev/null
echo "walkthrough done"
