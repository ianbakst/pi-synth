#!/usr/bin/env python3
"""
MIDI Instrument — entry point.

Run with: taskset -c 0,1 uv run python src/main.py
"""

import os
import sys

import pygame

from config import cfg
from ui import VoiceSwitcherUI


def main():
    os.makedirs(cfg.paths.soundfont_dir, exist_ok=True)

    ui = VoiceSwitcherUI()
    try:
        ui.run()
    except KeyboardInterrupt:
        ui.synth.stop()
        pygame.quit()
        sys.exit(0)


if __name__ == "__main__":
    main()