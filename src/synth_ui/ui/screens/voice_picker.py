"""VoicePickerScreen — the instrument catalog, one level down from rigs.

Was the home screen. Now it's reached from RigsScreen's "New": you pick an
instrument and get a rig built around it, which you then stack effects on.

Laid out as a category rail plus a grid of tiles, because this is the screen
with the scale problem: once the split GM set is discovered the catalogue is
~140 voices, and as a single column of full-width rows that was two dozen
screens of scrolling to reach an organ — while the category each voice already
carries went unused.

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
from synth_ui.ui.components.category_rail import ALL, CategoryRail, categories_of
from synth_ui.ui.components.category_rail import WIDTH as RAIL_W
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.tile_grid import Tile, TileGrid
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
        self._category = ALL

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

        self._voices = load_voices(VOICES_MANIFEST, SOUNDFONT_DIR, TRIMS_FILE)
        body = pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H)

        self.rail = CategoryRail(
            rect=pygame.Rect(body.x, body.y, RAIL_W, body.height),
            categories=categories_of(self._voices, lambda v: v.category),
            selected=self._category,
            font=font_medium,
            font_small=font_small,
            on_select=self._on_category,
        )
        # Three across in the remaining ~640px: a voice tile needs only a name
        # and a category, so it can be much narrower than a rig pad.
        self.grid = TileGrid(
            rect=pygame.Rect(
                body.x + RAIL_W, body.y, body.width - RAIL_W, body.height
            ),
            tiles=self._tiles(),
            columns=3,
            row_height=76,
            font_title=font_medium,
            font_small=font_small,
            on_select=self._on_voice_select,
        )
        self.components = (self.header, self.rail, self.grid)

    # ------------------------------------------------------------------

    def _visible(self) -> list[Voice]:
        if self._category == ALL:
            return self._voices
        return [v for v in self._voices if (v.category or "Other") == self._category]

    def _tiles(self) -> list[Tile]:
        return [
            Tile(
                title=voice.name,
                # The engine name used to sit here ("modhost"), an
                # implementation detail nobody picks a sound by. The category
                # replaces it — but only in "All": once you have filtered to
                # Electric Piano, every tile repeating "Electric Piano" is
                # noise that crowds the name.
                subtitle=voice.unavailable_reason
                or ("" if self._category != ALL else voice.category),
                disabled=bool(voice.unavailable_reason),
                payload=voice,
            )
            for voice in self._visible()
        ]

    def _on_category(self, name: str) -> None:
        self._category = name
        # The rail sets this itself when tapped, but not when the category is
        # changed from code (refresh, restoring a previous selection).
        self.rail.selected = name
        self.grid.tiles = self._tiles()
        self.grid.scroll_offset = 0

    def _on_voice_select(self, voice: Voice) -> None:
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
        self._voices = load_voices(VOICES_MANIFEST, SOUNDFONT_DIR, TRIMS_FILE)
        self.rail.categories = categories_of(self._voices, lambda v: v.category)
        # A category can disappear when its last voice is deleted; fall back to
        # All rather than showing an empty grid with no way to tell why.
        if self._category not in [name for name, _ in self.rail.categories]:
            self._category = ALL
            self.rail.selected = ALL
        self.grid.tiles = self._tiles()
