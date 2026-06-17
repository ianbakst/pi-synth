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
STATE_FILE = os.path.expanduser("~/.synth-state")
ENGINE_MANAGER_SCRIPT = os.path.expanduser("~/synth/scripts/engine-manager.sh")
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

# --- Colors ---
BG = (20, 20, 25)
PANEL_BG = (30, 30, 38)
BTN_NORMAL = (45, 45, 55)
BTN_ACTIVE = (60, 130, 180)
TEXT_PRIMARY = (240, 240, 245)
TEXT_SECONDARY = (160, 160, 170)
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
