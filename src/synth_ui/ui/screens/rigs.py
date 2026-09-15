"""RigsScreen — the instrument's main screen.

Rigs, not voices, are what you pick: a rig is a whole sound (instrument +
effects + level), which is what you actually reach for when playing. The voice
catalog is now one level down, reached through "New" when building a rig.

Header actions: New (build a rig from a voice), Edit (this rig's effects),
Audio (output device). USB lives on the voice picker, since importing
soundfonts is about the catalog, not about rigs.
"""

import threading
from collections.abc import Callable

import pygame

from synth_ui.clients.rig import Rig
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

# The volume fader runs down the right edge instead of across the bottom. It
# costs 96px of width once, rather than 80px of height on every screen, and a
# vertical fader is the shape the gesture already has on a real instrument.
FADER_W = 96


class RigsScreen(Screen):
    def __init__(
        self,
        rigs: list[Rig],
        on_load_rig: Callable[[Rig], bool],
        on_remove_rig: Callable[[Rig], None],
        on_edit_rig: Callable[[Rig], None],
        on_new: Callable,
        on_edit: Callable,
        on_audio: Callable,
        on_gain_change: Callable[[float], None],
        on_save: Callable[[str], None],
        on_reorder: Callable[[int, int], None] | None = None,
        effect_names: dict[str, str] | None = None,
        unavailable: Callable[[Rig], str] | None = None,
        initial_name: str | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        self._on_load_rig = on_load_rig
        self._on_save = on_save
        self._active_rig: Rig | None = None

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            action_label="New",
            on_action=on_new,
            action2_label="Edit",
            on_action2=on_edit,
            action3_label="Audio",
            on_action3=on_audio,
            on_prev=self.select_previous,
            on_next=self.select_next,
        )
        self._rigs = rigs
        self._effect_names = effect_names or {}
        self._unavailable = unavailable or (lambda _r: "")
        self._on_remove_rig = on_remove_rig
        self._on_edit_rig = on_edit_rig
        self._on_reorder = on_reorder

        # Two across: each pad is ~340px, big enough to hit without looking,
        # which is the whole job of this screen while playing.
        self.grid = TileGrid(
            rect=pygame.Rect(
                0, HEADER_H, SCREEN_W - FADER_W, SCREEN_H - HEADER_H
            ),
            tiles=self._tiles(),
            columns=2,
            row_height=112,
            font_title=font_medium,
            font_small=font_small,
            on_select=self._select_rig,
            on_long_press=self._on_edit_rig,
            on_reorder=self._reorder,
        )
        self.volume_slider = Slider(
            rect=pygame.Rect(SCREEN_W - FADER_W, HEADER_H, FADER_W,
                             SCREEN_H - HEADER_H),
            initial_value=initial_gain,
            on_change=on_gain_change,
            min_value=0.0,
            max_value=MAX_GAIN,
            label="Vol",
            font=font_small,
            orientation=VerticalOrientation(),
        )
        self.components = (self.header, self.grid, self.volume_slider)

        if initial_name is not None:
            threading.Thread(
                target=self._restore, args=(initial_name,), daemon=True
            ).start()

    def _tiles(self) -> list[Tile]:
        """One pad per rig: name, the instrument under it, and the chain as
        chips so the signal path reads at a glance rather than as prose."""
        tiles = []
        for rig in self._rigs:
            reason = self._unavailable(rig)
            tiles.append(
                Tile(
                    title=rig.name,
                    subtitle=reason or rig.voice,
                    chips=[
                        self._effect_names.get(e.uri, "?") for e in rig.effects
                    ],
                    active=(
                        self._active_rig is not None
                        and rig.name == self._active_rig.name
                    ),
                    disabled=bool(reason),
                    payload=rig,
                )
            )
        return tiles

    def _reorder(self, source: int, target: int) -> None:
        """Persist a drag. Order is performance order — the sequence that
        next/previous and a footswitch step through — so it has to survive a
        restart, not just a redraw."""
        if self._on_reorder is not None:
            self._on_reorder(source, target)

    def select_next(self) -> None:
        self._step(1)

    def select_previous(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> None:
        """Move to the adjacent rig, skipping any that can't load.

        Skipping rather than stopping: a rig whose plugin is missing is still
        in the list, and a footswitch that lands on it would leave you with
        silence and no way to tell why.
        """
        usable = [r for r in self._rigs if not self._reason(r)]
        if not usable:
            return
        names = [r.name for r in usable]
        current = self._active_rig.name if self._active_rig else None
        index = names.index(current) if current in names else -delta % len(names)
        self._select_rig(usable[(index + delta) % len(usable)])

    @property
    def active_rig(self) -> Rig | None:
        return self._active_rig

    def _select_rig(self, rig: Rig) -> None:
        self._on_rig_select(self._rigs.index(rig), rig)

    def _on_rig_select(self, index: int, rig: Rig) -> None:
        self._active_rig = rig
        self.grid.tiles = self._tiles()
        self.header.name = rig.name
        self.set_loading(True)
        threading.Thread(target=self._do_load, args=(rig,), daemon=True).start()

    def _do_load(self, rig: Rig) -> None:
        try:
            self.header.error = not self._on_load_rig(rig)
        finally:
            self.set_loading(False)

    def _restore(self, name: str) -> None:
        """Load the rig from last time, or — if it's gone or unusable — the first
        one that works, so the unit always boots into something playable."""
        rigs = self._rigs
        rig = next((r for r in rigs if r.name == name and not self._reason(r)), None)
        if rig is None:
            rig = next((r for r in rigs if not self._reason(r)), None)
        if rig is None:
            return
        self._active_rig = rig
        self.header.name = rig.name
        self.grid.tiles = self._tiles()
        self.set_loading(True)
        try:
            self._on_load_rig(rig)
        finally:
            self.set_loading(False)

    def _reason(self, rig: Rig) -> str:
        return self._unavailable(rig)

    def refresh(self, rigs: list[Rig]) -> None:
        """Re-render after the library changed (rig added, removed, edited)."""
        self._rigs = rigs
        if self._active_rig is not None:
            if self._active_rig.name not in [r.name for r in rigs]:
                self._active_rig = None
                self.header.name = None
        self.grid.tiles = self._tiles()

    def save(self) -> None:
        if self._active_rig is not None:
            self._on_save(self._active_rig.name)
