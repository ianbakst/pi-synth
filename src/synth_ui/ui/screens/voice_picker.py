"""VoicePickerScreen — the instrument catalog, one level down from rigs.

Was the home screen. Now it's reached from RigsScreen's "New": you pick an
instrument and get a rig built around it, which you then stack effects on.

Voices that can't be used on this unit are greyed with the reason (see
clients/voice.py validate). USB soundfont import lives here rather than on the
rig screen, because it changes the catalog.
"""

import threading
from collections.abc import Callable

import pygame

from synth_ui.clients.voice import Voice
from synth_ui.config import (
    HEADER_H,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
    TRIMS_FILE,
    VOICES_MANIFEST,
)
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.voice_list import VoiceList
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.utils import load_voices


class VoicePickerScreen(Screen):
    def __init__(
        self,
        on_pick: Callable[[Voice], None],
        on_back: Callable,
        on_usb: Callable,
    ):
        self._on_pick = on_pick

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="USB",
            on_action=on_usb,
        )
        self.header.name = "New Rig"

        self.voice_list = VoiceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            voices=load_voices(VOICES_MANIFEST, SOUNDFONT_DIR, TRIMS_FILE),
            font_medium=font_medium,
            font_small=font_small,
            on_select=self._on_voice_select,
        )
        self.components = (self.header, self.voice_list)

    def _on_voice_select(self, index: int, voice: Voice) -> None:
        self.voice_list.selected_index = index
        self.set_loading(True)
        # Creating the rig loads its instrument, which can take seconds for a
        # large sample library — off the UI thread.
        threading.Thread(target=self._do_pick, args=(voice,), daemon=True).start()

    def _do_pick(self, voice: Voice) -> None:
        try:
            self._on_pick(voice)
        finally:
            self.set_loading(False)

    def refresh(self) -> None:
        """Reload the catalog (e.g. after a USB soundfont copy)."""
        self.voice_list.voices = load_voices(VOICES_MANIFEST, SOUNDFONT_DIR, TRIMS_FILE)
