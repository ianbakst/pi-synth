#!/bin/bash
# midi-connect.sh <engine>
#
# Route every hardware MIDI keyboard to the JACK MIDI input of the active engine.
#
# All engines consume MIDI over JACK now (fluidsynth -m jack, setBfree, mod-host
# for sfizz/dexed, pianoteq). The USB keyboard is an ALSA-seq device, bridged
# into JACK by a2jmidid.service — its ports appear under the "a2j:" client. We
# connect those bridged capture ports to the current engine's MIDI input.
#
# Runs as the 'synth' user (invoked by engine-manager.sh, which the UI spawns),
# so it shares the synth JACK server. Never fails the engine switch: a missing
# keyboard or a not-yet-registered port must not turn the voice red.

set -u
ENGINE="${1:-}"

# Wait until the JACK server answers.
for _ in $(seq 1 30); do
    jack_lsp >/dev/null 2>&1 && break
    sleep 0.1
done

# Map an engine to its JACK MIDI input port. setBfree and mod-host have stable
# names; fluidsynth/pianoteq are discovered (client name can carry a pid/suffix).
target_port() {
    case "$1" in
        setbfree)     echo "setBfree:midi_in" ;;
        sfizz|dexed)  echo "mod-host:midi_in" ;;
        fluidsynth)   jack_lsp 2>/dev/null | grep -iE '^fluidsynth.*midi' | head -1 ;;
        pianoteq)     jack_lsp 2>/dev/null | grep -iE '^Pianoteq.*midi'   | head -1 ;;
        *)            echo "" ;;
    esac
}

TARGET="$(target_port "$ENGINE")"
if [ -z "$TARGET" ]; then
    echo "midi-connect: no JACK MIDI target for engine '$ENGINE' — nothing to do" >&2
    exit 0
fi

# The engine may still be registering ports right after start — wait for it.
for _ in $(seq 1 30); do
    jack_lsp 2>/dev/null | grep -qxF "$TARGET" && break
    sleep 0.1
done

# Connect every bridged hardware keyboard (a2j capture ports) to the engine.
# read -r keeps the full port name, which contains spaces, e.g.
#   "a2j:Roland Digital Piano [20] (capture): Roland Digital Piano MIDI 1"
jack_lsp 2>/dev/null | grep -i a2j | grep -i capture | while read -r src; do
    if jack_connect "$src" "$TARGET" 2>/dev/null; then
        echo "midi-connect: connected [$src] -> $TARGET"
    fi   # already-connected / transient errors are ignored on purpose
done
exit 0
