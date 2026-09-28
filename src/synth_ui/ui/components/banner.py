"""Banner — one message laid over the bottom of a screen until it's tapped away.

For the rare thing the player has to be told about without being asked
anything: the sets file was damaged and has been recovered. Not a dialog — there
is nothing to decide, and a dialog in the way of the pads would stop the
instrument being played until it was answered.

The owning screen must offer it each event first and stop if it took it, or the
tap that dismisses it also lands on whatever is underneath.
"""

import pygame

from synth_ui.config import PANEL_BG, STATUS_ERR, TEXT_PRIMARY, TEXT_SECONDARY
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

PAD = 14
_POINTER = (
    pygame.FINGERDOWN, pygame.FINGERMOTION, pygame.FINGERUP,
    pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION,
)


class Banner(Component):
    def __init__(self, rect: pygame.Rect, font: pygame.font.Font,
                 font_small: pygame.font.Font):
        super().__init__(rect)
        self.text: str | None = None
        self._font = font
        self._font_small = font_small

    def _lines(self) -> list[str]:
        return _wrap(self.text or "", self._font, self.rect.width - PAD * 2)

    def _box(self) -> pygame.Rect:
        line_h = self._font.get_linesize()
        height = PAD * 2 + line_h * len(self._lines()) + self._font_small.get_linesize()
        return pygame.Rect(self.rect.x, self.rect.bottom - height,
                           self.rect.width, height)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.text:
            return
        box = self._box()
        pygame.draw.rect(surface, PANEL_BG, box)
        pygame.draw.rect(surface, STATUS_ERR, box, width=2)
        y = box.y + PAD
        for line in self._lines():
            surface.blit(self._font.render(line, True, TEXT_PRIMARY), (box.x + PAD, y))
            y += self._font.get_linesize()
        hint = self._font_small.render("Tap to dismiss", True, TEXT_SECONDARY)
        surface.blit(hint, (box.x + PAD, y))

    def handle_event(self, event: UIEvent) -> bool:
        """Swallow every touch inside the banner; a lift dismisses it."""
        if not self.text or event.type not in _POINTER:
            return False
        if not self._box().collidepoint(event.pos):
            return False
        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
            self.text = None
        return True


def _wrap(text: str, font: pygame.font.Font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.size(candidate)[0] > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines
