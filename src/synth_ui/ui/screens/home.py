import threading
from collections.abc import Callable

import pygame

from synth_ui.clients import Preset
from synth_ui.clients.voice import Voice
from synth_ui.config import (
    DEFAULT_GAIN,
    FOOTER_H,
    HEADER_H,
    MAX_GAIN,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
    VOICES_MANIFEST,
)
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.components.voice_list import VoiceList
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.utils import load_voices


class HomeScreen(Screen):
    def __init__(
        self,
        on_load_voice: Callable[[Voice], bool],
        on_list_presets: Callable[[], list[Preset]],
        on_navigate: Callable[[Voice, list[Preset]], None],
        on_gain_change: Callable[[float], None],
        on_save: Callable[[str], None],
        on_usb: Callable,
        initial_name: str | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        self._on_load_voice = on_load_voice
        self._on_list_presets = on_list_presets
        self._on_navigate = on_navigate
        self._on_save = on_save
        self._selected_voice: Voice | None = None

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            action_label="USB",
            on_action=on_usb,
        )
        voices = load_voices(VOICES_MANIFEST, SOUNDFONT_DIR)
        self.voice_list = VoiceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            voices=voices,
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

        if initial_name is not None:
            threading.Thread(target=self._restore, args=(initial_name,), daemon=True).start()

    def _on_voice_select(self, index: int, voice: Voice) -> None:
        self._selected_voice = voice
        self.voice_list.selected_index = index
        self.header.name = voice.name
        self.set_loading(True)
        threading.Thread(target=self._do_load, args=(voice,), daemon=True).start()

    def _do_load(self, voice: Voice) -> None:
        try:
            ok = self._on_load_voice(voice)
            self.header.error = not ok
            if ok:
                presets = self._on_list_presets()
                self._on_navigate(voice, presets)
        finally:
            self.set_loading(False)

    def _restore(self, name: str) -> None:
        voice = next((v for v in self.voice_list.voices if v.name == name), None)
        if voice is None:
            return
        index = self.voice_list.voices.index(voice)
        self.voice_list.selected_index = index
        self.header.name = voice.name
        self._selected_voice = voice
        self.set_loading(True)
        try:
            self._on_load_voice(voice)
        finally:
            self.set_loading(False)

    def refresh(self) -> None:
        """Reload voices from manifest (e.g. after a USB SF2 copy)."""
        self.voice_list.voices = load_voices(VOICES_MANIFEST, SOUNDFONT_DIR)
        if self._selected_voice is not None:
            names = [v.name for v in self.voice_list.voices]
            self.voice_list.selected_index = (
                names.index(self._selected_voice.name)
                if self._selected_voice.name in names
                else -1
            )

    def save(self) -> None:
        if self._selected_voice is not None:
            self._on_save(self._selected_voice.name)
