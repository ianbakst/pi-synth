from collections.abc import Callable

import pygame

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.config import HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.category_rail import ALL, CategoryRail, categories_of
from synth_ui.ui.components.category_rail import WIDTH as RAIL_W
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.signal_chain import SignalChain
from synth_ui.ui.components.slider.orient import VerticalOrientation
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.components.tile_grid import Tile, TileGrid
from synth_ui.ui.screens.base import Screen

# By-ear nudge only: the measured per-voice offset from calibrate_levels does
# the heavy lifting, so this range is deliberately narrow.
TRIM_RANGE_DB = 12.0

# Matches RigsScreen: one vertical fader column on the right rather than a
# horizontal strip eating height from every screen.
FADER_W = 96


class EffectsScreen(Screen):
    """The active rig's chain and level — effectively the rig editor.

    Leaving the screen writes both back to the rig (app._sync_active_rig),
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
        on_reorder: Callable[[int, int], None] | None = None,
        on_edit_instrument: Callable | None = None,
        on_fixed_velocity: Callable[[bool], None] | None = None,
        initial_trim: float = 0.0,
        initial_fixed_velocity: bool = False,
        source_name: str = "",
        rig_name: str = "",
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        # The instrument is a block on the wire now, not a caption, so the
        # header goes back to naming the thing being edited: the rig.
        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="Add",
            on_action=on_add,
        )
        self.header.name = rig_name or "Chain"

        # Velocity is a rig setting like trim, but it has two states rather than
        # a range, so it goes in the header as a button that reads out its own
        # state instead of costing the chain another 96px fader column. Added
        # after "Add" so it renders to the left of it and the button that was
        # always rightmost stays rightmost.
        self.fixed_velocity = initial_fixed_velocity
        self._on_fixed_velocity = on_fixed_velocity
        self._velocity_action: int | None = None
        # Absent on a board without x42-plugins, where the control would be a
        # button that does nothing. See EngineManager.fixed_velocity_available.
        if on_fixed_velocity is not None:
            self._velocity_action = len(self.header.actions)
            self.header.actions.append(
                (self._velocity_label(), self._toggle_fixed_velocity)
            )

        # Trim runs down the right edge, matching the rigs screen — and giving
        # the chain the full height it needs for three rows of blocks.
        self.chain = SignalChain(
            rect=pygame.Rect(
                0, HEADER_H, SCREEN_W - FADER_W, SCREEN_H - HEADER_H
            ),
            effects=effects,
            catalog=catalog,
            font_medium=font_medium,
            font_small=font_small,
            on_edit=on_edit or (lambda i: None),
            on_bypass=on_bypass or (lambda i, b: None),
            on_add=lambda index: on_add(index),
            on_reorder=on_reorder or (lambda a, b: None),
            on_edit_instrument=on_edit_instrument,
            source_name=source_name,
        )
        self.trim_slider = Slider(
            rect=pygame.Rect(SCREEN_W - FADER_W, HEADER_H, FADER_W,
                             SCREEN_H - HEADER_H),
            initial_value=initial_trim,
            on_change=on_trim_change or (lambda db: None),
            min_value=-TRIM_RANGE_DB,
            max_value=TRIM_RANGE_DB,
            label="Trim",
            font=font_small,
            format_value=lambda db: f"{db:+.1f}",
            orientation=VerticalOrientation(),
        )
        self.components = (self.header, self.chain, self.trim_slider)

    def _velocity_label(self) -> str:
        """Names the state, not the action. A button labelled "Fixed" is
        ambiguous about whether that is what it does or what it already is —
        and this one is read at a glance, mid-song."""
        return "Vel: Fixed" if self.fixed_velocity else "Vel: Played"

    def _toggle_fixed_velocity(self) -> None:
        self.fixed_velocity = not self.fixed_velocity
        if self._velocity_action is not None:
            self.header.actions[self._velocity_action] = (
                self._velocity_label(),
                self._toggle_fixed_velocity,
            )
        if self._on_fixed_velocity is not None:
            self._on_fixed_velocity(self.fixed_velocity)


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

        self._catalog = catalog
        self._category = ALL
        self._on_select = on_select
        body = pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H)

        # Same rail as the voice picker. The catalogue is ordered by position in
        # a signal chain (amp, drive, modulation, delay, reverb...), and the
        # rail makes that ordering navigable instead of merely implicit.
        self.rail = CategoryRail(
            rect=pygame.Rect(body.x, body.y, RAIL_W, body.height),
            categories=categories_of(catalog, lambda e: e.category),
            selected=ALL,
            font=font_medium,
            font_small=font_small,
            on_select=self._on_category,
        )
        self.grid = TileGrid(
            rect=pygame.Rect(
                body.x + RAIL_W, body.y, body.width - RAIL_W, body.height
            ),
            tiles=self._tiles(),
            columns=2,
            row_height=88,
            font_title=font_medium,
            font_small=font_small,
            on_select=on_select,
        )
        self.components = (self.header, self.rail, self.grid)

    def _visible(self) -> list[EffectCatalogEntry]:
        if self._category == ALL:
            return self._catalog
        return [e for e in self._catalog if (e.category or "Other") == self._category]

    def _tiles(self) -> list[Tile]:
        return [
            Tile(
                title=entry.name,
                # The one-line note is what tells you whether you want this
                # effect; the category is already on the rail beside it.
                subtitle=entry.unavailable_reason or entry.note or entry.category,
                disabled=not entry.available,
                payload=entry,
            )
            for entry in self._visible()
        ]

    def _on_category(self, name: str) -> None:
        self._category = name
        self.rail.selected = name
        self.grid.tiles = self._tiles()
        self.grid.scroll_offset = 0
