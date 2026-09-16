from collections.abc import Callable

import pygame

from synth_ui.clients.audio_devices import Card
from synth_ui.config import FOOTER_H, HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.audio_list import AudioDeviceList
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.screens.base import Screen


class AudioScreen(Screen):
    """Device and display settings.

    Picking a card restarts the audio stack. Brightness lives here too rather
    than on a screen of its own: it is the only other hardware setting, and one
    settings screen is easier to find than two.
    """

    def __init__(
        self,
        cards: list[Card],
        current_id: str | None,
        on_select: Callable[[str], None],
        on_back: Callable,
        on_brightness: Callable[[float], None] | None = None,
        initial_brightness: float | None = None,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
        )
        self.header.name = "Settings"

        # No backlight on this board (dev machine, HDMI build): no slider, and
        # the device list gets the space back.
        self.brightness_slider: Slider | None = None
        body_h = SCREEN_H - HEADER_H
        if on_brightness is not None and initial_brightness is not None:
            body_h -= FOOTER_H
            self.brightness_slider = Slider(
                rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
                initial_value=initial_brightness,
                on_change=on_brightness,
                min_value=0.0,
                max_value=1.0,
                label="Brightness",
                font=font_small,
                format_value=lambda v: f"{int(v * 100)}%",
                live=True,
            )

        self.device_list = AudioDeviceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, body_h),
            cards=cards,
            current_id=current_id,
            font_medium=font_medium,
            font_small=font_small,
            on_select=on_select,
        )
        self.components = tuple(
            c for c in (self.header, self.device_list, self.brightness_slider)
            if c is not None
        )
