# MIDI Instrument — Project Specification & Rebuild Instructions

## IMPORTANT: Read this entire document before making any changes.

This document describes a working MIDI instrument built on a Raspberry Pi 4. A previous Claude session broke the project. This document provides everything needed to rebuild it correctly.

## Architecture

There are THREE independent components that must run as separate processes:

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│   FluidSynth        │     │   MIDI Auto-Connect  │     │   Python UI         │
│   (audio engine)    │     │   (udev/script)      │     │   (touchscreen)     │
│                     │     │                      │     │                     │
│   Cores 2-3         │◄────│   Runs on plug       │     │   Cores 0-1         │
│   RT priority 80    │     │   events             │     │   Normal priority   │
│   TCP shell :9800   │     │                      │     │   Connects via TCP  │
│   systemd service   │     │                      │     │   systemd service   │
│                     │     │                      │     │                     │
│   NEVER depends on  │     │   NEVER depends on   │     │   CAN crash without │
│   Python UI         │     │   Python UI          │     │   affecting audio   │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
```

**Critical design rule:** FluidSynth must run completely independently of the Python UI. If Python crashes, audio continues playing. The Python UI is ONLY a controller that sends TCP commands to FluidSynth. It never starts, stops, or manages the FluidSynth process.

## Hardware

- **SBC:** Raspberry Pi 4, 64-bit Pi OS Lite, custom PREEMPT_RT kernel (6.12.x)
- **DAC:** Teyleten Robot PCM5102A I2S DAC
  - BCK → GPIO18 (pin 12)
  - DIN → GPIO21 (pin 40)
  - LCK → GPIO19 (pin 35)
  - VIN → 5V (pin 2)
  - GND → pin 6
  - SCK pad shorted to ground
  - Uses `dtoverlay=hifiberry-dac`
  - ALSA device: `hw:sndrpihifiberry` (by name, not number — number can shift at boot)
- **Display:** 3.5" touchscreen, 800×480, ft5x06 controller at `/dev/input/event4`
- **MIDI:** USB MIDI keyboard, ALSA sequencer client (number varies)
- **User account:** `synth`

## Boot Configuration

### /boot/firmware/config.txt (additions):
```
dtparam=i2s=on
dtoverlay=hifiberry-dac
camera_auto_detect=0
dtoverlay=disable-bt
dtparam=audio=off
```

NOTE: `display_auto_detect=0` was removed because it disabled the touchscreen.

### /boot/firmware/cmdline.txt (appended to existing line):
```
isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
```

## System Tuning Already Applied

These are already configured on the Pi and should NOT be changed:

- PREEMPT_RT kernel built from `rpi-6.12.y` branch with `bcm2711_rt_defconfig`
- CPU governor locked to `performance` via systemd service
- IRQ affinity pinned to cores 0-1 via systemd service
- Swap disabled
- `/etc/security/limits.conf` has `@audio - rtprio 99` and `@audio - memlock unlimited`
- User `synth` is in `audio` group
- Unnecessary services disabled (bluetooth, avahi, cron, timers, etc.)

## Project Structure

```
synth/
├── pyproject.toml
├── README.md
├── CLAUDE.md                 # This file
├── provision.sh
├── deploy.sh
├── apt-requirements.txt
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── main.py               # UI entry point
│   ├── config.py             # All settings and constants
│   ├── synth_client.py       # TCP client to FluidSynth (NO process management)
│   └── ui.py                 # Pygame touchscreen UI
├── scripts/
│   └── midi-connect.sh       # Auto-connects MIDI devices to FluidSynth
├── tests/
│   ├── __init__.py
│   └── test_synth_client.py
├── soundfonts/
│   └── .gitkeep
└── systemd/
    ├── fluidsynth-engine.service   # Audio engine (independent)
    ├── synth-ui.service  # Python UI (can crash safely)
    └── cpu-performance.service     # CPU governor + IRQ affinity
```

## File Contents

### systemd/fluidsynth-engine.service

This is the audio engine. It runs forever, restarts if it dies, and has NOTHING to do with Python.

```ini
[Unit]
Description=FluidSynth Audio Engine
After=sound.target

[Service]
Type=simple
User=synth
ExecStart=/usr/bin/chrt -f 80 /usr/bin/taskset -c 2,3 \
    /usr/bin/fluidsynth \
    -a alsa \
    -o audio.alsa.device=hw:2 \
    -o audio.period-size=128 \
    -o audio.periods=2 \
    -o synth.sample-rate=48000 \
    -o synth.gain=2.0 \
    -o shell.port=9800 \
    -m alsa_seq \
    -s \
    /home/synth/soundfonts/default.sf2
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

IMPORTANT: There is a default FluidSynth user service that ships with Pi OS that MUST be disabled first: `systemctl --user disable --now fluidsynth.service`

### systemd/synth-ui.service

The touchscreen UI. Depends on FluidSynth being running. Can crash and restart without affecting audio.

```ini
[Unit]
Description=MIDI Instrument UI
After=fluidsynth-engine.service
Requires=fluidsynth-engine.service

[Service]
Type=simple
User=synth
WorkingDirectory=/home/$USER/synth/src
ExecStart=/usr/bin/taskset -c 0,1 /usr/bin/python3 main.py
Restart=always
RestartSec=2
Environment=SDL_FBDEV=/dev/fb0
Environment=SDL_MOUSEDEV=/dev/input/event4
Environment=SDL_MOUSEDRV=TSLIB

[Install]
WantedBy=multi-user.target
```

### systemd/cpu-performance.service

Already installed on the Pi. Sets CPU governor and IRQ affinity at boot.

```ini
[Unit]
Description=Set CPU governor and IRQ affinity
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor && for irq in $(ls /proc/irq/); do echo 3 > /proc/irq/$irq/smp_affinity 2>/dev/null; done'

[Install]
WantedBy=multi-user.target
```

### scripts/midi-connect.sh

Finds all MIDI devices and connects them to FluidSynth. Run manually or triggered by udev when a USB MIDI device is plugged in.

```bash
#!/bin/bash
# Wait for FluidSynth to be ready
sleep 1

FLUID_CLIENT=$(aconnect -l | grep -i "FLUID" | head -1 | sed 's/client \([0-9]*\).*/\1/')

if [ -z "$FLUID_CLIENT" ]; then
    echo "FluidSynth not found in ALSA sequencer"
    exit 1
fi

aconnect -l | grep "^client" | while read line; do
    CLIENT_NUM=$(echo "$line" | sed 's/client \([0-9]*\).*/\1/')
    CLIENT_NAME=$(echo "$line" | sed "s/client [0-9]*: '\(.*\)'.*/\1/")

    if [ "$CLIENT_NUM" -gt 15 ] && [ "$CLIENT_NUM" != "$FLUID_CLIENT" ]; then
        echo "Connecting $CLIENT_NAME ($CLIENT_NUM:0) to FluidSynth ($FLUID_CLIENT:0)"
        aconnect $CLIENT_NUM:0 $FLUID_CLIENT:0
    fi
done
```

### src/config.py

```python
"""
Hardware and application configuration.
Edit these values to match your setup.
"""

import os

# --- Environment detection ---
IS_PI = os.path.exists("/sys/firmware/devicetree/base/model")

# --- Paths ---
SOUNDFONT_DIR = os.path.expanduser("~/soundfonts")
STATE_FILE = os.path.expanduser("~/.synth-state")

# --- FluidSynth TCP connection ---
FLUIDSYNTH_HOST = "127.0.0.1"
FLUIDSYNTH_PORT = 9800

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
```

### src/synth_client.py

This is ONLY a TCP client. It does NOT start, stop, or manage FluidSynth. It sends commands to an already-running FluidSynth server.

```python
"""
TCP client for communicating with a running FluidSynth server.

This module does NOT manage the FluidSynth process. FluidSynth runs
as an independent systemd service. This client connects to its TCP
shell (port 9800) to send commands like loading SoundFonts and
changing gain.

If the connection drops, it reconnects automatically on the next command.
"""

import socket
import sys

from config import FLUIDSYNTH_HOST, FLUIDSYNTH_PORT


class FluidSynthClient:
    """Persistent TCP connection to a running FluidSynth server."""

    def __init__(self, host=FLUIDSYNTH_HOST, port=FLUIDSYNTH_PORT, timeout=2):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        """Establish connection to FluidSynth."""
        self.close()
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            self.sock.recv(4096)  # consume welcome banner
            return True
        except Exception as e:
            print(f"FluidSynth connect error: {e}", file=sys.stderr)
            self.sock = None
            return False

    def send(self, cmd):
        """Send a command, reconnecting if needed. Returns response or None."""
        if not self.sock:
            if not self.connect():
                return None
        try:
            self.sock.sendall((cmd + "\n").encode())
            return self.sock.recv(4096).decode()
        except Exception:
            self.sock = None
            return None

    def close(self):
        """Close the connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class FluidSynthController:
    """High-level interface for controlling FluidSynth."""

    def __init__(self):
        self.client = FluidSynthClient()
        self.current_font = None
        self.gain = 2.0

    def load_soundfont(self, path):
        """Hot-swap the SoundFont in the running FluidSynth."""
        response = self.client.send(f"load {path}")
        if response is not None:
            self.client.send("select 0 1 0 0")
            self.client.send("reset")
            self.current_font = path
            return True
        return False

    def set_gain(self, gain):
        """Change volume instantly."""
        self.gain = gain
        self.client.send(f"gain {gain}")

    def is_connected(self):
        """Check if we can talk to FluidSynth."""
        return self.client.send("fonts") is not None
```

### src/ui.py

Pygame touchscreen UI. Renders to framebuffer on Pi, windowed on dev machine. Uses finger events (FINGERDOWN, FINGERMOTION, FINGERUP) for touch input — these have normalized 0.0-1.0 coordinates that must be multiplied by screen dimensions.

```python
"""
Touchscreen UI for voice selection and volume control.

Renders directly to the framebuffer via Pygame on the Pi.
Runs windowed on a dev machine for testing.

Touch events use normalized 0.0-1.0 coordinates (FINGERDOWN,
FINGERMOTION, FINGERUP), not pixel coordinates. Multiply by
SCREEN_W/SCREEN_H to convert.
"""

import glob
import os

import pygame

from config import (
    BG, BTN_ACTIVE, BTN_H, BTN_MARGIN, BTN_NORMAL, BTN_PAD_X,
    DEFAULT_GAIN, DIVIDER, FOOTER_H, FRAMEBUFFER, HEADER_H, IS_PI,
    MAX_GAIN, PANEL_BG, SCREEN_H, SCREEN_W, SCROLL_BAR_W,
    SLIDER_BG, SLIDER_FILL, SLIDER_KNOB, SOUNDFONT_DIR, STATE_FILE,
    STATUS_ERR, STATUS_OK, TEXT_ACTIVE, TEXT_PRIMARY, TEXT_SECONDARY,
    TOUCH_DEVICE,
)
from synth_client import FluidSynthController


LIST_TOP = HEADER_H
LIST_BOTTOM = SCREEN_H - FOOTER_H
VISIBLE_AREA_H = LIST_BOTTOM - LIST_TOP


def scan_soundfonts(directory):
    """Recursively find all .sf2 and .sf3 files."""
    fonts = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        fonts.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
    fonts.sort(key=lambda f: os.path.basename(f).lower())
    return fonts


def display_name(path):
    """Clean up a SoundFont filename for display."""
    name = os.path.basename(path)
    name = os.path.splitext(name)[0]
    name = name.replace("_", " ").replace("-", " ")
    while "  " in name:
        name = name.replace("  ", " ")
    return name.strip()


def file_size_str(path):
    """Return human-readable file size."""
    size = os.path.getsize(path)
    if size < 1024 * 1024:
        return f"{size // 1024} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class VoiceSwitcherUI:
    """Touchscreen UI for voice selection."""

    def __init__(self):
        if IS_PI:
            os.environ["SDL_FBDEV"] = FRAMEBUFFER
            os.environ["SDL_MOUSEDEV"] = TOUCH_DEVICE
            os.environ["SDL_MOUSEDRV"] = "TSLIB"

        pygame.init()

        if IS_PI:
            self.screen = pygame.display.set_mode(
                (SCREEN_W, SCREEN_H), pygame.FULLSCREEN
            )
            pygame.mouse.set_visible(False)
        else:
            self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        self.font_large = pygame.font.Font(None, 36)
        self.font_medium = pygame.font.Font(None, 28)
        self.font_small = pygame.font.Font(None, 22)

        self.soundfonts = scan_soundfonts(SOUNDFONT_DIR)
        self.selected_index = -1
        self.scroll_offset = 0
        self.gain = DEFAULT_GAIN
        self.dragging_slider = False
        self.running = True
        self.slider_rect = pygame.Rect(0, 0, 0, 0)

        self.synth = FluidSynthController()

        self._load_state()

    def _load_state(self):
        """Restore last selected SoundFont."""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    last_font = f.read().strip()
                if last_font in self.soundfonts:
                    self.selected_index = self.soundfonts.index(last_font)
                    self.synth.load_soundfont(last_font)
            except Exception:
                pass

    def _save_state(self):
        """Save current selection."""
        if 0 <= self.selected_index < len(self.soundfonts):
            try:
                with open(STATE_FILE, "w") as f:
                    f.write(self.soundfonts[self.selected_index])
            except Exception:
                pass

    def _draw_header(self):
        """Draw the top bar with current voice name."""
        pygame.draw.rect(self.screen, PANEL_BG, (0, 0, SCREEN_W, HEADER_H))
        pygame.draw.line(
            self.screen, DIVIDER, (0, HEADER_H - 1), (SCREEN_W, HEADER_H - 1)
        )

        if 0 <= self.selected_index < len(self.soundfonts):
            name = display_name(self.soundfonts[self.selected_index])
            color = TEXT_ACTIVE
            status_color = STATUS_OK if self.synth.is_connected() else STATUS_ERR
            pygame.draw.circle(self.screen, status_color, (20, HEADER_H // 2), 6)
        else:
            name = "No voice selected"
            color = TEXT_SECONDARY

        text = self.font_large.render(name, True, color)
        max_w = SCREEN_W - 50
        if text.get_width() > max_w:
            while text.get_width() > max_w and len(name) > 3:
                name = name[:-4] + "..."
                text = self.font_large.render(name, True, color)
        self.screen.blit(text, (36, (HEADER_H - text.get_height()) // 2))

    def _draw_voice_list(self):
        """Draw the scrollable list of SoundFonts."""
        list_rect = pygame.Rect(0, LIST_TOP, SCREEN_W - SCROLL_BAR_W, VISIBLE_AREA_H)
        pygame.draw.rect(self.screen, BG, (0, LIST_TOP, SCREEN_W, VISIBLE_AREA_H))

        if not self.soundfonts:
            text = self.font_medium.render(
                "No SoundFonts found in ~/soundfonts/", True, TEXT_SECONDARY
            )
            self.screen.blit(text, (20, LIST_TOP + 20))
            return

        total_h = len(self.soundfonts) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - VISIBLE_AREA_H)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = self.screen.subsurface(list_rect)

        for i, sf_path in enumerate(self.soundfonts):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)

            if btn_y + BTN_H < 0 or btn_y > VISIBLE_AREA_H:
                continue

            color = BTN_ACTIVE if i == self.selected_index else BTN_NORMAL
            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, color, btn_rect, border_radius=6)

            name = display_name(sf_path)
            text_color = TEXT_ACTIVE if i == self.selected_index else TEXT_PRIMARY
            text = self.font_medium.render(name, True, text_color)
            max_text_w = btn_rect.width - 100
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, text_color)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            size_str = file_size_str(sf_path)
            size_text = self.font_small.render(size_str, True, TEXT_SECONDARY)
            clip.blit(size_text, (btn_rect.x + 12, btn_rect.y + 36))

        if total_h > VISIBLE_AREA_H and max_scroll > 0:
            bar_x = SCREEN_W - SCROLL_BAR_W
            bar_h = max(30, int(VISIBLE_AREA_H * VISIBLE_AREA_H / total_h))
            bar_y = LIST_TOP + int(
                self.scroll_offset / max_scroll * (VISIBLE_AREA_H - bar_h)
            )
            pygame.draw.rect(
                self.screen, SLIDER_BG,
                (bar_x, LIST_TOP, SCROLL_BAR_W, VISIBLE_AREA_H),
            )
            pygame.draw.rect(
                self.screen, SLIDER_FILL,
                (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    def _draw_footer(self):
        """Draw volume slider."""
        footer_y = SCREEN_H - FOOTER_H
        pygame.draw.rect(self.screen, PANEL_BG, (0, footer_y, SCREEN_W, FOOTER_H))
        pygame.draw.line(self.screen, DIVIDER, (0, footer_y), (SCREEN_W, footer_y))

        vol_label = self.font_small.render("Volume", True, TEXT_SECONDARY)
        self.screen.blit(vol_label, (16, footer_y + 10))

        vol_pct = int(self.gain / MAX_GAIN * 100)
        vol_val = self.font_small.render(f"{vol_pct}%", True, TEXT_PRIMARY)
        self.screen.blit(vol_val, (SCREEN_W - vol_val.get_width() - 16, footer_y + 10))

        self.slider_rect = pygame.Rect(16, footer_y + 38, SCREEN_W - 32, 24)
        pygame.draw.rect(self.screen, SLIDER_BG, self.slider_rect, border_radius=12)

        fill_w = int((self.gain / MAX_GAIN) * self.slider_rect.width)
        fill_rect = pygame.Rect(
            self.slider_rect.x, self.slider_rect.y, fill_w, self.slider_rect.height
        )
        pygame.draw.rect(self.screen, SLIDER_FILL, fill_rect, border_radius=12)

        knob_x = self.slider_rect.x + fill_w
        knob_y = self.slider_rect.y + self.slider_rect.height // 2
        pygame.draw.circle(self.screen, SLIDER_KNOB, (knob_x, knob_y), 14)

    def _handle_list_tap(self, x, y):
        """Handle a tap in the voice list area."""
        if not self.soundfonts:
            return
        relative_y = y - LIST_TOP + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if 0 <= index < len(self.soundfonts):
            if index != self.selected_index:
                self.selected_index = index
                self.synth.load_soundfont(self.soundfonts[index])
                self._save_state()

    def _handle_slider(self, x):
        """Handle volume slider interaction."""
        relative_x = x - self.slider_rect.x
        ratio = max(0.0, min(1.0, relative_x / self.slider_rect.width))
        self.gain = ratio * MAX_GAIN
        self.synth.set_gain(self.gain)

    def run(self):
        """Main event loop."""
        clock = pygame.time.Clock()
        finger_moved = False

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False

                # --- Touch events (Pi touchscreen) ---
                elif event.type == pygame.FINGERDOWN:
                    finger_moved = False
                    x = int(event.x * SCREEN_W)
                    y = int(event.y * SCREEN_H)
                    if self.slider_rect.collidepoint(x, y):
                        self.dragging_slider = True
                        self._handle_slider(x)

                elif event.type == pygame.FINGERMOTION:
                    x = int(event.x * SCREEN_W)
                    y = int(event.y * SCREEN_H)
                    if self.dragging_slider:
                        self._handle_slider(x)
                    else:
                        dy = event.dy * SCREEN_H
                        if abs(dy) > 2:
                            finger_moved = True
                            self.scroll_offset -= int(dy)

                elif event.type == pygame.FINGERUP:
                    x = int(event.x * SCREEN_W)
                    y = int(event.y * SCREEN_H)
                    if self.dragging_slider:
                        self.dragging_slider = False
                    elif not finger_moved and LIST_TOP <= y < LIST_BOTTOM:
                        self._handle_list_tap(x, y)
                    finger_moved = False

                # --- Mouse events (dev machine / SSH testing) ---
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    x, y = event.pos
                    if self.slider_rect.collidepoint(x, y):
                        self.dragging_slider = True
                        self._handle_slider(x)
                    elif LIST_TOP <= y < LIST_BOTTOM:
                        self._handle_list_tap(x, y)

                elif event.type == pygame.MOUSEBUTTONUP:
                    if self.dragging_slider:
                        self.dragging_slider = False

                elif event.type == pygame.MOUSEMOTION:
                    if self.dragging_slider:
                        self._handle_slider(event.pos[0])

                elif event.type == pygame.MOUSEWHEEL:
                    self.scroll_offset -= event.y * 40

            self.screen.fill(BG)
            self._draw_header()
            self._draw_voice_list()
            self._draw_footer()
            pygame.display.flip()

            clock.tick(30)

        pygame.quit()
```

### src/main.py

```python
#!/usr/bin/env python3
"""
MIDI Instrument — entry point.
Run on Pi with: taskset -c 0,1 python3 src/main.py
"""

import os
import sys

import pygame

from config import SOUNDFONT_DIR
from ui import VoiceSwitcherUI


def main():
    os.makedirs(SOUNDFONT_DIR, exist_ok=True)
    ui = VoiceSwitcherUI()
    try:
        ui.run()
    except KeyboardInterrupt:
        pygame.quit()
        sys.exit(0)


if __name__ == "__main__":
    main()
```

### pyproject.toml

```toml
[project]
name = "synth"
version = "0.1.0"
description = "A dedicated MIDI instrument built on Raspberry Pi with FluidSynth"
requires-python = ">=3.11"
dependencies = [
    "pygame",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "ruff",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 88
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "I", "W"]
```

### apt-requirements.txt

```
python3-pygame
python3-pytest
fluidsynth
```

## Service Installation Instructions

Run these on the Pi:

```bash
# 1. Disable the default FluidSynth user service (Pi OS ships one with bad settings)
systemctl --user disable --now fluidsynth.service

# 2. Copy service files
sudo cp systemd/fluidsynth-engine.service /etc/systemd/system/
sudo cp systemd/synth-ui.service /etc/systemd/system/
sudo cp systemd/cpu-performance.service /etc/systemd/system/

# 3. Reload systemd
sudo systemctl daemon-reload

# 4. Enable services (they will start on boot)
sudo systemctl enable cpu-performance.service
sudo systemctl enable fluidsynth-engine.service
sudo systemctl enable synth-ui.service

# 5. Start them now
sudo systemctl start cpu-performance.service
sudo systemctl start fluidsynth-engine.service
sudo systemctl start synth-ui.service

# 6. Check status
systemctl status fluidsynth-engine.service
systemctl status synth-ui.service
```

IMPORTANT: `fluidsynth-engine.service` needs a default SoundFont at `/home/synth/soundfonts/default.sf2`. Create a symlink to whichever SoundFont you want as default:

```bash
ln -s /home/synth/soundfonts/GeneralUser-GS.sf2 /home/synth/soundfonts/default.sf2
```

## MIDI Auto-Connection

After FluidSynth starts, MIDI devices need to be connected to it via `aconnect`. Run the script manually:

```bash
bash scripts/midi-connect.sh
```

Or set up a udev rule to run it automatically when a USB MIDI device is plugged in:

```bash
sudo tee /etc/udev/rules.d/99-midi-connect.rules << 'EOF'
ACTION=="add", SUBSYSTEM=="sound", KERNEL=="midi*", RUN+="/home/$USER/synth/scripts/midi-connect.sh"
EOF
sudo udevadm control --reload-rules
```

## Development Workflow

### Dev machine:
```bash
uv sync
uv run python src/main.py         # windowed mode, stub audio
uv run pytest
uv run ruff check src/ tests/
./deploy.sh                        # deploy to Pi
```

### Deploy script (deploy.sh):
```bash
#!/bin/bash
set -e
PI_USER="${PI_USER:-synth}"
PI_HOST="${1:-${PI_HOST:-raspberrypi.local}}"
PI="$PI_USER@$PI_HOST"
PROJECT="/home/$PI_USER/synth"

echo "Deploying to $PI:$PROJECT"
rsync -avz --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'soundfonts/*.sf2' --exclude 'soundfonts/*.sf3' \
    ./ "$PI:$PROJECT/"

ssh "$PI" "sudo apt-get update -qq && xargs sudo apt-get install -y -qq < $PROJECT/apt-requirements.txt"
ssh "$PI" "cd $PROJECT && python3 -m pytest tests/ -v && echo 'ALL TESTS PASSED'"
ssh "$PI" "sudo systemctl restart synth-ui.service"
echo "Deploy complete."
```

## Things That MUST NOT Change

1. FluidSynth runs as its own systemd service, NOT managed by Python
2. FluidSynth runs on cores 2-3 with RT priority 80
3. Python UI runs on cores 0-1 with normal priority
4. Communication between UI and FluidSynth is TCP only (port 9800)
5. Touch events are FINGERDOWN/FINGERMOTION/FINGERUP with normalized coordinates
6. The audio device is hw:sndrpihifiberry (by name — never by number, which can shift at boot)
7. The touchscreen is /dev/input/event4

## Known Issues

- Period size 64 causes occasional dropouts; 128 is stable (~5ms latency)
- `display_auto_detect=0` disables the touchscreen — don't add it back
- The Pi OS default FluidSynth user service must be disabled or it blocks port 9800 and the audio device
- `bcm2835-audio` VCHI errors in dmesg are harmless
- Disabling wpa_supplicant kills WiFi SSH — keep it during development
