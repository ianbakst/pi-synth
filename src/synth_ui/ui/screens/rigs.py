"""RigsScreen — the rigs of one set: what you pick during a song.

Rigs, not voices, are what you pick: a rig is a whole sound (instrument +
effects + level), which is what you actually reach for when playing. The voice
catalog is one level down, reached through "New" when building a rig.

**This screen shows one set's rigs, never the whole store.** That is the point
of sets: four rigs for a song means four pads here, and next/previous — on the
steppers or on a footswitch — walks those four. `_step` iterates whatever list
this screen holds, so scoping the list is the entire mechanism.

Header actions: Back (to the sets screen), New (build a rig from a voice), Edit
(this rig's chain). Audio lives on the sets screen: Back plus the step buttons
plus three actions does not fit across 800px. USB lives on the voice picker,
since importing soundfonts is about the catalog, not about rigs.

The **active rig** — what is loaded and making sound — is held here, and the
rest of the app reads it (`app._sync_active_rig`, the chain and params screens).
Swapping the shown list with `set_rigs` deliberately leaves it alone: showing
and playing are separate, so a set that turns out to be empty or unplayable
doesn't cut the sound. Only a rig genuinely going away clears it, which is
`rig_removed`'s job.
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
        on_edit_rig: Callable[[Rig], None],
        on_new: Callable,
        on_edit: Callable,
        on_back: Callable,
        on_gain_change: Callable[[float], None],
        on_reorder: Callable[[int, int], None] | None = None,
        effect_names: dict[str, str] | None = None,
        unavailable: Callable[[Rig], str] | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        self._on_load_rig = on_load_rig
        self._active_rig: Rig | None = None

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="New",
            on_action=on_new,
            action2_label="Edit",
            on_action2=on_edit,
            on_prev=self.select_previous,
            on_next=self.select_next,
        )
        self._rigs = rigs
        self._effect_names = effect_names or {}
        self._unavailable = unavailable or (lambda _r: "")
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
                        and rig.id == self._active_rig.id
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
        ids = [r.id for r in usable]
        current = self._active_rig.id if self._active_rig else None
        # Not in this set — you stepped after browsing elsewhere — so start from
        # the end the step is coming from rather than refusing to move.
        index = ids.index(current) if current in ids else -delta % len(ids)
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

    # `select` and `load_first` load *inline* rather than on a thread of their
    # own, because their callers are already off the UI thread and go on to do
    # more mod-host work — warming the rest of the set — the moment they return.
    # Loading on a second thread made those two overlap, and since every
    # instrument, effect and master-chain command shares one socket, the
    # commands and their replies interleaved: params landing on instances that
    # weren't loaded yet, and replies arriving against the wrong command.

    def select(self, rig: Rig) -> None:
        """Load a rig chosen from outside — restoring on boot, or the first rig
        of a set just entered. Blocks until it is loaded."""
        self._active_rig = rig
        self.grid.tiles = self._tiles()
        self.header.name = rig.name
        self.set_loading(True)
        self._do_load(rig)

    def load_first(self) -> Rig | None:
        """Load the first rig that can actually play, skipping any whose voice
        is missing. Returns what it chose, or None for a set that is empty or
        entirely unusable — in which case whatever was playing keeps playing.
        Blocks until it is loaded."""
        rig = next((r for r in self._rigs if not self._reason(r)), None)
        if rig is not None:
            self.select(rig)
        return rig

    def _reason(self, rig: Rig) -> str:
        return self._unavailable(rig)

    def set_rigs(self, rigs: list[Rig], title: str = "") -> None:
        """Show a different set's rigs, or re-render this one after an edit.

        Changing what's *shown* never changes what's *playing* — this loads and
        unloads nothing. Whether to start a rig is the caller's decision
        (`app._enter_set` makes it for a set you weren't already in), which is
        what leaves the sound alone when the set turns out to be empty or
        entirely unplayable. A rig that has genuinely gone is `rig_removed`.
        """
        self._rigs = rigs
        if title:
            self.header.name = title
        self.grid.tiles = self._tiles()

    def rig_removed(self, rig_id: str) -> None:
        """A rig was deleted. If it was the one playing, stop claiming to be on
        it — this is the one case where the active rig is cleared, and it is
        deletion rather than "not in the list I was just handed"."""
        if self._active_rig is not None and self._active_rig.id == rig_id:
            self._active_rig = None
        self.grid.tiles = self._tiles()
