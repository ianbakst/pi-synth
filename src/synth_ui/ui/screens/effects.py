from collections.abc import Callable

import pygame

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.config import FOOTER_H, HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.effects_list import EffectsCatalogList, RackEffectsList
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.screens.base import Screen

# By-ear nudge only: the measured per-voice offset from calibrate_levels does
# the heavy lifting, so this range is deliberately narrow.
TRIM_RANGE_DB = 12.0


class EffectsScreen(Screen):
    """The active rig's chain and level — effectively the rig editor.

    Leaving the screen writes both back to the rig (app._sync_active_rig_effects),
    so there is no separate save step to forget."""

    def __init__(
        self,
        effects: list[Effect],
        catalog: list[EffectCatalogEntry],
        on_remove: Callable[[int], None],
        on_add: Callable,
        on_back: Callable,
        on_bypass: Callable[[int, bool], None] | None = None,
        on_edit: Callable[[int], None] | None = None,
        on_trim_change: Callable[[float], None] | None = None,
        initial_trim: float = 0.0,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="Add",
            on_action=on_add,
        )
        self.header.name = "Effects"

        self.rack_list = RackEffectsList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            effects=effects,
            catalog=catalog,
            font_medium=font_medium,
            font_small=font_small,
            on_remove=on_remove,
            on_bypass=on_bypass,
            on_edit=on_edit,
        )
        self.trim_slider = Slider(
            rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
            initial_value=initial_trim,
            on_change=on_trim_change or (lambda db: None),
            min_value=-TRIM_RANGE_DB,
            max_value=TRIM_RANGE_DB,
            label="Trim",
            font=font_small,
            format_value=lambda db: f"{db:+.1f} dB",
        )
        self.components = (self.header, self.rack_list, self.trim_slider)


class EffectsCatalogScreen(Screen):
    """Browse installed LV2 effects; tap one to add it to the chain."""

    def __init__(
        self,
        catalog: list[EffectCatalogEntry],
        on_select: Callable[[EffectCatalogEntry], None],
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
        self.header.name = "Add Effect"

        self.catalog_list = EffectsCatalogList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            catalog=catalog,
            font_medium=font_medium,
            font_small=font_small,
            on_select=on_select,
        )
        self.components = (self.header, self.catalog_list)
