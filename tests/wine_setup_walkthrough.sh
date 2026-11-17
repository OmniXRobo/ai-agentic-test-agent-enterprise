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
for i in $(seq 1 200); do
  sleep 3
  # the second verification (the Verify page) writes its own log as the last step of the install
  if grep -qs "Patch files present" "$(winepath -u "$APPDIR" 2>/dev/null)"/logs/*verify*; then sleep 10; break; fi
  [ "$i" -eq 40 ] && shot 4b-install-progress 0
done
# the install page moves on by itself to Verify when it ends
shot 5-verify 4
next; shot 6-finish 4
next; sleep 3
kill $WPID $XPID 2>/dev/null
wait $WPID 2>/dev/null
echo "walkthrough done"
