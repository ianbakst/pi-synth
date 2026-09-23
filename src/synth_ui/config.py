"""
Hardware and application configuration.
Edit these values to match your setup.
"""

import os

# --- Environment detection ---
IS_PI = os.path.exists("/sys/firmware/devicetree/base/model")

# --- Paths ---
SOUNDFONT_DIR = os.path.expanduser("~/soundfonts")
INSTRUMENTS_DIR = os.path.expanduser("~/instruments")
VOICES_MANIFEST = os.path.join(INSTRUMENTS_DIR, "voices.json")
EFFECTS_MANIFEST = os.path.join(INSTRUMENTS_DIR, "effects.json")
STATE_FILE = os.path.expanduser("~/.synth-state")
# Saved sets, each holding its own rigs (instrument + effects chain + level).
# Unlike voices.json — a read-only catalog shipped in the image — this is the
# user's own work, created on the device, so it lives in $HOME and is written
# atomically.
SETS_FILE = os.path.expanduser("~/.synth-sets.json")
# The pre-sets rig store. Read once, to migrate its rigs into a set; never
# written again. Left in place afterwards, which makes it that migration's
# backup. See clients/set.py.
RIGS_FILE = os.path.expanduser("~/.synth-rigs.json")
# Per-voice level trims measured by tools/calibrate_levels on THIS board. In
# $HOME for the same reason as the rigs: deploy.sh overwrites the shipped
# voices.json, and most voices (the split GM set) have no manifest entry to
# write a number into anyway. See clients/trims.py.
TRIMS_FILE = os.path.expanduser("~/.synth-trims.json")
# Screen brightness, 0.0-1.0 of the panel's range. Per-unit like the rest of the
# $HOME state: it depends on where the instrument is played, not on the build.
BRIGHTNESS_FILE = os.path.expanduser("~/.synth-brightness")
DEFAULT_BRIGHTNESS = 1.0

# --- Hands-free rig switching (clients/midi_control.py) ---
# Control Change numbers a footswitch sends to step through rigs. 80 and 81 are
# in the "general purpose / undefined" block of the MIDI spec, so they won't
# collide with sustain (64), expression (11) or anything the Roland sends on its
# own. Most footswitches let you set the CC they transmit.
#
# Rig order is the order on the rigs screen, which is drag-to-reorder — so the
# pedal walks the set list in the sequence you arranged.
MIDI_NEXT_RIG_CC = 80
MIDI_PREV_RIG_CC = 81
# Program Change selects a rig by position (0 = first). Off by default: the
# Roland sends Program Change when its own tones are changed from the panel,
# which would yank the rig out from under you mid-song.
MIDI_PROGRAM_SELECTS_RIG = False

# Selected ALSA card id (e.g. "sndrpihifiberry"). The UI writes it; scripts/
# start-jack.sh reads it to pick JACK's device. Absent = auto-detect (default).
AUDIO_DEVICE_FILE = os.path.expanduser("~/.synth-audio-device")

# Voice a fresh card's first rig is built on, so the unit plays on boot with no
# touchscreen interaction. Must be a voice the IMAGE guarantees — not one that
# depends on anything done after flashing:
#   - not a split GM font: splitting runs on the board, after first boot
#   - not a sample library: none ship in the image
# mda EPiano comes from mda-lv2 (00-packages), needs no instrument file, and is
# resident. It was "General MIDI" on fluidsynth until those hand-written GM
# voices were replaced by discovered, split fonts.
DEFAULT_VOICE = "Rhodes EP"
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
IMAGES_DIR = os.path.join(ASSETS_DIR, "images")

# --- mod-host TCP connection ---
MOD_HOST_PORT = 5555

# --- Display ---
SCREEN_W = 800
SCREEN_H = 480
FRAMEBUFFER = "/dev/fb0"
TOUCH_DEVICE = "/dev/input/event4"

# --- Gain ---
# Volume slider range. Its top is 0 dB on the master chain; see
# engine_manager._gain_to_db for the taper.
MAX_GAIN = 5.0
# Boot volume: ~-6 dB, leaving headroom. Was 2.0, which under the old mapping
# meant +6 dB post-limiter — clipping from the first note.
DEFAULT_GAIN = 3.5

# --- Master chain (permanent tail of the signal path; see clients/master_chain.py) ---
# Every voice and every effect feeds through this, so it's where per-voice level
# trim is applied and where a limiter protects the DAC.
#
# Confirmed on hardware with `verify_voices --inspect` (Calf Limiter, calf.lv2).
# All of Calf's gain controls are LINEAR multipliers, not decibels — hence unit.
#
# Gain staging: per-voice trim goes in *before* the limiter (level_in) so a hot
# voice is actually limited, and the user's volume goes *after* it (level_out)
# so turning down doesn't change how the limiter behaves. They're two different
# jobs and Calf gives us a port for each.
# --- Fixed velocity (per rig; see clients/velocity_filter.py) ---
# What a rig with `fixed_velocity` set sends for every note-on, ignoring how
# hard the key was actually struck. 100 rather than 127: it sits in the upper
# part of most sampled instruments' velocity layers without pinning them to the
# hardest, brightest one, which on a piano library is a hammer strike and on an
# organ is indistinguishable from any other value.
FIXED_VELOCITY = 100

MASTER_LIMITER_URI = "http://calf.sourceforge.net/plugins/Limiter"
MASTER_CHAIN: list[dict] = [
    {
        "uri": MASTER_LIMITER_URI,
        "params": {
            "limit": 0.89,       # ceiling, linear: ~ -1 dBFS
            # OFF. Calf ships this ON: it auto-compensates gain so perceived
            # loudness stays constant, which is exactly the automatic leveling
            # we rejected — it would silently undo the per-voice trim and
            # flatten playing dynamics.
            "auto_level": 0,
            # Lookahead, ms. Calf defaults to 5, which the plugin does NOT
            # report as latency ("Has latency: no") — so it would quietly add
            # ~5ms to key-to-sound on top of JACK's own buffer. 1ms is ample
            # for a safety limiter that should rarely engage.
            "attack": 1.0,
            "oversampling": 1,   # 4x costs CPU on the RT path for no audible gain here
        },
        "trim_symbol": "level_in",     # pre-limiter: per-voice level match
        "volume_symbol": "level_out",  # post-limiter: the user's volume
        "unit": "linear",
        # Port bounds (1/64 .. 64). Values outside these are invalid, so the
        # silence floor has to clamp here rather than send 0.
        "minimum": 0.015625,
        "maximum": 64.0,
    },
]


# --- Colors ---
BG = (20, 20, 25)
PANEL_BG = (30, 30, 38)
BTN_NORMAL = (45, 45, 55)
BTN_ACTIVE = (60, 130, 180)
# Voices whose plugin or instrument file isn't present on this unit.
BTN_DISABLED = (32, 32, 38)
TEXT_PRIMARY = (240, 240, 245)
TEXT_SECONDARY = (160, 160, 170)
TEXT_DISABLED = (105, 105, 115)
TEXT_ACTIVE = (255, 255, 255)
SLIDER_BG = (50, 50, 60)
SLIDER_FILL = (60, 130, 180)
SLIDER_KNOB = (200, 200, 210)
DIVIDER = (50, 50, 60)
STATUS_OK = (80, 200, 120)
STATUS_ERR = (200, 80, 80)

# --- Layout ---
HEADER_H = 60
FOOTER_H = 80
BTN_H = 64
BTN_MARGIN = 4
BTN_PAD_X = 12
SCROLL_BAR_W = 8
