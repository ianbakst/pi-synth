import threading
from collections.abc import Callable

import pygame

from synth_ui.clients import Preset
from synth_ui.config import (
    DEFAULT_GAIN,
    FOOTER_H,
    HEADER_H,
    MAX_GAIN,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
)
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.components.voice_list import VoiceList
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.utils import display_name, scan_soundfonts


class HomeScreen(Screen):
    def __init__(
        self,
        on_load_soundfont: Callable[[str], bool],
        on_list_presets: Callable[[], list[Preset]],
        on_navigate: Callable[[str, list[Preset]], None],
        on_gain_change: Callable[[float], None],
        on_save: Callable[[str], None],
        on_usb: Callable,
        initial_path: str | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        self._on_load_soundfont = on_load_soundfont
        self._on_list_presets = on_list_presets
        self._on_navigate = on_navigate
        self._on_save = on_save
        self._selected_path: str | None = None

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            action_label="USB",
            on_action=on_usb,
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
            initial_value=initial_gain,
            on_change=on_gain_change,
            min_value=0.0,
            max_value=MAX_GAIN,
            label="Volume",
            font=font_small,
        )
        self.components = (self.header, self.voice_list, self.volume_slider)

        if initial_path is not None:
            threading.Thread(target=self._restore, args=(initial_path,), daemon=True).start()

    def _on_voice_select(self, index: int, path: str) -> None:
        self._selected_path = path
        self.voice_list.selected_index = index
        self.header.name = display_name(path)
        self.set_loading(True)
        threading.Thread(target=self._do_load, args=(path,), daemon=True).start()

    def _do_load(self, path: str) -> None:
        try:
            ok = self._on_load_soundfont(path)
            self.header.error = not ok
            if ok:
                presets = self._on_list_presets()
                self._on_navigate(path, presets)
        finally:
            self.set_loading(False)

    def _restore(self, path: str) -> None:
        if path not in self.voice_list.soundfonts:
            return
        index = self.voice_list.soundfonts.index(path)
        self.voice_list.selected_index = index
        self.header.name = display_name(path)
        self._selected_path = path
        self.set_loading(True)
        try:
            self._on_load_soundfont(path)
        finally:
            self.set_loading(False)

    def refresh(self) -> None:
        """Re-scan the soundfont directory (e.g. after a USB copy)."""
        self.voice_list.soundfonts = scan_soundfonts(SOUNDFONT_DIR)
        if self._selected_path in self.voice_list.soundfonts:
            self.voice_list.selected_index = self.voice_list.soundfonts.index(self._selected_path)
        else:
            self.voice_list.selected_index = -1

    def save(self) -> None:
        if self._selected_path is not None:
            self._on_save(self._selected_path)
