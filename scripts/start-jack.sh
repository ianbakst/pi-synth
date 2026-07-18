#!/bin/bash
# start-jack.sh — pick the ALSA playback device and exec jackd on the audio core.
#
# jack.service runs this (as the 'synth' user) instead of a hardcoded device, so
# the card is selectable at runtime and the system self-recovers if the saved
# card disappears.
#
# Device precedence (mirrors AudioDevices.resolve in Python):
#   1. a valid saved selection in ~/.synth-audio-device (written by the UI)
#   2. the built-in HiFiBerry DAC
#   3. the first available playback card
#   4. jackd's 'dummy' backend — silent, but jackd still starts so the graph and
#      UI come up and the user can plug in / select a card and restart jack.
#
# The device is used by NAME (hw:<id>), never index (indices shift across boots).

set -u

DEVICE_FILE="${SYNTH_AUDIO_DEVICE_FILE:-${HOME:-/home/synth}/.synth-audio-device}"
RATE="${SYNTH_JACK_RATE:-48000}"
PERIOD="${SYNTH_JACK_PERIOD:-128}"
NPERIODS="${SYNTH_JACK_NPERIODS:-2}"

# Core 1: JACK's ALSA backend runs alone on the first isolated core.
JACK="/usr/bin/chrt -f 90 /usr/bin/taskset -c 1 /usr/bin/jackd"

# Playback card ids (stable names), one per line, de-duplicated.
list_cards() {
    aplay -l 2>/dev/null \
        | sed -nE 's/^card [0-9]+: ([^ ]+) \[.*/\1/p' \
        | awk '!seen[$0]++'
}
have_card() { list_cards | grep -qxF "$1"; }

# Wait (bounded) for a real playback card before deciding. At cold boot the I2S
# DAC (hifiberry) enumerates a beat AFTER jack.service starts, so a one-shot
# check finds nothing, falls to the silent dummy backend below, and never
# recovers — Restart=always doesn't help because dummy jackd runs happily
# forever (no crash to trigger a restart). This is the difference between "boots
# with sound" and "boots silent until a manual jack restart". Override the cap
# with SYNTH_JACK_CARD_WAIT (seconds); 0 disables the wait.
CARD_WAIT="${SYNTH_JACK_CARD_WAIT:-15}"
waited=0
while [ -z "$(list_cards)" ] && [ "$waited" -lt "$CARD_WAIT" ]; do
    sleep 1
    waited=$((waited + 1))
done
if [ "$waited" -gt 0 ]; then
    echo "start-jack: waited ${waited}s for an ALSA playback card to appear" >&2
fi

SAVED="$(tr -d '[:space:]' < "$DEVICE_FILE" 2>/dev/null || true)"

DEVICE=""
if [ -n "$SAVED" ] && have_card "$SAVED"; then
    DEVICE="$SAVED"
elif have_card "sndrpihifiberry"; then
    DEVICE="sndrpihifiberry"
else
    DEVICE="$(list_cards | head -1)"
fi

if [ -n "$DEVICE" ]; then
    echo "start-jack: using hw:$DEVICE" >&2
    exec $JACK -d alsa -d "hw:$DEVICE" -r "$RATE" -p "$PERIOD" -n "$NPERIODS"
else
    echo "start-jack: no ALSA playback card found — starting on the dummy backend" >&2
    exec $JACK -d dummy -r "$RATE" -p "$PERIOD"
fi
