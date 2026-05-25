import os
import threading

import pygame

from config import (
    DEFAULT_GAIN,
    FOOTER_H,
    HEADER_H,
    MAX_GAIN,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
    STATE_FILE,
)
from src.clients import FluidSynthController
from src.ui.components.header import Header
from src.ui.components.slider.slider import Slider
from src.ui.components.voice_list import VoiceList
from src.ui.screens.base import Screen
from src.ui.utils import display_name, scan_soundfonts


class HomeScreen(Screen):
    def __init__(self, synth: FluidSynthController):
        self.synth = synth

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
        )
        self.voice_list = VoiceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            soundfonts=scan_soundfonts(SOUNDFONT_DIR),
            font_medium=font_medium,
            font_small=font_small,
            on_select=self._on_voice_select,
        )
        self.volume_slider = Slider(
            rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
            initial_value=DEFAULT_GAIN,
            on_change=self.synth.set_gain,
            min_value=0.0,
            max_value=MAX_GAIN,
            label="Volume",
            font=font_small,
        )

        self.components = (self.header, self.voice_list, self.volume_slider)
        threading.Thread(target=self._load_state, daemon=True).start()

    def _set_loading(self, loading: bool) -> None:
        self.header.loading = loading
        self.voice_list.loading = loading

    def _on_voice_select(self, index: int, path: str) -> None:
        self.voice_list.selected_index = index
        self.header.name = display_name(path)
        self._save_state(path)
        self._set_loading(True)
        threading.Thread(target=self._do_load, args=(path,), daemon=True).start()

    def _do_load(self, path: str) -> None:
        try:
            self.synth.load_soundfont(path)
        finally:
            self._set_loading(False)

    def _load_state(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                path = f.read().strip()
            if path in self.voice_list.soundfonts:
                index = self.voice_list.soundfonts.index(path)
                self.voice_list.selected_index = index
                self.header.name = display_name(path)
                self._set_loading(True)
                self.synth.load_soundfont(path)
        except Exception:
            pass
        finally:
            self._set_loading(False)

    def _save_state(self, path: str) -> None:
        try:
            with open(STATE_FILE, "w") as f:
                f.write(path)
        except Exception:
            pass
