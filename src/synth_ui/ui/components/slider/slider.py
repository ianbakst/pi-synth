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
        format_value: Callable[[float], str] | None = None,
        live: bool = False,
    ):
        super().__init__(rect)
        self.min_value = min_value
        self.max_value = max_value
        self.value = max(min(initial_value, max_value), min_value)
        self.on_change = on_change
        self.label = label
        self.font = font
        # How the current value reads. Defaults to a percentage of the
        # range, which is right for volume but nonsense for a bipolar
        # control -- -3 dB on a -12..+12 slider is not "37%".
        self.format_value = format_value or (lambda v: f"{int(self._ratio() * 100)}%")
        self.dragging = False
        # Fire on_change during the drag, not only on release. Right for an
        # effect parameter, where the question is "how much reverb" and the only
        # way to answer it is to hear the change while making it. Left off by
        # default: a volume slider that fires per motion event would send a
        # command per frame.
        self.live = live
        self._track = self._track_for(rect)

    @staticmethod
    def _track_for(rect: Rect) -> Rect:
        return Rect(rect.x + 16, rect.y + 38, rect.width - 32, 24)

    def move_to(self, x: int, y: int) -> None:
        """Reposition, keeping the track in step.

        The track is derived from the rect at construction, so moving the rect
        alone leaves hit-testing and drawing at the old position — which is what
        a scrolling list of sliders does on every frame.
        """
        self.rect.topleft = (x, y)
        self._track = self._track_for(self.rect)

    @property
    def track_rect(self) -> Rect:
        """The draggable band. A list of these needs to tell a drag on the
        control from a scroll of the list, and the track is that boundary."""
        return self._track

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
            pct = self.font.render(self.format_value(self.value), True, TEXT_PRIMARY)
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
                    if self.live:
                        self.on_change(self.value)
                    return True
            case pygame.FINGERUP | pygame.MOUSEBUTTONUP:
                if self.dragging:
                    self.dragging = False
                    self.on_change(self.value)
                    return True
        return False
