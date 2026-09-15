"""On-screen keyboard for naming things on a touchscreen with no keyboard.

Dumb by design: it reports keystrokes and holds no text. The screen owns the
buffer (see screens/text_entry.py), the same way VoiceList reports a selection
rather than owning the voice.

**Two layouts over one fixed grid.** The 5x10 geometry never changes — only the
character map does. Hit-testing and drawing stay layout-agnostic and the layouts
are data, not branching code, which also means a tap's result can be asserted
per cell in tests rather than inferred from a screenshot.

The bottom row is outside both maps and never changes apart from the mode label,
so `-` and `_` (the two characters most used in names) are always one tap away,
and backspace holds its cell in both layouts so neither is relearned on switch.
"""

from collections.abc import Callable

import pygame

from synth_ui.config import (
    BTN_ACTIVE,
    BTN_NORMAL,
    PANEL_BG,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

COLS = 10
ROWS = 5
_GAP = 4

# Non-character keys, as sentinels in the layout tables. Strings (not an enum) so
# a layout row reads as what it is.
SHIFT = "\x01"
BACKSPACE = "\x02"
MODE = "\x03"
SPACE = "\x04"
BLANK = "\x05"

_LABELS = {
    SHIFT: "shift",
    BACKSPACE: "del",
    SPACE: "space",
}

# Rows 0-3 swap with the mode; row 4 is fixed (see module docstring).
LETTERS: list[list[str]] = [
    list("1234567890"),
    list("qwertyuiop"),
    [BLANK, *list("asdfghjkl")],
    [SHIFT, BLANK, *list("zxcvbnm"), BACKSPACE],
]

SYMBOLS: list[list[str]] = [
    list("!@#$%^&*()"),          # the digit row's shift symbols
    list("{}[]<>/\\|~"),
    [BLANK, *list(".,:;'\"?+=")],
    [BLANK] * 9 + [BACKSPACE],   # backspace keeps its cell
]

# Fixed bottom row. SPACE spans the middle; the spans are expressed by repeating
# the key so hit-testing stays pure grid arithmetic.
BOTTOM: list[str] = [MODE, BLANK, *([SPACE] * 5), BLANK, "-", "_"]


class Keyboard(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        on_char: Callable[[str], None],
        on_backspace: Callable[[], None],
        font: pygame.font.Font | None = None,
    ):
        super().__init__(rect)
        self.on_char = on_char
        self.on_backspace = on_backspace
        self.font = font or pygame.font.Font(None, 30)
        self.symbols = False   # False = letters (ABC), True = symbols (#+=)
        self.shift = False     # latch: clears after one character

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    @property
    def _rows(self) -> list[list[str]]:
        return [*(SYMBOLS if self.symbols else LETTERS), BOTTOM]

    def _key_size(self) -> tuple[float, float]:
        return self.rect.width / COLS, self.rect.height / ROWS

    def key_at(self, x: int, y: int) -> str | None:
        """The key under a point, or None outside the grid / on a blank cell."""
        if not self.rect.collidepoint(x, y):
            return None
        key_w, key_h = self._key_size()
        col = int((x - self.rect.x) // key_w)
        row = int((y - self.rect.y) // key_h)
        if not (0 <= col < COLS and 0 <= row < ROWS):
            return None
        key = self._rows[row][col]
        return None if key == BLANK else key

    def _label(self, key: str) -> str:
        if key == MODE:
            return "ABC" if self.symbols else "#+="
        if key in _LABELS:
            return _LABELS[key]
        return key.upper() if (self.shift and not self.symbols) else key

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        key_w, key_h = self._key_size()

        drawn: set[tuple[int, int]] = set()
        for row_i, row in enumerate(self._rows):
            for col in range(COLS):
                key = row[col]
                if key == BLANK or (row_i, col) in drawn:
                    continue
                # A key repeated across cells (space) draws once, spanning them.
                span = 1
                while col + span < COLS and row[col + span] == key and key == SPACE:
                    span += 1
                for extra in range(span):
                    drawn.add((row_i, col + extra))

                rect = pygame.Rect(
                    int(self.rect.x + col * key_w) + _GAP,
                    int(self.rect.y + row_i * key_h) + _GAP,
                    int(key_w * span) - _GAP * 2,
                    int(key_h) - _GAP * 2,
                )
                active = (key == SHIFT and self.shift) or (
                    key == MODE and self.symbols
                )
                pygame.draw.rect(
                    surface,
                    BTN_ACTIVE if active else BTN_NORMAL,
                    rect,
                    border_radius=6,
                )
                label = self.font.render(
                    self._label(key), True, TEXT_ACTIVE if active else TEXT_PRIMARY
                )
                surface.blit(
                    label,
                    (
                        rect.centerx - label.get_width() // 2,
                        rect.centery - label.get_height() // 2,
                    ),
                )

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def press(self, key: str) -> None:
        """Apply a key. Separate from hit-testing so tests (and a future
        physical keyboard) can drive it without synthesising coordinates."""
        if key == SHIFT:
            self.shift = not self.shift
        elif key == MODE:
            self.symbols = not self.symbols
            self.shift = False
        elif key == BACKSPACE:
            self.on_backspace()
        elif key == SPACE:
            self.on_char(" ")
        else:
            self.on_char(key.upper() if (self.shift and not self.symbols) else key)
            self.shift = False   # latch, not a lock

    def handle_event(self, event: UIEvent) -> bool:
        if event.type not in (pygame.FINGERUP, pygame.MOUSEBUTTONDOWN):
            return False
        key = self.key_at(*event.pos)
        if key is None:
            return False
        self.press(key)
        return True
