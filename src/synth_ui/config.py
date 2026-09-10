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
# Saved rigs (instrument + effects chain + level). Unlike voices.json — a
# read-only catalog shipped in the image — this is the user's own work, created
# on the device, so it lives in $HOME and is written atomically.
RIGS_FILE = os.path.expanduser("~/.synth-rigs.json")

# Selected ALSA card id (e.g. "sndrpihifiberry"). The UI writes it; scripts/
# start-jack.sh reads it to pick JACK's device. Absent = auto-detect (default).
AUDIO_DEVICE_FILE = os.path.expanduser("~/.synth-audio-device")

# Voice loaded on a fresh boot when no ~/.synth-state exists yet. Must match a
# `name` in voices.json. The FluidSynth "General MIDI" voice is the safe default:
# its default.sf2 is guaranteed present in the image and (with the service's
# audio.jack.autoconnect) its audio reaches the DAC — so the unit plays on boot
# with no touchscreen interaction.
DEFAULT_VOICE = "General MIDI"
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
IMAGES_DIR = os.path.join(ASSETS_DIR, "images")

# --- FluidSynth TCP connection ---
FLUIDSYNTH_HOST = "127.0.0.1"
FLUIDSYNTH_PORT = 9800

# --- mod-host TCP connection ---
MOD_HOST_PORT = 5555

# --- Display ---
SCREEN_W = 800
SCREEN_H = 480
FRAMEBUFFER = "/dev/fb0"
TOUCH_DEVICE = "/dev/input/event4"

# --- Gain ---
DEFAULT_GAIN = 2.0
MAX_GAIN = 5.0

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

# Volume slider position (linear, 0..MAX_GAIN) that means "unity" on the master
# chain. The slider is converted to dB around this point.
UNITY_GAIN = 1.0

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
