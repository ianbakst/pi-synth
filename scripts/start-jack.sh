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
# MIDI bridge. `raw` uses jackd's own alsa_rawmidi driver, which reads the
# hardware MIDI device directly and publishes physical JACK ports for it,
# instead of going through a2jmidid and the ALSA sequencer.
#
# Currently `none`: a2jmidid is in the path, and there is no measured reason to
# change that.
#
# History, because the comment here used to claim the opposite. `raw` was tried
# against a variable seconds-long MIDI delay and made no difference. a2jmidid's
# sequencer hand-off was blamed on the strength of one reading — `aseqdump`
# looking instant while jack_midi_dump looked late — and that blame was wrong.
# The delay was the Roland FP-10 batching its USB MIDI whenever its Bluetooth
# was enabled, upstream of every piece of software here. Proven with
# tools/midi_latency.py: a Pico on the same USB controller delivered 1.0 notes
# per read while the piano delivered ~14 per read in clumps ~2s apart.
#
# So a2jmidid was never shown to add delay. `raw` is left available because one
# bridge inside jackd is still tidier than two processes, but that is a
# simplification, not a fix — measure before switching.
#
# `raw` also takes the MIDI device exclusively, which locks the ALSA sequencer
# out of it: `aseqdump` and `aconnect` cannot see the keyboard while it is set.
#
# a2jmidid MUST NOT run alongside this: it would publish the same keyboard a
# second time, and EngineManager wires every physical MIDI source to the active
# instrument — so every note would sound twice.
MIDI_DRIVER="${SYNTH_JACK_MIDI:-none}"
RATE="${SYNTH_JACK_RATE:-48000}"
PERIOD="${SYNTH_JACK_PERIOD:-128}"
NPERIODS="${SYNTH_JACK_NPERIODS:-2}"

# Core 1: JACK's ALSA backend runs alone on the first isolated core.
JACK="/usr/bin/chrt -f 90 /usr/bin/taskset -c 1 /usr/bin/jackd"

# Playback card ids (stable names), one per line, de-duplicated. The Pi's HDMI
# audio devices (vc4hdmi0/1) are excluded: this is a HifiBerry appliance, HDMI
# audio is never the target, and — critically — they're always present and
# enumerate BEFORE the I2S DAC at cold boot. Without excluding them the card-wait
# below trips on HDMI, then jackd picks it, fails to open it, and the whole audio
# stack collapses (jack exits 255 → a2jmidid/mod-host cascade-fail).
list_cards() {
    aplay -l 2>/dev/null \
        | sed -nE 's/^card [0-9]+: ([^ ]+) \[.*/\1/p' \
        | grep -v '^vc4hdmi' \
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
    exec $JACK -d alsa -d "hw:$DEVICE" -r "$RATE" -p "$PERIOD" -n "$NPERIODS" \
        -X "$MIDI_DRIVER"
else
    echo "start-jack: no ALSA playback card found — starting on the dummy backend" >&2
    exec $JACK -d dummy -r "$RATE" -p "$PERIOD"
fi
