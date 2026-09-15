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
from synth_ui.ui.components.slider.orient import (
    HorizontalOrientation,
    Orientation,
    VerticalOrientation,
)
from synth_ui.ui.event import UIEvent

# Track thickness, and the room the label/value text needs beside it.
_TRACK = 24
_PAD = 16
_LABEL_H = 38
_KNOB_R = 14


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
        orientation: Orientation | None = None,
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
        # way to answer it is by hearing the change while making it. Off by
        # default: a volume slider firing per motion event sends a command a
        # frame.
        self.live = live
        # The vertical form was scaffolded in orient.py and never wired up. A
        # vertical fader on the right edge gives the list back the full width.
        self.orientation = orientation or HorizontalOrientation()
        self._track = self._track_for(rect)

    @property
    def _is_vertical(self) -> bool:
        return isinstance(self.orientation, VerticalOrientation)

    def _track_for(self, rect: Rect) -> Rect:
        if self._is_vertical:
            # Label above, value below, track down the middle.
            height = rect.height - _LABEL_H * 2
            return Rect(
                rect.centerx - _TRACK // 2, rect.y + _LABEL_H, _TRACK, max(height, 1)
            )
        return Rect(rect.x + _PAD, rect.y + _LABEL_H, rect.width - _PAD * 2, _TRACK)

    def move_to(self, x: int, y: int) -> None:
        """Reposition, keeping the track in step.

        The track is derived from the rect, so moving the rect alone leaves
        hit-testing and drawing at the old position — which is what a scrolling
        column of sliders does on every frame.
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

    def _value_from_pos(self, pos: tuple[int, int]) -> float:
        ratio = self.orientation.ratio_from_pos(self._track, pos)
        return self.min_value + ratio * (self.max_value - self.min_value)

    def draw(self, surface: Surface) -> None:
        if self.label is not None and self.font is not None:
            pygame.draw.rect(surface, PANEL_BG, self.rect)
            start, end = (
                ((self.rect.left, self.rect.top), (self.rect.left, self.rect.bottom))
                if self._is_vertical
                else ((self.rect.left, self.rect.top), (self.rect.right, self.rect.top))
            )
            pygame.draw.line(surface, DIVIDER, start, end)
            self._draw_text(surface)

        ratio = self._ratio()
        pygame.draw.rect(surface, SLIDER_BG, self._track, border_radius=12)
        pygame.draw.rect(
            surface,
            SLIDER_FILL,
            self.orientation.fill_rect(self._track, ratio),
            border_radius=12,
        )
        pygame.draw.circle(
            surface, SLIDER_KNOB, self.orientation.knob_pos(self._track, ratio), _KNOB_R
        )

    def _draw_text(self, surface: Surface) -> None:
        label = self.font.render(self.label, True, TEXT_SECONDARY)
        value = self.font.render(self.format_value(self.value), True, TEXT_PRIMARY)
        if self._is_vertical:
            # Stacked, not side by side: a narrow column has no room for both.
            surface.blit(
                label, (self.rect.centerx - label.get_width() // 2, self.rect.y + 10)
            )
            surface.blit(
                value,
                (
                    self.rect.centerx - value.get_width() // 2,
                    self.rect.bottom - value.get_height() - 10,
                ),
            )
        else:
            surface.blit(label, (self.rect.x + _PAD, self.rect.y + 10))
            surface.blit(
                value, (self.rect.right - value.get_width() - _PAD, self.rect.y + 10)
            )

    def handle_event(self, event: UIEvent) -> bool:
        match event.type:
            case pygame.FINGERDOWN | pygame.MOUSEBUTTONDOWN:
                if self.rect.collidepoint(event.pos):
                    self.dragging = True
                    self.value = self._value_from_pos(event.pos)
                    return True
            case pygame.FINGERMOTION | pygame.MOUSEMOTION:
                if self.dragging:
                    self.value = self._value_from_pos(event.pos)
                    if self.live:
                        self.on_change(self.value)
                    return True
            case pygame.FINGERUP | pygame.MOUSEBUTTONUP:
                if self.dragging:
                    self.dragging = False
                    self.on_change(self.value)
                    return True
        return False
