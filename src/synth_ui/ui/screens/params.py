"""Edit one plugin's controls.

The controls are read from the plugin itself (`lv2info`), not from a table in
this repo. Hand-written port symbols have been a repeated source of silent
failures here — a value sent to a symbol the plugin doesn't have is accepted by
mod-host and does nothing — and the plugin is the authority on its own
controls. clients/controls.py decides what each one is drawn as.

Values are sent continuously as they change, so you hear the change while making
it. That is the whole point: "is this reverb too much" is not a question anyone
answers by typing a number and pressing apply.
"""

from collections.abc import Callable

import pygame

from synth_ui.clients.controls import controls_for
from synth_ui.clients.lv2 import ControlPort
from synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_NORMAL,
    HEADER_H,
    SCREEN_H,
    SCREEN_W,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.components.controls import ControlWidget, widget_for
from synth_ui.ui.components.header import Header
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen

COLUMNS = 4
# Three rows fit under the header, so a plugin with up to twelve controls — most
# of them — is a single page.
ROW_H = 140
# Finger-wide: the page buttons live here. Pages rather than scrolling because a
# drag anywhere in the grid lands on a knob, and knobs are turned by dragging
# vertically — a scroll gesture would have to share it with every control.
PAGE_STRIP_W = 40


class ControlPanel(Component):
    """A paged grid of controls, one cell each, in the plugin's order.

    Widgets live in absolute screen coordinates. Only the current page's are
    positioned, drawn and hit-tested; the rest wait where they are.
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
        self.page = 0
        self._font = font
        # The widget a touch landed on keeps every event until the finger
        # lifts, wherever it moves — a knob dragged upward leaves its own cell
        # almost immediately.
        self._captured: ControlWidget | None = None
        # Page buttons act on release, and only if released on the same one.
        self._pressed: pygame.Rect | None = None

        cell_w = (rect.width - PAGE_STRIP_W) // COLUMNS
        self.widgets: list[ControlWidget] = [
            widget_for(
                rect=pygame.Rect(0, 0, cell_w, ROW_H),
                control=control,
                value=values.get(control.symbol, control.default),
                on_change=(lambda v, s=control.symbol: on_change(s, v)),
                font=font,
            )
            for control in controls_for(ports)
        ]
        self._layout()

    @property
    def per_page(self) -> int:
        return COLUMNS * max(1, self.rect.height // ROW_H)

    @property
    def pages(self) -> int:
        return max(1, -(-len(self.widgets) // self.per_page))

    @property
    def visible(self) -> list[ControlWidget]:
        first = self.page * self.per_page
        return self.widgets[first:first + self.per_page]

    @property
    def _strip(self) -> pygame.Rect:
        return pygame.Rect(
            self.rect.right - PAGE_STRIP_W, self.rect.y,
            PAGE_STRIP_W, self.rect.height,
        )

    @property
    def _prev_button(self) -> pygame.Rect:
        s = self._strip
        return pygame.Rect(s.x, s.y, s.width, s.height // 3)

    @property
    def _next_button(self) -> pygame.Rect:
        s = self._strip
        return pygame.Rect(s.x, s.bottom - s.height // 3, s.width, s.height // 3)

    def turn(self, delta: int) -> None:
        # Stops at the ends rather than wrapping: the page count is on screen,
        # and "next" landing back on page 1 reads as the plugin having lost
        # its other controls.
        self.page = max(0, min(self.pages - 1, self.page + delta))
        self._layout()

    def _layout(self) -> None:
        for i, widget in enumerate(self.visible):
            row, col = divmod(i, COLUMNS)
            widget.move_to(
                self.rect.x + col * widget.rect.width, self.rect.y + row * ROW_H
            )

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, BG, self.rect)
        if not self.widgets:
            # Not only effects any more: b_synth is a real case of an
            # *instrument* with no control ports at all — its drawbars,
            # percussion and Leslie are MIDI CC.
            text = self._font.render(
                "No adjustable parameters", True, TEXT_SECONDARY
            )
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        for widget in self.visible:
            widget.draw(surface)
        if self.pages > 1:
            self._draw_pager(surface)

    def _draw_pager(self, surface: pygame.Surface) -> None:
        for button, direction, enabled in (
            (self._prev_button, -1, self.page > 0),
            (self._next_button, 1, self.page < self.pages - 1),
        ):
            inner = button.inflate(-6, -6)
            pressed = self._pressed == button
            pygame.draw.rect(
                surface, BTN_ACTIVE if pressed else BTN_NORMAL, inner,
                border_radius=8,
            )
            # Drawn, not typed: the default font has no guaranteed arrows.
            cx, cy = inner.center
            w, h = 9, 6 * direction
            pygame.draw.polygon(
                surface, TEXT_PRIMARY if enabled else TEXT_DISABLED,
                [(cx - w, cy - h), (cx + w, cy - h), (cx, cy + h)],
            )
        for i, line in enumerate((str(self.page + 1), "/", str(self.pages))):
            text = self._font.render(line, True, TEXT_SECONDARY)
            y = self._strip.centery + (i - 1) * text.get_height()
            surface.blit(text, text.get_rect(center=(self._strip.centerx, y)))

    def handle_event(self, event: UIEvent) -> bool:
        if self._captured is not None:
            handled = self._captured.handle_event(event)
            if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP):
                self._captured = None
            return handled

        if event.type == pygame.MOUSEWHEEL and event.dy:
            # A wheel turns pages on the desktop build: one notch, one page.
            self.turn(-1 if event.dy > 0 else 1)
            return True

        if event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            if not self.rect.collidepoint(event.pos):
                return False
            if self._strip.collidepoint(event.pos):
                self._pressed = next(
                    (b for b in (self._prev_button, self._next_button)
                     if b.collidepoint(event.pos)),
                    None,
                )
                return True
            for widget in self.visible:
                if widget.rect.collidepoint(event.pos) and widget.handle_event(event):
                    self._captured = widget
                    return True
            return False

        if self._pressed is not None and event.type in (
            pygame.FINGERUP, pygame.MOUSEBUTTONUP
        ):
            pressed, self._pressed = self._pressed, None
            if pressed.collidepoint(event.pos):
                self.turn(-1 if pressed == self._prev_button else 1)
            return True
        return False


class ParamsScreen(Screen):
    """Header + a paged grid of the plugin's controls, with a Reset that returns
    every control to its baseline.

    Used for both halves of a chain. An LV2 instrument is a plugin with control
    ports exactly like an effect is, so the instrument gets this same screen
    rather than one of its own — `on_swap` and `on_remove` are what differ, since
    a rig can change its instrument but an effect is removed instead.
    """

    def __init__(
        self,
        name: str,
        ports: list[ControlPort],
        values: dict[str, float],
        on_change: Callable[[str, float], None],
        on_back: Callable,
        on_reset: Callable | None = None,
        on_swap: Callable | None = None,
        on_remove: Callable | None = None,
        on_settings: Callable | None = None,
    ):
        font_large = pygame.font.Font(None, 36)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
            action_label="Reset" if on_reset else "",
            on_action=on_reset,
            # Where swapping the instrument went when tapping its block came to
            # mean "edit it", like every other block on the wire.
            action2_label="Change" if on_swap else "",
            on_action2=on_swap,
            # Remove lives here, not on the chain's block: the block's tap is
            # "edit", its dot is bypass, and a mid-song mis-tap there must not
            # delete anything. Unlike Change, it can't be taken back.
            action3_label="Remove" if on_remove else "",
            on_action3=self._confirm_remove,
            on_settings=on_settings,
        )
        self.header.name = name
        self._on_remove = on_remove

        self.controls = ControlPanel(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            ports=ports,
            values=values,
            font=font_small,
            on_change=on_change,
        )
        self.components = (self.header, self.controls)

    def _confirm_remove(self) -> None:
        """Two taps: the first asks, the second removes. Removing throws away
        everything dialled into the effect, so one stray tap mustn't do it —
        the same guard Restart and Shut down have."""
        labels = [label for label, _cb in self.header.actions]
        if "Really?" in labels:
            self._on_remove()
            return
        self.header.actions = [
            ("Really?" if label == "Remove" else label, cb)
            for label, cb in self.header.actions
        ]
