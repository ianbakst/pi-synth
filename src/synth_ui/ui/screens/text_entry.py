"""TextEntryScreen — name something using the on-screen keyboard.

Owns the text buffer; the Keyboard reports keystrokes and the TextField displays
them. Used for naming a rig or a set on creation, and for renaming one later.

It doubles as the "manage this one thing" screen: `on_delete` puts a Delete
action in the header. Deletion needs *somewhere*, and this is the screen you
already reach by long-pressing the thing you mean — putting it on the tile
itself would make a mis-aimed press destructive.
"""

from collections.abc import Callable

import pygame

from synth_ui.config import HEADER_H, SCREEN_H, SCREEN_W
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.keyboard import Keyboard
from synth_ui.ui.components.text_field import TextField
from synth_ui.ui.screens.base import Screen

FIELD_H = 70


class TextEntryScreen(Screen):
    def __init__(
        self,
        title: str,
        initial: str,
        on_done: Callable[[str], None],
        on_cancel: Callable[[], None],
        max_len: int = 40,
        on_delete: Callable[[], None] | None = None,
        delete_label: str = "Delete",
    ):
        self._initial = initial
        self._on_done = on_done
        self.max_len = max_len

        font_large = pygame.font.Font(None, 36)
        font_field = pygame.font.Font(None, 40)
        font_key = pygame.font.Font(None, 30)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_cancel,
            action_label="Done",
            on_action=self._done,
            # Left of Done, so the button under your thumb stays the safe one.
            action2_label=delete_label if on_delete else "",
            on_action2=on_delete,
        )
        self.header.name = title

        self.field = TextField(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, FIELD_H),
            font=font_field,
            text=initial,
        )
        self.keyboard = Keyboard(
            rect=pygame.Rect(
                0, HEADER_H + FIELD_H, SCREEN_W, SCREEN_H - HEADER_H - FIELD_H
            ),
            on_char=self._append,
            on_backspace=self._backspace,
            font=font_key,
        )
        self.components = (self.header, self.field, self.keyboard)

    @property
    def text(self) -> str:
        return self.field.text

    def _append(self, char: str) -> None:
        if len(self.field.text) < self.max_len:
            self.field.text += char

    def _backspace(self) -> None:
        self.field.text = self.field.text[:-1]

    def _done(self) -> None:
        # An empty buffer falls back to what we started with rather than
        # disabling Done: clearing the field and tapping Done shouldn't be a
        # dead end, and an unnamed rig is worse than a default-named one.
        self._on_done(self.field.text.strip() or self._initial)
