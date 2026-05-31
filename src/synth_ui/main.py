#!/usr/bin/env python3
"""
MIDI Instrument — entry point.
Run on Pi with: taskset -c 0,1 python3 src/main.py
"""

import os
import sys

import pygame

from synth_ui.config import SOUNDFONT_DIR
from synth_ui.ui import SynthUI


def main():
    os.makedirs(SOUNDFONT_DIR, exist_ok=True)
    ui = SynthUI()
    try:
        ui.run()
    except KeyboardInterrupt:
        pygame.quit()
        sys.exit(0)


if __name__ == "__main__":
    main()
