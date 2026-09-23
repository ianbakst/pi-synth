"""SetsScreen — the instrument's home screen.

A set is one song's worth of rigs. This is what you're looking at between songs;
the rigs screen is what you're looking at during one.

**Entering a set you are not already in makes it active and loads its first
usable rig**, so walking into a song leaves you ready to play. Entering the set
you're already in changes nothing — you keep whatever you were on, which is the
case that matters when you come back from editing a chain. Coming back out to
this screen never changes what's playing either: browsing is not switching.

Header actions: New (a set), Audio (output device). Audio lives here rather than
on the rigs screen because that one needs its Back arrow and the step buttons,
and four controls plus a name do not fit across 800px.
"""

from collections.abc import Callable

import pygame

from synth_ui.clients.set import SongSet
from synth_ui.config import (
    DEFAULT_GAIN,
    HEADER_H,
    MAX_GAIN,
    SCREEN_H,
    SCREEN_W,
)
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.orient import VerticalOrientation
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.components.tile_grid import Tile, TileGrid
from synth_ui.ui.screens.base import Screen

# Matches the rigs screen: the volume fader runs down the right edge, so it is
# in the same place whichever screen you are on.
FADER_W = 96


class SetsScreen(Screen):
    def __init__(
        self,
        sets: list[SongSet],
        on_enter_set: Callable[[SongSet], None],
        on_edit_set: Callable[[SongSet], None],
        on_new: Callable,
        on_audio: Callable,
        on_gain_change: Callable[[float], None],
        on_reorder: Callable[[int, int], None] | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self._sets = sets
        self._active_set: SongSet | None = None
        self._on_enter_set = on_enter_set

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            action_label="New",
            on_action=on_new,
            action2_label="Audio",
            on_action2=on_audio,
        )
        self.header.name = "Sets"

        self.grid = TileGrid(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W - FADER_W, SCREEN_H - HEADER_H),
            tiles=self._tiles(),
            columns=2,
            row_height=112,
            font_title=font_medium,
            font_small=font_small,
            on_select=self._enter,
            on_long_press=on_edit_set,
            on_reorder=on_reorder or (lambda a, b: None),
        )
        self.volume_slider = Slider(
            rect=pygame.Rect(
                SCREEN_W - FADER_W, HEADER_H, FADER_W, SCREEN_H - HEADER_H
            ),
            initial_value=initial_gain,
            on_change=on_gain_change,
            min_value=0.0,
            max_value=MAX_GAIN,
            label="Vol",
            font=font_small,
            orientation=VerticalOrientation(),
        )
        self.components = (self.header, self.grid, self.volume_slider)

    def _tiles(self) -> list[Tile]:
        """One pad per set: its name, and how many rigs are in it. The count is
        what tells you at a glance whether you're looking at the song you meant
        — names alone blur together in a set list."""
        return [
            Tile(
                title=song_set.name,
                subtitle=_rig_count(len(song_set.rigs)),
                active=(
                    self._active_set is not None
                    and song_set.id == self._active_set.id
                ),
                payload=song_set,
            )
            for song_set in self._sets
        ]

    def _enter(self, song_set: SongSet) -> None:
        self._on_enter_set(song_set)

    @property
    def active_set(self) -> SongSet | None:
        return self._active_set

    def set_active(self, song_set: SongSet | None) -> None:
        self._active_set = song_set
        self.grid.tiles = self._tiles()

    def refresh(self, sets: list[SongSet]) -> None:
        """Re-render after the library changed (set added, renamed, deleted)."""
        self._sets = sets
        if self._active_set is not None:
            if self._active_set.id not in [s.id for s in sets]:
                self._active_set = None
        self.grid.tiles = self._tiles()


def _rig_count(count: int) -> str:
    return "1 rig" if count == 1 else f"{count} rigs"
