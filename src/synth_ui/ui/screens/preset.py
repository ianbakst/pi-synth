from collections.abc import Callable

import pygame

from synth_ui.clients import Preset
from synth_ui.config import FOOTER_H, HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.preset_list import PresetList
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.screens.base import Screen


class PresetScreen(Screen):
    def __init__(
        self,
        font_name: str,
        presets: list[Preset],
        on_select: Callable[[Preset], None],
        on_back: Callable,
        on_gain_change: Callable[[float], None],
        initial_gain: float,
        max_gain: float,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
        )
        self.header.name = font_name

        self.preset_list = PresetList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            presets=presets,
            font_medium=font_medium,
            font_small=font_small,
            on_select=on_select,
        )

        self.volume_slider = Slider(
            rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
            initial_value=initial_gain,
            on_change=on_gain_change,
            min_value=0.0,
            max_value=max_gain,
            label="Volume",
            font=font_small,
        )

        self.components = (self.header, self.preset_list, self.volume_slider)

    def set_selected(self, preset: Preset) -> None:
        try:
            self.preset_list.selected_index = self.preset_list.presets.index(preset)
        except ValueError:
            pass
