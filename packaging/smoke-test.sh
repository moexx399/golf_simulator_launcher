#!/usr/bin/env bash
# Starts a built launcher without a display and checks that the window comes up with
# its bundled example config. Nothing is booted: this never picks a simulator.
#   packaging/smoke-test.sh path/to/golf_simulator_launcher
set -euo pipefail

exe=$1
"$exe" --help > /dev/null
"$exe" --version

export QT_QPA_PLATFORM=offscreen
export HOME XDG_DATA_HOME
HOME=$(mktemp -d)
XDG_DATA_HOME=$HOME/data
"$exe" --windowed --dry-run &
pid=$!
trap 'kill "$pid" 2> /dev/null || true' EXIT

log=$XDG_DATA_HOME/golf_simulator_launcher/launcher.log
[ "$(uname)" = Darwin ] && log="$HOME/Library/Application Support/golf_simulator_launcher/launcher.log"
for _ in $(seq 1 60); do
    if grep -q 'Launcher opened' "$log" 2> /dev/null; then
        sleep 2   # a crash while painting shows up just after opening
        kill -0 "$pid"
        grep -q gspro "$(dirname "$log")/launcher.json"
        echo "launcher opened"
        exit 0
    fi
    kill -0 "$pid"   # exited early: set -e fails the test
    sleep 1
done
echo "the launcher didn't open within 60 s" >&2
exit 1
