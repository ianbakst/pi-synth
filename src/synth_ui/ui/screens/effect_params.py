"""Edit one effect's controls.

The knobs are read from the plugin itself (`lv2info`), not from a table in this
repo. Hand-written port symbols have been a repeated source of silent failures
here — a value sent to a symbol the plugin doesn't have is accepted by mod-host
and does nothing — and the plugin is the authority on its own controls.

Values are sent continuously as they're dragged (Slider(live=True)), so you hear
the change while making it. That is the whole point: "is this reverb too much" is
not a question anyone answers by typing a number and pressing apply.
"""

from collections.abc import Callable

import pygame

from synth_ui.clients.lv2 import ControlPort
from synth_ui.config import (
    BG,
    BTN_MARGIN,
    HEADER_H,
    SCREEN_H,
    SCREEN_W,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen

# Tall enough to drag accurately with a finger, short enough that a plugin with
# a handful of controls doesn't need scrolling to see them all.
ROW_H = 72


def format_value(port: ControlPort) -> Callable[[float], str]:
    """How a port's value is written out.

    Toggles read as on/off rather than 1.0/0.0, integers lose their decimal
    point, and everything else gets one — enough precision to be useful without
    implying more than a finger on a 800px panel can set.
    """
    if port.toggled:
        return lambda v: "on" if v >= 0.5 else "off"
    if port.integer:
        return lambda v: f"{v:.0f}"
    return lambda v: f"{v:.2f}" if abs(port.maximum - port.minimum) <= 4 else f"{v:.1f}"


class ParamSliders(Component):
    """A scrollable column of sliders, one per control port.

    Sliders live in absolute screen coordinates and are repositioned on each
    draw, so scrolling is a matter of moving them rather than translating every
    event. Slider.move_to keeps the draggable track in step with the rect;
    setting .rect alone leaves hit-testing at the old position.
    """

    def __init__(
        self,
        rect: pygame.Rect,
        ports: list[ControlPort],
        values: dict[str, float],
        font: pygame.font.Font,
        on_change: Callable[[str, float], None],
    ):
        super().__init__(rect)
        self.ports = ports
        self.scroll_offset = 0
        self._dragging: Slider | None = None
        # A scroll gesture is tracked from its first touch, because a finger
        # dragging the list upward leaves this component's rect almost
        # immediately — testing position per motion event would drop the scroll
        # the moment it became useful.
        self._scrolling = False
        self._font = font

        self.sliders: list[Slider] = [
            Slider(
                rect=pygame.Rect(rect.x, rect.y, rect.width - SCROLL_BAR_W, ROW_H),
                initial_value=values.get(port.symbol, port.default),
                on_change=(lambda v, s=port.symbol: on_change(s, v)),
                min_value=port.minimum,
                max_value=port.maximum,
                label=port.name,
                font=font,
                format_value=format_value(port),
                live=True,
            )
            for port in ports
        ]

    @property
    def _row_h(self) -> int:
        return ROW_H + BTN_MARGIN

    @property
    def _max_scroll(self) -> int:
        return max(0, len(self.sliders) * self._row_h - self.rect.height)

    def _layout(self) -> None:
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll))
        for i, slider in enumerate(self.sliders):
            y = self.rect.y - self.scroll_offset + i * self._row_h
            slider.move_to(self.rect.x, y)

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, BG, self.rect)
        if not self.sliders:
            text = self._font.render(
                "This effect has no adjustable parameters", True, TEXT_SECONDARY
            )
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        self._layout()
        previous = surface.get_clip()
        surface.set_clip(self.rect)
        for slider in self.sliders:
            if slider.rect.bottom >= self.rect.y and slider.rect.y <= self.rect.bottom:
                slider.draw(surface)
        surface.set_clip(previous)

        if self._max_scroll > 0:
            total = len(self.sliders) * self._row_h
            bar_x = self.rect.right - SCROLL_BAR_W
            bar_h = max(30, int(self.rect.height * self.rect.height / total))
            bar_y = self.rect.y + int(
                self.scroll_offset / self._max_scroll * (self.rect.height - bar_h)
            )
            pygame.draw.rect(
                surface, SLIDER_BG,
                (bar_x, self.rect.y, SCROLL_BAR_W, self.rect.height),
            )
            pygame.draw.rect(
                surface, SLIDER_FILL, (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    def handle_event(self, event: UIEvent) -> bool:
        if self._dragging is not None:
            handled = self._dragging.handle_event(event)
            if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                self._dragging = None
            return handled

        if event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.dy * 40
            return True

        if event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            if not self.rect.collidepoint(event.pos):
                return False
            self._layout()
            # Only a touch on the track itself grabs a slider. Everywhere else
            # in the row — the label and value line above it — scrolls the list,
            # so a column of full-width sliders is still scrollable by dragging.
            for slider in self.sliders:
                if slider.track_rect.collidepoint(event.pos):
                    self._dragging = slider
                    return slider.handle_event(event)
            self._scrolling = True
            return False

        if self._scrolling:
            if event.type == pygame.FINGERMOTION and abs(event.dy) > 2:
                self.scroll_offset -= int(event.dy)
                return True
            if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                self._scrolling = False
        return False


class EffectParamsScreen(Screen):
    """Header + one slider per control port, with a Reset that returns every
    control to the plugin's own default."""

    def __init__(
        self,
        name: str,
        ports: list[ControlPort],
        values: dict[str, float],
        on_change: Callable[[str, float], None],
        on_back: Callable,
        on_reset: Callable | None = None,
    ):
        font_large = pygame.font.Font(None, 36)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="Reset" if on_reset else "",
            on_action=on_reset,
        )
        self.header.name = name

        self.sliders = ParamSliders(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            ports=ports,
            values=values,
            font=font_small,
            on_change=on_change,
        )
        self.components = (self.header, self.sliders)
