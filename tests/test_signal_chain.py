"""Tests for the snake-laid-out signal chain.

The geometry is the risk here: an off-by-one in the boustrophedon turns a
connector into a diagonal across the screen, and a wrong plus-index inserts an
effect at the wrong point in the chain — which changes the sound, not just the
picture.
"""

import pygame
import pytest

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.ui.components.signal_chain import COLUMNS, SignalChain
from synth_ui.ui.event import UIEvent


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def chain(count=4, **callbacks):
    catalog = [
        EffectCatalogEntry(f"FX{i}", f"urn:fx{i}", "Cat") for i in range(12)
    ]
    font = pygame.font.Font(None, 24)
    return SignalChain(
        rect=pygame.Rect(0, 60, 704, 420),
        effects=[Effect(10 + i, f"urn:fx{i}") for i in range(count)],
        catalog=catalog,
        font_medium=font,
        font_small=font,
        on_edit=callbacks.get("on_edit", lambda i: None),
        on_bypass=callbacks.get("on_bypass", lambda i, b: None),
        on_add=callbacks.get("on_add", lambda i: None),
        on_reorder=callbacks.get("on_reorder", lambda a, b: None),
        source_name="Hammond B3",
    )


class TestSnakeLayout:
    def test_first_row_runs_left_to_right(self):
        c = chain(count=3)
        xs = [c.slot_rect(s).x for s in range(COLUMNS)]
        assert xs == sorted(xs)

    def test_second_row_runs_right_to_left(self):
        c = chain(count=6)
        xs = [c.slot_rect(s).x for s in range(COLUMNS, 2 * COLUMNS)]
        assert xs == sorted(xs, reverse=True)

    def test_rows_are_stacked(self):
        c = chain(count=4)
        assert c.slot_rect(COLUMNS).y > c.slot_rect(0).y

    def test_the_row_turn_keeps_the_column(self):
        """This is what makes every connector a straight line: the last block of
        a row and the first of the next share a column, so no elbows."""
        c = chain(count=8)
        for turn in (COLUMNS, 2 * COLUMNS):
            assert c.slot_rect(turn - 1).centerx == c.slot_rect(turn).centerx

    def test_blocks_in_a_row_do_not_overlap(self):
        c = chain(count=3)
        assert c.slot_rect(0).right < c.slot_rect(1).x

    def test_blocks_stay_inside_the_component(self):
        c = chain(count=3)
        assert c.slot_rect(0).x >= c.rect.x
        assert c.slot_rect(COLUMNS - 1).right <= c.rect.right

    def test_the_instrument_is_the_first_block(self):
        c = chain(count=3)
        assert c.instrument_rect() == c.slot_rect(0)
        assert c.block_rect(0) == c.slot_rect(1)


class TestInsertionPoints:
    def test_one_plus_per_insertion_point(self):
        """N effects means N+1 places to insert, including before the first."""
        c = chain(count=4)
        rects = [c.plus_rect(i) for i in range(5)]
        assert len({(r.x, r.y) for r in rects}) == 5

    def test_a_chain_with_no_effects_still_offers_one(self):
        """Just the instrument, and one place to put the first effect."""
        c = chain(count=0)
        assert c.plus_rect(0).width > 0
        assert c.plus_rect(0).centery > c.instrument_rect().y

    def test_plus_between_two_blocks_sits_between_them(self):
        c = chain(count=3)
        plus = c.plus_rect(1)
        assert c.slot_rect(1).right <= plus.centerx <= c.slot_rect(2).x

    def test_no_plus_sits_before_the_instrument(self):
        """Nothing may precede the instrument, so the first insertion point is
        after it, not in front of it."""
        c = chain(count=3)
        instrument = c.instrument_rect()
        for index in range(len(c.effects) + 1):
            assert c.plus_rect(index).centerx > instrument.x

    def test_plus_at_a_row_turn_sits_on_the_vertical_wire(self):
        c = chain(count=4)
        plus = c.plus_rect(COLUMNS - 1)
        above, below = c.slot_rect(COLUMNS - 1), c.slot_rect(COLUMNS)
        assert plus.centerx == above.centerx
        assert above.bottom <= plus.centery <= below.y

    def test_tapping_a_plus_reports_its_index(self):
        added = []
        c = chain(count=3, on_add=added.append)
        target = c.plus_rect(2)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=target.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=target.center))
        assert added == [2]


class TestBlockInteraction:
    def test_tapping_a_block_edits_it(self):
        edited = []
        c = chain(count=3, on_edit=edited.append)
        rect = c.block_rect(1)
        pos = (rect.x + 20, rect.bottom - 15)        # clear of the bypass dot
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=pos))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=pos))
        assert edited == [11]

    def test_tapping_the_dot_bypasses_instead_of_editing(self):
        edited, bypassed = [], []
        c = chain(count=3, on_edit=edited.append,
                  on_bypass=lambda i, b: bypassed.append((i, b)))
        dot = c._dot_rect(c.block_rect(0))
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=dot.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=dot.center))
        assert bypassed == [(10, True)]
        assert edited == []

    def test_bypass_toggles_back(self):
        bypassed = []
        c = chain(count=1, on_bypass=lambda i, b: bypassed.append(b))
        c.effects[0].bypassed = True
        dot = c._dot_rect(c.block_rect(0))
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=dot.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=dot.center))
        assert bypassed == [False]


class TestReorder:
    def test_hold_then_drag_moves_a_block(self):
        moves = []
        c = chain(count=4, on_reorder=lambda a, b: moves.append((a, b)))
        start, end = c.block_rect(0), c.block_rect(2)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert moves == [(0, 2)]

    def test_a_quick_drag_does_not_move_anything(self):
        """Without the hold, scrolling the chain would rearrange it."""
        moves = []
        c = chain(count=9, on_reorder=lambda a, b: moves.append((a, b)))
        start = c.block_rect(0)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=start.center, dy=-40))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=start.center))
        assert moves == []

    def test_a_drag_does_not_also_edit(self):
        moves, edited = [], []
        c = chain(count=4, on_edit=edited.append,
                  on_reorder=lambda a, b: moves.append((a, b)))
        start, end = c.block_rect(1), c.block_rect(3)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert edited == []
        assert moves == [(1, 3)]

    def test_dropping_in_place_is_not_a_move(self):
        moves = []
        c = chain(count=4, on_reorder=lambda a, b: moves.append((a, b)))
        rect = c.block_rect(2)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=rect.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=rect.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))
        assert moves == []


class TestScrolling:
    def test_a_short_chain_does_not_scroll(self):
        assert chain(count=3)._max_scroll == 0

    def test_a_long_chain_scrolls(self):
        assert chain(count=12)._max_scroll > 0


class TestInstrumentIsStructural:
    """A chain must start with an instrument. These assert the component
    enforces that by construction rather than by asking the user nicely."""

    def test_tapping_the_instrument_opens_the_swap(self):
        swaps, edited = [], []
        c = chain(count=2, on_edit=edited.append)
        c.on_edit_instrument = lambda: swaps.append(True)
        pos = c.instrument_rect().center
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=pos))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=pos))
        assert swaps == [True]
        assert edited == []

    def test_the_instrument_cannot_be_dragged(self):
        moves = []
        c = chain(count=3, on_reorder=lambda a, b: moves.append((a, b)))
        start, end = c.instrument_rect(), c.block_rect(1)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert moves == []
        assert c._drag_slot is None

    def test_an_effect_cannot_be_dropped_before_the_instrument(self):
        """Dragging onto the instrument must land at position 0 of the effects,
        which is still after it — never displace it."""
        moves = []
        c = chain(count=3, on_reorder=lambda a, b: moves.append((a, b)))
        start, onto_instrument = c.block_rect(2), c.instrument_rect()
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=onto_instrument.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=onto_instrument.center))
        assert moves == [(2, 0)]

    def test_the_instrument_has_no_bypass_dot_to_hit(self):
        """Bypassing the instrument is silence; the corner of its block opens
        the swap like the rest of it."""
        swaps, bypassed = [], []
        c = chain(count=1, on_bypass=lambda i, b: bypassed.append(i))
        c.on_edit_instrument = lambda: swaps.append(True)
        corner = c._dot_rect(c.instrument_rect()).center
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=corner))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=corner))
        assert bypassed == []
        assert swaps == [True]

    def test_reorder_indices_are_effect_indices_not_slots(self):
        """The component speaks slots internally and effects to its caller; an
        off-by-one here would move the wrong effect."""
        moves = []
        c = chain(count=4, on_reorder=lambda a, b: moves.append((a, b)))
        start, end = c.block_rect(0), c.block_rect(2)
        c.handle_event(UIEvent(pygame.FINGERDOWN, pos=start.center))
        c._press_start_ms -= c.LONG_PRESS_MS + 50
        c.handle_event(UIEvent(pygame.FINGERMOTION, pos=end.center))
        c.handle_event(UIEvent(pygame.FINGERUP, pos=end.center))
        assert moves == [(0, 2)]

    def test_slot_count_includes_the_instrument(self):
        assert chain(count=0)._slots == 1
        assert chain(count=3)._slots == 4
