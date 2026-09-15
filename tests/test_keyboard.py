"""Tests for the on-screen keyboard. Grid arithmetic + the two layout tables.

These are the parts that silently yield the wrong character — a screenshot shows
the label, not what a tap actually produces — so taps are asserted per cell.
"""

import pygame
import pytest

from synth_ui.ui.components.keyboard import (
    BACKSPACE,
    BOTTOM,
    COLS,
    LETTERS,
    MODE,
    ROWS,
    SHIFT,
    SPACE,
    SYMBOLS,
    Keyboard,
)
from synth_ui.ui.event import UIEvent

RECT = pygame.Rect(0, 130, 800, 350)   # as laid out by TextEntryScreen
KEY_W, KEY_H = 80, 70


@pytest.fixture
def kb():
    pygame.init()
    typed: list[str] = []
    deletes: list[int] = []
    k = Keyboard(
        RECT,
        on_char=typed.append,
        on_backspace=lambda: deletes.append(1),
        font=pygame.font.Font(None, 30),
    )
    k.typed, k.deletes = typed, deletes
    return k


def cell(col: int, row: int) -> tuple[int, int]:
    """Centre of a grid cell."""
    return (RECT.x + col * KEY_W + KEY_W // 2, RECT.y + row * KEY_H + KEY_H // 2)


def tap(kb, col: int, row: int) -> None:
    kb.handle_event(UIEvent(pygame.MOUSEBUTTONDOWN, pos=cell(col, row)))


# --- layout tables ----------------------------------------------------------

def test_every_row_is_exactly_ten_cells():
    # The grid is fixed and hit-testing is arithmetic, so a short row would
    # silently shift every key after it.
    for row in (*LETTERS, *SYMBOLS, BOTTOM):
        assert len(row) == COLS


def test_backspace_holds_the_same_cell_in_both_layouts():
    assert LETTERS[3].index(BACKSPACE) == SYMBOLS[3].index(BACKSPACE)


def test_the_bottom_row_is_shared_and_always_offers_hyphen_and_underscore():
    assert BOTTOM[-2:] == ["-", "_"]
    assert MODE in BOTTOM and SPACE in BOTTOM


# --- hit testing ------------------------------------------------------------

def test_taps_map_to_the_right_letters(kb):
    tap(kb, 0, 0)   # '1'
    tap(kb, 0, 1)   # 'q'
    tap(kb, 9, 1)   # 'p'
    tap(kb, 1, 2)   # 'a'
    assert kb.typed == ["1", "q", "p", "a"]


def test_a_tap_outside_the_grid_is_ignored(kb):
    kb.handle_event(UIEvent(pygame.MOUSEBUTTONDOWN, pos=(400, 10)))
    assert kb.typed == []


def test_blank_cells_do_nothing(kb):
    tap(kb, 0, 2)   # the indent before 'a'
    assert kb.typed == []


def test_space_spans_its_cells(kb):
    for col in (2, 4, 6):
        tap(kb, col, 4)
    assert kb.typed == [" ", " ", " "]


def test_hyphen_and_underscore_work_in_both_modes(kb):
    tap(kb, 8, 4)
    tap(kb, 9, 4)
    kb.press(MODE)
    tap(kb, 8, 4)
    tap(kb, 9, 4)
    assert kb.typed == ["-", "_", "-", "_"]


def test_backspace_reports_a_delete(kb):
    tap(kb, 9, 3)
    assert kb.deletes == [1]
    assert kb.typed == []


# --- shift ------------------------------------------------------------------

def test_shift_capitalises_one_character_then_clears(kb):
    tap(kb, 0, 3)   # shift
    assert kb.shift is True
    tap(kb, 0, 1)   # q -> Q
    tap(kb, 1, 1)   # w stays lowercase
    assert kb.typed == ["Q", "w"]
    assert kb.shift is False


def test_shift_can_be_cancelled_by_tapping_it_again(kb):
    tap(kb, 0, 3)
    tap(kb, 0, 3)
    assert kb.shift is False


# --- mode toggle ------------------------------------------------------------

def test_mode_toggle_swaps_the_map_without_moving_the_grid(kb):
    tap(kb, 0, 0)          # '1'
    tap(kb, 0, 4)          # #+=
    assert kb.symbols is True
    tap(kb, 0, 0)          # same cell, now '!'
    assert kb.typed == ["1", "!"]


def test_symbols_layout_reaches_the_bracket_row(kb):
    kb.press(MODE)
    tap(kb, 0, 1)
    tap(kb, 1, 1)
    tap(kb, 6, 1)
    assert kb.typed == ["{", "}", "/"]


def test_switching_modes_clears_a_pending_shift(kb):
    # Shift is meaningless in symbols mode; leaving it latched would uppercase
    # the first letter typed after switching back.
    tap(kb, 0, 3)
    assert kb.shift is True
    kb.press(MODE)
    assert kb.shift is False


def test_shift_does_not_alter_symbols(kb):
    kb.shift = True
    kb.symbols = True
    tap(kb, 0, 0)
    assert kb.typed == ["!"]


def test_mode_label_reflects_the_target_not_the_current_mode(kb):
    assert kb._label(MODE) == "#+="
    kb.press(MODE)
    assert kb._label(MODE) == "ABC"


# --- geometry ---------------------------------------------------------------

def test_key_at_covers_the_whole_rect():
    pygame.init()
    kb = Keyboard(RECT, on_char=lambda c: None, on_backspace=lambda: None)
    for row in range(ROWS):
        for col in range(COLS):
            assert kb.key_at(*cell(col, row)) is not None or (
                kb._rows[row][col] == "\x05"
            )


def test_shift_is_unreachable_in_symbols_mode(kb):
    kb.press(MODE)
    assert SHIFT not in [k for row in kb._rows for k in row]
