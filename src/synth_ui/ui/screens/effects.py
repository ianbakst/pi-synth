from collections.abc import Callable

import pygame

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.config import HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.effects_list import EffectsCatalogList, RackEffectsList
from synth_ui.ui.components.header import Header
from synth_ui.ui.screens.base import Screen


class EffectsScreen(Screen):
    """The loaded effects chain, with an "Add" action into the catalog."""

    def __init__(
        self,
        effects: list[Effect],
        catalog: list[EffectCatalogEntry],
        on_remove: Callable[[int], None],
        on_add: Callable,
        on_back: Callable,
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
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            effects=effects,
            catalog=catalog,
            font_medium=font_medium,
            font_small=font_small,
            on_remove=on_remove,
        )
        self.components = (self.header, self.rack_list)


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
