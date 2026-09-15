"""A scrollable grid of tiles.

Replaces the full-width row lists. On an 800x480 panel a row holding a name and
a subtitle uses maybe 250px of its 800 and leaves the rest empty, so six items
fill the screen. The same items as tiles, two or three across, put two to three
times as much on screen *and* make each target larger, because the wasted width
becomes height.

Generic over the item type: callers supply the strings and the tap handler, so
rigs, voices and effects all use this rather than three near-identical lists.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pygame

from synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_DISABLED,
    BTN_NORMAL,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    TEXT_ACTIVE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

GAP = 8
PAD = 10
# Enough for a name, a subtitle and a row of chips without crowding; two of
# these plus gaps is the full 800px width.
MIN_TILE_W = 180


@dataclass
class Tile:
    """What a tile shows. `chips` render as small rounded labels under the
    subtitle — an effects chain reads better as a row of them than as a
    comma-separated string."""

    title: str
    subtitle: str = ""
    chips: Sequence[str] = field(default_factory=tuple)
    active: bool = False
    disabled: bool = False
    # Whatever the caller wants back from on_select; this component never
    # inspects it.
    payload: Any = None


class TileGrid(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        tiles: list[Tile],
        columns: int,
        row_height: int,
        font_title: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[Any], None],
        on_long_press: Callable[[Any], None] | None = None,
        on_reorder: Callable[[int, int], None] | None = None,
    ):
        super().__init__(rect)
        self.tiles = tiles
        self.columns = max(1, columns)
        self.row_height = row_height
        self.font_title = font_title
        self.font_small = font_small
        self.on_select = on_select
        self.on_long_press = on_long_press
        self.on_reorder = on_reorder

        self.scroll_offset = 0
        self._finger_moved = False
        self._tracking = False
        self._press_start_ms = 0
        self._press_index: int | None = None
        # Reorder state. A drag only picks a tile up after it has been held,
        # so an ordinary scroll of the grid can't rearrange it by accident —
        # the failure mode that makes drag-and-drop unpleasant on a touch panel.
        self._drag_index: int | None = None
        self._drag_pos: tuple[int, int] = (0, 0)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    @property
    def _tile_w(self) -> int:
        usable = self.rect.width - SCROLL_BAR_W - GAP * (self.columns + 1)
        return usable // self.columns

    @property
    def _rows(self) -> int:
        return (len(self.tiles) + self.columns - 1) // self.columns

    @property
    def _total_h(self) -> int:
        return self._rows * (self.row_height + GAP) + GAP

    @property
    def _max_scroll(self) -> int:
        return max(0, self._total_h - self.rect.height)

    def _tile_rect(self, index: int) -> pygame.Rect:
        row, col = divmod(index, self.columns)
        return pygame.Rect(
            self.rect.x + GAP + col * (self._tile_w + GAP),
            self.rect.y + GAP + row * (self.row_height + GAP) - self.scroll_offset,
            self._tile_w,
            self.row_height,
        )

    def _index_at(self, pos: tuple[int, int]) -> int | None:
        for index in range(len(self.tiles)):
            if self._tile_rect(index).collidepoint(pos):
                return index
        return None

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, BG, self.rect)
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll))

        previous_clip = surface.get_clip()
        surface.set_clip(self.rect)
        for index, tile in enumerate(self.tiles):
            if index == self._drag_index:
                continue        # drawn last, under the finger
            rect = self._tile_rect(index)
            if rect.bottom < self.rect.y or rect.y > self.rect.bottom:
                continue
            self._draw_tile(surface, rect, tile)

        if self._drag_index is not None:
            # An outline where it would land, and the tile itself under the
            # finger, so the drop target is never a guess.
            target = self._tile_rect(self._drop_index())
            pygame.draw.rect(surface, TEXT_SECONDARY, target, width=2, border_radius=8)
            floating = self._tile_rect(self._drag_index)
            floating.center = self._drag_pos
            self._draw_tile(surface, floating, self.tiles[self._drag_index])
        surface.set_clip(previous_clip)

        if self._max_scroll > 0:
            self._draw_scrollbar(surface)

    def _draw_tile(
        self, surface: pygame.Surface, rect: pygame.Rect, tile: Tile
    ) -> None:
        # Subtitle colour tracks the background: TEXT_SECONDARY is grey, which
        # is legible on the dark tile and nearly invisible on the active blue.
        if tile.disabled:
            background, title_color = BTN_DISABLED, TEXT_DISABLED
            sub_color = TEXT_DISABLED
        elif tile.active:
            background, title_color = BTN_ACTIVE, TEXT_ACTIVE
            sub_color = TEXT_ACTIVE
        else:
            background, title_color = BTN_NORMAL, TEXT_PRIMARY
            sub_color = TEXT_SECONDARY
        pygame.draw.rect(surface, background, rect, border_radius=8)

        y = rect.y + PAD
        title = self._fit(
            tile.title, self.font_title, rect.width - PAD * 2, title_color
        )
        surface.blit(title, (rect.x + PAD, y))
        y += title.get_height() + 4

        if tile.subtitle:
            sub = self._fit(
                tile.subtitle, self.font_small, rect.width - PAD * 2, sub_color
            )
            surface.blit(sub, (rect.x + PAD, y))
            y += sub.get_height() + 6

        if tile.chips:
            self._draw_chips(surface, rect, y, tile.chips)

    def _draw_chips(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        y: int,
        chips: Sequence[str],
    ) -> None:
        """Signal-chain order, left to right, truncated with a count when they
        don't fit — better than a clipped string that stops mid-word."""
        x = rect.x + PAD
        limit = rect.right - PAD
        for i, chip in enumerate(chips):
            text = self.font_small.render(chip, True, TEXT_SECONDARY)
            width = text.get_width() + 12
            remaining = len(chips) - i
            if x + width > limit:
                more = self.font_small.render(f"+{remaining}", True, TEXT_SECONDARY)
                if x + more.get_width() + 12 <= limit:
                    surface.blit(more, (x + 6, y + 3))
                return
            chip_rect = pygame.Rect(x, y, width, text.get_height() + 6)
            pygame.draw.rect(surface, SLIDER_BG, chip_rect, border_radius=6)
            surface.blit(text, (x + 6, y + 3))
            x += width + 4

    def _fit(
        self, text: str, font: pygame.font.Font, width: int, color: tuple
    ) -> pygame.Surface:
        surface = font.render(text, True, color)
        while surface.get_width() > width and len(text) > 3:
            text = text[:-4] + "..."
            surface = font.render(text, True, color)
        return surface

    def _draw_scrollbar(self, surface: pygame.Surface) -> None:
        bar_x = self.rect.right - SCROLL_BAR_W
        bar_h = max(30, int(self.rect.height * self.rect.height / self._total_h))
        bar_y = self.rect.y + int(
            self.scroll_offset / self._max_scroll * (self.rect.height - bar_h)
        )
        pygame.draw.rect(
            surface, SLIDER_BG, (bar_x, self.rect.y, SCROLL_BAR_W, self.rect.height)
        )
        pygame.draw.rect(
            surface, SLIDER_FILL, (bar_x, bar_y, SCROLL_BAR_W, bar_h), border_radius=4
        )

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    LONG_PRESS_MS = 500

    def _held_long_enough(self) -> bool:
        return pygame.time.get_ticks() - self._press_start_ms >= self.LONG_PRESS_MS

    def _drop_index(self) -> int:
        """Where the dragged tile would land, from the finger position."""
        nearest, best = self._drag_index or 0, None
        for index in range(len(self.tiles)):
            rect = self._tile_rect(index)
            if index == self._drag_index:
                rect = rect.copy()
            dx = rect.centerx - self._drag_pos[0]
            dy = rect.centery - self._drag_pos[1]
            distance = dx * dx + dy * dy
            if best is None or distance < best:
                nearest, best = index, distance
        return nearest

    def handle_event(self, event: UIEvent) -> bool:
        if self.loading:
            return False

        if event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.dy * 40
            return True

        if event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            if not self.rect.collidepoint(event.pos):
                return False
            self._tracking = True
            self._finger_moved = False
            self._press_start_ms = pygame.time.get_ticks()
            self._press_index = self._index_at(event.pos)
            self._drag_pos = event.pos
            return True

        if event.type in (pygame.FINGERMOTION, pygame.MOUSEMOTION) and self._tracking:
            self._drag_pos = event.pos
            if self._drag_index is not None:
                return True
            # Held, then moved, over a real tile: that's a pick-up, not a scroll.
            if (
                self.on_reorder
                and self._press_index is not None
                and self._held_long_enough()
                and not self._finger_moved
            ):
                self._drag_index = self._press_index
                return True
            if abs(event.dy) > 2:
                self._finger_moved = True
                self.scroll_offset -= int(event.dy)
            return True

        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP) and self._tracking:
            self._tracking = False
            index, self._press_index = self._press_index, None

            if self._drag_index is not None:
                source, self._drag_index = self._drag_index, None
                target = self._drop_index()
                if self.on_reorder and target != source:
                    self.on_reorder(source, target)
                return True

            if self._finger_moved or index is None:
                return True
            tile = self.tiles[index]
            if tile.disabled:
                return True
            if self.on_long_press and self._held_long_enough():
                # Long press is the secondary action (rename, delete), so a tile
                # doesn't need a row of small icons crowding its corner.
                self.on_long_press(tile.payload)
            else:
                self.on_select(tile.payload)
            return True

        return False
