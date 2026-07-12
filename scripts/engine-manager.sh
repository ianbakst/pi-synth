#!/bin/bash
# engine-manager.sh — stop the current audio engine, start the new one.
#
# Usage: engine-manager.sh <engine> [instrument-path]
#   engine: fluidsynth | sfizz | setbfree | dexed | pianoteq
#
# sfizz and dexed are loaded as LV2 plugins via mod-host by the Python layer
# after this script returns. This script only handles process lifecycle.

set -e

ENGINE="${1:?Usage: engine-manager.sh <engine> [path]}"
INSTRUMENT="$2"
SCRIPTS_DIR="$(dirname "$0")"

# --- Stop whatever is currently running ---
sudo systemctl stop fluidsynth-engine.service 2>/dev/null || true
sudo systemctl stop setbfree.service          2>/dev/null || true
killall -q -TERM pianoteq                2>/dev/null || true
sleep 0.3
killall -q -KILL pianoteq                2>/dev/null || true

# --- Start the new engine ---
case "$ENGINE" in
    fluidsynth)
        sudo systemctl start fluidsynth-engine.service
        ;;
    sfizz|dexed)
        # Loaded as LV2 via mod-host by the Python EngineManager — nothing to do here.
        ;;
    setbfree)
        sudo systemctl start setbfree.service
        ;;
    pianoteq)
        # Core 2: the instrument engine core (matches the other engines).
        chrt -f 80 taskset -c 2 \
            pianoteq --headless --midi-channel 1 &
        disown
        ;;
    *)
        echo "Unknown engine: $ENGINE" >&2
        exit 1
        ;;
esac

# --- Reconnect MIDI after engine starts ---
# Non-fatal: a missing keyboard or MIDI-routing hiccup must NOT make the engine
# switch report failure (the UI turns the voice red on any non-zero exit here).
# midi-connect.sh routes the keyboard to THIS engine's JACK MIDI input.
sleep 1.5
bash "$SCRIPTS_DIR/midi-connect.sh" "$ENGINE" || true
