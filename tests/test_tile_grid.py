"""Tests for the tile grid and category rail.

Grid arithmetic is what silently puts a tap on the wrong tile, so the mapping
from coordinates to items gets direct coverage rather than being inferred from
a screenshot.
"""

import pygame
import pytest

from synth_ui.ui.components.category_rail import ALL, CategoryRail, categories_of
from synth_ui.ui.components.tile_grid import Tile, TileGrid
from synth_ui.ui.event import UIEvent


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def grid(count=9, columns=3, on_select=None, on_long_press=None):
    font = pygame.font.Font(None, 24)
    return TileGrid(
        rect=pygame.Rect(0, 60, 800, 420),
        tiles=[Tile(title=f"T{i}", payload=i) for i in range(count)],
        columns=columns,
        row_height=76,
        font_title=font,
        font_small=font,
        on_select=on_select or (lambda p: None),
        on_long_press=on_long_press,
    )


class TestLayout:
    def test_rows_wrap_at_the_column_count(self):
        g = grid(count=7, columns=3)
        assert g._rows == 3

    def test_tiles_in_a_row_share_a_y(self):
        g = grid(count=6, columns=3)
        assert g._tile_rect(0).y == g._tile_rect(2).y
        assert g._tile_rect(3).y > g._tile_rect(0).y

    def test_tiles_do_not_overlap(self):
        g = grid(count=6, columns=3)
        assert g._tile_rect(0).right <= g._tile_rect(1).x

    def test_tiles_stay_inside_the_component(self):
        g = grid(count=6, columns=3)
        assert g._tile_rect(2).right <= g.rect.right

    def test_a_single_column_still_works(self):
        """max(1, columns) guards against a zero that would divide by zero."""
        assert grid(count=3, columns=0)._rows == 3


class TestSelection:
    def test_tap_selects_the_tile_under_it(self):
        picked = []
        g = grid(on_select=picked.append)
        rect = g._tile_rect(4)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert picked == [4]

    def test_a_drag_scrolls_instead_of_selecting(self):
        picked = []
        g = grid(count=30, on_select=picked.append)
        rect = g._tile_rect(0)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=rect.center, dy=-40))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert picked == []
        assert g.scroll_offset > 0

    def test_disabled_tiles_are_not_selectable(self):
        picked = []
        g = grid(on_select=picked.append)
        g.tiles[2].disabled = True
        rect = g._tile_rect(2)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert picked == []

    def test_scrolling_stops_at_the_end(self):
        g = grid(count=12)
        g.scroll_offset = 99999
        g.draw(pygame.Surface((800, 480)))
        assert g.scroll_offset == g._max_scroll

    def test_a_short_list_does_not_scroll(self):
        assert grid(count=3)._max_scroll == 0


class TestCategories:
    def test_counts_each_category_with_all_first(self):
        items = [("a", "Piano"), ("b", "Piano"), ("c", "Organ")]
        result = categories_of(items, lambda i: i[1])
        assert result[0] == (ALL, 3)
        assert ("Organ", 1) in result and ("Piano", 2) in result

    def test_blank_category_becomes_other(self):
        assert ("Other", 1) in categories_of([("a", "")], lambda i: i[1])

    def test_order_is_stable_not_by_count(self):
        """The rail is something you learn the shape of; frequency ordering
        would rearrange it every time the library changed."""
        items = [("a", "Zither")] + [("b", "Piano")] * 5
        names = [n for n, _ in categories_of(items, lambda i: i[1])]
        assert names == [ALL, "Piano", "Zither"]

    def test_tapping_a_row_selects_that_category(self):
        chosen = []
        rail = CategoryRail(
            rect=pygame.Rect(0, 60, 150, 420),
            categories=[(ALL, 3), ("Organ", 1), ("Piano", 2)],
            selected=ALL,
            font=pygame.font.Font(None, 24),
            font_small=pygame.font.Font(None, 20),
            on_select=chosen.append,
        )
        y = rail.rect.y + 48 + 10          # second row
        rail.handle_event(UIEvent(pygame.FINGERDOWN, pos=(50, y)))
        rail.handle_event(UIEvent(pygame.FINGERUP, pos=(50, y)))
        assert chosen == ["Organ"]
        assert rail.selected == "Organ"


class TestReorder:
    """Drag-to-reorder. The guard that matters is that an ordinary scroll can't
    rearrange the grid by accident."""

    def test_a_plain_drag_scrolls_and_does_not_reorder(self):
        moves = []
        g = grid(count=30, columns=3)
        g.on_reorder = lambda s, t: moves.append((s, t))
        rect = g._tile_rect(0)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=rect.center, dy=-40))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert moves == []

    def test_held_then_dragged_reorders(self):
        moves = []
        g = grid(count=6, columns=3)
        g.on_reorder = lambda s, t: moves.append((s, t))
        start, end = g._tile_rect(0), g._tile_rect(4)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        g._press_start_ms -= g.LONG_PRESS_MS + 50        # simulate the hold
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert moves == [(0, 4)]

    def test_dropping_where_it_started_is_not_a_move(self):
        moves = []
        g = grid(count=6, columns=3)
        g.on_reorder = lambda s, t: moves.append((s, t))
        rect = g._tile_rect(2)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g._press_start_ms -= g.LONG_PRESS_MS + 50
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=rect.center))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert moves == []

    def test_a_drag_does_not_also_select(self):
        picked, moves = [], []
        g = grid(count=6, columns=3, on_select=picked.append)
        g.on_reorder = lambda s, t: moves.append((s, t))
        start, end = g._tile_rect(0), g._tile_rect(3)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        g._press_start_ms -= g.LONG_PRESS_MS + 50
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        g.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert picked == []
        assert moves == [(0, 3)]

    def test_without_a_reorder_handler_a_hold_and_drag_still_scrolls(self):
        """Grids that aren't reorderable (the voice picker) must keep normal
        scrolling behaviour no matter how long the finger rests first."""
        g = grid(count=30, columns=3)
        rect = g._tile_rect(0)
        g.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        g._press_start_ms -= g.LONG_PRESS_MS + 50
        g.handle_event(UIEvent(pygame.FINGERMOTION, pos=rect.center, dy=-40))
        assert g.scroll_offset > 0
        assert g._drag_index is None
