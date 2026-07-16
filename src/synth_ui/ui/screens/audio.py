from collections.abc import Callable

import pygame

from synth_ui.clients.audio_devices import Card
from synth_ui.config import HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.audio_list import AudioDeviceList
from synth_ui.ui.components.header import Header
from synth_ui.ui.screens.base import Screen


class AudioScreen(Screen):
    """Pick which ALSA card JACK opens. Selecting one restarts the audio stack."""

    def __init__(
        self,
        cards: list[Card],
        current_id: str | None,
        on_select: Callable[[str], None],
        on_back: Callable,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
        )
        self.header.name = "Audio Output"

        self.device_list = AudioDeviceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            cards=cards,
            current_id=current_id,
            font_medium=font_medium,
            font_small=font_small,
            on_select=on_select,
        )
        self.components = (self.header, self.device_list)
