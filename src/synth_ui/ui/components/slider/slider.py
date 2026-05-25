from typing import Callable

import pygame
from pygame import Rect
from pygame.font import Font
from pygame.surface import Surface

from synth_ui.config import (
    DIVIDER,
    PANEL_BG,
    SLIDER_BG,
    SLIDER_FILL,
    SLIDER_KNOB,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent


class Slider(Component):
    def __init__(
        self,
        rect: Rect,
        initial_value: float,
        on_change: Callable[[float], None],
        *,
        min_value: float = 0.0,
        max_value: float = 100.0,
        label: str | None = None,
        font: Font | None = None,
    ):
        super().__init__(rect)
        self.min_value = min_value
        self.max_value = max_value
        self.value = max(min(initial_value, max_value), min_value)
        self.on_change = on_change
        self.label = label
        self.font = font
        self.dragging = False
        self._track = Rect(rect.x + 16, rect.y + 38, rect.width - 32, 24)

    def _ratio(self) -> float:
        return (self.value - self.min_value) / (self.max_value - self.min_value)

    def _value_from_x(self, x: int) -> float:
        ratio = max(0.0, min(1.0, (x - self._track.x) / self._track.width))
        return self.min_value + ratio * (self.max_value - self.min_value)

    def draw(self, surface: Surface) -> None:
        if self.label is not None and self.font is not None:
            pygame.draw.rect(surface, PANEL_BG, self.rect)
            pygame.draw.line(
                surface, DIVIDER,
                (self.rect.left, self.rect.top),
                (self.rect.right, self.rect.top),
            )
            surface.blit(
                self.font.render(self.label, True, TEXT_SECONDARY),
                (self.rect.x + 16, self.rect.y + 10),
            )
            pct = self.font.render(f"{int(self._ratio() * 100)}%", True, TEXT_PRIMARY)
            surface.blit(pct, (self.rect.right - pct.get_width() - 16, self.rect.y + 10))

        ratio = self._ratio()
        fill_w = int(ratio * self._track.width)
        pygame.draw.rect(surface, SLIDER_BG, self._track, border_radius=12)
        pygame.draw.rect(
            surface, SLIDER_FILL,
            Rect(self._track.x, self._track.y, fill_w, self._track.height),
            border_radius=12,
        )
        pygame.draw.circle(surface, SLIDER_KNOB, (self._track.x + fill_w, self._track.centery), 14)

    def handle_event(self, event: UIEvent) -> bool:
        match event.type:
            case pygame.FINGERDOWN | pygame.MOUSEBUTTONDOWN:
                if self.rect.collidepoint(event.pos):
                    self.dragging = True
                    self.value = self._value_from_x(event.pos[0])
                    return True
            case pygame.FINGERMOTION | pygame.MOUSEMOTION:
                if self.dragging:
                    self.value = self._value_from_x(event.pos[0])
                    return True
            case pygame.FINGERUP | pygame.MOUSEBUTTONUP:
                if self.dragging:
                    self.dragging = False
                    self.on_change(self.value)
                    return True
        return False
