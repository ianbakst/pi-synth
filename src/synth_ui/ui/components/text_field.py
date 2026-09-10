"""A read-only display of text being typed, with a caret.

Owns no input — the screen owns the buffer and the Keyboard reports keystrokes.
"""

import pygame

from synth_ui.config import DIVIDER, PANEL_BG, SLIDER_FILL, TEXT_PRIMARY
from synth_ui.ui.components.base import Component

_PAD_X = 20
_CARET_W = 2
_BLINK_MS = 530


class TextField(Component):
    def __init__(self, rect: pygame.Rect, font: pygame.font.Font, text: str = ""):
        super().__init__(rect)
        self.font = font
        self.text = text

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        pygame.draw.line(
            surface,
            DIVIDER,
            (self.rect.left, self.rect.bottom - 1),
            (self.rect.right, self.rect.bottom - 1),
        )

        rendered = self.font.render(self.text, True, TEXT_PRIMARY)
        max_w = self.rect.width - _PAD_X * 2 - _CARET_W - 6
        # Clip from the *left* when the name outgrows the field: the caret is at
        # the end, so that's the part you need to see while typing.
        src = pygame.Rect(max(0, rendered.get_width() - max_w), 0, 0, 0)
        src.width = min(rendered.get_width(), max_w)
        src.height = rendered.get_height()

        y = self.rect.centery - rendered.get_height() // 2
        surface.blit(rendered, (self.rect.x + _PAD_X, y), src)

        if (pygame.time.get_ticks() // _BLINK_MS) % 2 == 0:
            caret_x = self.rect.x + _PAD_X + src.width + 3
            pygame.draw.rect(
                surface,
                SLIDER_FILL,
                (caret_x, y, _CARET_W, rendered.get_height()),
            )
