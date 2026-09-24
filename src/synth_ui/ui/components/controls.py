"""The touchscreen's drawing of a plugin's controls: knob, toggle, choice.

Each widget fills one cell of the params grid — control on top, name underneath
— so a plugin's screen reads as a uniform panel whatever mix of controls it has.
What kind of control a setting is was decided in clients/controls.py; these only
draw it and turn touches into values.

Knobs are dragged *vertically*, relative to where the finger landed, not by
pointing at the angle you want. A finger covers the knob it is turning, so an
absolute gesture can't be seen while it's made, and a relative one lets a small
knob be set as finely as the drag is long.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pygame
from pygame import Rect, Surface, gfxdraw
from pygame.font import Font

from synth_ui.clients.controls import CHOICE, TOGGLE, Control
from synth_ui.config import (
    BTN_ACTIVE,
    BTN_NORMAL,
    SLIDER_BG,
    SLIDER_FILL,
    SLIDER_KNOB,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

LABEL_H = 30
PAD = 10

# Vertical travel, in pixels, that sweeps a knob end to end. Long enough to set a
# 20 Hz..20 kHz knob within a few Hz of intent; short enough to cover its range
# without lifting the finger on a 480-pixel-tall screen.
KNOB_DRAG_PX = 240
KNOB_RING_W = 10
# The dial runs from 7:30 round to 4:30, the gap at the bottom where a real
# knob's end stops are. Angles are mathematical: degrees anticlockwise from 3
# o'clock.
_ARC_START = 225.0
_ARC_SWEEP = 270.0

# At most this many options are drawn as buttons in the cell. More than that and
# each button is too short to hit, so the cell becomes a stepper instead.
MAX_SEGMENTS = 3
_STEPPER_H = 52
_ARROW_W = 40

_DOWN = (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN)
_MOTION = (pygame.FINGERMOTION, pygame.MOUSEMOTION)
_UP = (pygame.FINGERUP, pygame.MOUSEBUTTONUP)


def fit_text(font: Font, text: str, width: int) -> str:
    """`text`, cut down with "..." until it fits `width`. Plugin names for
    their controls ("Highshelf Frequency") are written for a desktop."""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _arc_polygon(
    center: tuple[float, float],
    outer: float,
    inner: float,
    start_ratio: float,
    end_ratio: float,
) -> list[tuple[int, int]]:
    """A thick arc between two points of the dial's travel, as one polygon.

    One filled polygon rather than pygame.draw.arc, whose thick arcs have
    moiré gaps, or a chain of circles, which is a hundred draw calls a knob at
    30 frames a second on the UI's single core.
    """
    lo, hi = sorted((start_ratio, end_ratio))
    steps = max(2, int((hi - lo) * 48) + 1)
    angles = [
        math.radians(_ARC_START - _ARC_SWEEP * (lo + (hi - lo) * i / (steps - 1)))
        for i in range(steps)
    ]
    cx, cy = center
    outer_pts = [(cx + outer * math.cos(a), cy - outer * math.sin(a)) for a in angles]
    inner_pts = [
        (cx + inner * math.cos(a), cy - inner * math.sin(a)) for a in reversed(angles)
    ]
    return [(round(x), round(y)) for x, y in outer_pts + inner_pts]


def _fill_polygon(surface: Surface, points: list[tuple[int, int]], color) -> None:
    gfxdraw.filled_polygon(surface, points, color)
    gfxdraw.aapolygon(surface, points, color)


class ControlWidget(Component):
    """One cell: a control, with its name underneath.

    `on_change` is called with the new value as it changes, not on release: the
    question an effect's control answers is "how much", and the only way to
    answer it is to hear the change while making it.
    """

    def __init__(
        self,
        rect: Rect,
        control: Control,
        value: float,
        on_change: Callable[[float], None],
        font: Font,
    ):
        super().__init__(rect)
        self.control = control
        self.value = control.clamp(value)
        self.on_change = on_change
        self.font = font

    def move_to(self, x: int, y: int) -> None:
        """Reposition. Geometry is derived from the rect on every draw and
        event, so this is all a paged grid has to do."""
        self.rect.topleft = (x, y)

    @property
    def body(self) -> Rect:
        """The cell above the name: where the control itself goes."""
        return Rect(
            self.rect.x + PAD,
            self.rect.y + PAD,
            self.rect.width - PAD * 2,
            self.rect.height - LABEL_H - PAD,
        )

    def _set(self, value: float) -> None:
        value = self.control.clamp(value)
        if value != self.value:
            self.value = value
            self.on_change(value)

    def _draw_label(self, surface: Surface) -> None:
        text = fit_text(self.font, self.control.name, self.rect.width - PAD * 2)
        label = self.font.render(text, True, TEXT_SECONDARY)
        surface.blit(
            label,
            (
                self.rect.centerx - label.get_width() // 2,
                self.rect.bottom - LABEL_H + (LABEL_H - label.get_height()) // 2,
            ),
        )

    def _draw_centered(self, surface: Surface, text: str, rect: Rect, color) -> None:
        rendered = self.font.render(fit_text(self.font, text, rect.width), True, color)
        surface.blit(rendered, rendered.get_rect(center=rect.center))


class Knob(ControlWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dragging = False
        self._grab_y = 0
        self._grab_ratio = 0.0

    @property
    def _center(self) -> tuple[int, int]:
        return self.body.center

    @property
    def _radius(self) -> int:
        return max(12, min(self.body.width, self.body.height) // 2)

    def draw(self, surface: Surface) -> None:
        outer = self._radius
        inner = outer - KNOB_RING_W
        ratio = self.control.to_ratio(self.value)

        _fill_polygon(
            surface, _arc_polygon(self._center, outer, inner, 0.0, 1.0), SLIDER_BG
        )
        # A control either side of zero — a gain in dB, a pan — fills from zero,
        # so -6 and +6 look like opposites rather than a little and a lot.
        c = self.control
        origin = c.to_ratio(0.0) if c.minimum < 0 < c.maximum else 0.0
        if abs(ratio - origin) > 0.005:
            _fill_polygon(
                surface,
                _arc_polygon(self._center, outer, inner, origin, ratio),
                SLIDER_FILL,
            )

        # The pointer: a dot riding the ring at the current value.
        angle = math.radians(_ARC_START - _ARC_SWEEP * ratio)
        cx, cy = self._center
        mid = outer - KNOB_RING_W / 2
        dot = (round(cx + mid * math.cos(angle)), round(cy - mid * math.sin(angle)))
        color = TEXT_ACTIVE if self.dragging else SLIDER_KNOB
        gfxdraw.filled_circle(surface, *dot, KNOB_RING_W // 2 + 3, color)
        gfxdraw.aacircle(surface, *dot, KNOB_RING_W // 2 + 3, color)

        value_box = Rect(0, 0, inner * 2 - 8, self.font.get_height())
        value_box.center = self._center
        self._draw_centered(
            surface, self.control.format(self.value), value_box, TEXT_PRIMARY
        )
        self._draw_label(surface)

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in _DOWN and self.rect.collidepoint(event.pos):
            self.dragging = True
            self._grab_y = event.pos[1]
            self._grab_ratio = self.control.to_ratio(self.value)
            return True
        if not self.dragging:
            return False
        if event.type in _MOTION:
            travel = (self._grab_y - event.pos[1]) / KNOB_DRAG_PX
            self._set(self.control.from_ratio(self._grab_ratio + travel))
            return True
        if event.type in _UP:
            self.dragging = False
            return True
        return False


class _Tappable(ControlWidget):
    """A widget that acts on release, and only if the finger lifts where it
    went down — so sliding off a button is how you change your mind."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pressed: Rect | None = None

    def _targets(self) -> list[tuple[Rect, Callable[[], None]]]:
        raise NotImplementedError

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in _DOWN:
            self._pressed = next(
                (r for r, _ in self._targets() if r.collidepoint(event.pos)), None
            )
            return self._pressed is not None
        if self._pressed is None:
            return False
        if event.type in _UP:
            pressed, self._pressed = self._pressed, None
            for rect, action in self._targets():
                if rect == pressed and rect.collidepoint(event.pos):
                    action()
            return True
        return event.type in _MOTION


class Toggle(_Tappable):
    @property
    def _button(self) -> Rect:
        rect = Rect(0, 0, self.body.width, min(_STEPPER_H, self.body.height))
        rect.center = self.body.center
        return rect

    def _targets(self):
        return [(self._button, lambda: self._set(0.0 if self.value >= 0.5 else 1.0))]

    def draw(self, surface: Surface) -> None:
        on = self.value >= 0.5
        pygame.draw.rect(
            surface, BTN_ACTIVE if on else BTN_NORMAL, self._button, border_radius=26
        )
        self._draw_centered(
            surface, "On" if on else "Off", self._button,
            TEXT_ACTIVE if on else TEXT_SECONDARY,
        )
        self._draw_label(surface)


class Choice(_Tappable):
    """A few options as buttons stacked in the cell; more as a stepper,
    ‹ value ›, where tapping the value itself also steps forward."""

    @property
    def _options(self) -> list[tuple[float, str]]:
        return self.control.options

    @property
    def _segmented(self) -> bool:
        return len(self._options) <= MAX_SEGMENTS

    def _segments(self) -> list[Rect]:
        n = len(self._options)
        gap = 4
        height = min(44, (self.body.height - gap * (n - 1)) // n)
        top = self.body.centery - (height * n + gap * (n - 1)) // 2
        return [
            Rect(self.body.x, top + i * (height + gap), self.body.width, height)
            for i in range(n)
        ]

    @property
    def _stepper(self) -> Rect:
        rect = Rect(0, 0, self.body.width, min(_STEPPER_H, self.body.height))
        rect.center = self.body.center
        return rect

    def _step(self, delta: int) -> None:
        # Wraps: with no list on screen, "past the last one" is only useful as
        # "back to the first".
        index = (self.control.option_index(self.value) + delta) % len(self._options)
        self._set(self._options[index][0])

    def _targets(self):
        if not self._options:
            return []
        if self._segmented:
            return [
                (rect, lambda v=value: self._set(v))
                for rect, (value, _label) in zip(self._segments(), self._options)
            ]
        s = self._stepper
        left = Rect(s.x, s.y, _ARROW_W, s.height)
        right = Rect(s.right - _ARROW_W, s.y, _ARROW_W, s.height)
        middle = Rect(left.right, s.y, s.width - _ARROW_W * 2, s.height)
        return [
            (left, lambda: self._step(-1)),
            (middle, lambda: self._step(1)),
            (right, lambda: self._step(1)),
        ]

    def draw(self, surface: Surface) -> None:
        if not self._options:
            self._draw_label(surface)
            return
        selected = self.control.option_index(self.value)
        if self._segmented:
            for i, (rect, (_value, label)) in enumerate(
                zip(self._segments(), self._options)
            ):
                on = i == selected
                pygame.draw.rect(
                    surface, BTN_ACTIVE if on else BTN_NORMAL, rect, border_radius=8
                )
                self._draw_centered(
                    surface, label, rect, TEXT_ACTIVE if on else TEXT_SECONDARY
                )
        else:
            s = self._stepper
            pygame.draw.rect(surface, BTN_NORMAL, s, border_radius=8)
            self._draw_arrow(surface, Rect(s.x, s.y, _ARROW_W, s.height), -1)
            self._draw_arrow(
                surface, Rect(s.right - _ARROW_W, s.y, _ARROW_W, s.height), 1
            )
            middle = Rect(s.x + _ARROW_W, s.y, s.width - _ARROW_W * 2, s.height)
            self._draw_centered(
                surface, self._options[selected][1], middle, TEXT_PRIMARY
            )
        self._draw_label(surface)

    @staticmethod
    def _draw_arrow(surface: Surface, rect: Rect, direction: int) -> None:
        # Drawn, not typed: the default font has no guaranteed ‹ › glyphs.
        cx, cy = rect.center
        w, h = 6 * direction, 9
        pygame.draw.polygon(
            surface, TEXT_SECONDARY, [(cx - w, cy - h), (cx + w, cy), (cx - w, cy + h)]
        )


def widget_for(
    rect: Rect,
    control: Control,
    value: float,
    on_change: Callable[[float], None],
    font: Font,
) -> ControlWidget:
    kind = {TOGGLE: Toggle, CHOICE: Choice}.get(control.kind, Knob)
    return kind(rect, control, value, on_change, font)
