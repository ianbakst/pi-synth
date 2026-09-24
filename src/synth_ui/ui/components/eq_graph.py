"""An EQ as a curve with a handle per band, and the page that holds it.

Drag a handle and its band follows the finger: across is frequency, up and down
is gain. High- and low-pass handles only move across — they have no gain.
Dragging a band that is off turns it on, since moving it is asking to hear it.
Width (Q, bandwidth, resonance) has no gesture on a single-touch panel, so it is
a knob in the row beneath, alongside the selected band's on/off, frequency and
gain.

The curve is recomputed only when a band's settings change, not every frame —
it's a few thousand biquad evaluations, and the UI shares core 0 with the OS.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pygame
from pygame import Rect, Surface, gfxdraw
from pygame.font import Font

from synth_ui.clients.controls import Control
from synth_ui.clients.eq import (
    DB_RANGE,
    FREQ_HI,
    FREQ_LO,
    Band,
    EqLayout,
    is_enabled,
    log_freqs,
)
from synth_ui.config import (
    BTN_ACTIVE,
    BTN_NORMAL,
    DIVIDER,
    PANEL_BG,
    SLIDER_FILL,
    TEXT_ACTIVE,
    TEXT_DISABLED,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.components.controls import ControlWidget, widget_for
from synth_ui.ui.event import UIEvent

HANDLE_R = 14
# How far from a handle a touch still grabs it. Wider than the handle: a finger
# is, and bands close in frequency would otherwise be hard to tell apart.
GRAB_R = 36
_GRID_FREQS = (50, 100, 200, 500, 1000, 2000, 5000, 10000)
_FREQ_LABELS = {100: "100", 1000: "1k", 10000: "10k"}
_GRID_DB = (-12.0, -6.0, 0.0, 6.0, 12.0)

_DOWN = (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN)
_MOTION = (pygame.FINGERMOTION, pygame.MOUSEMOTION)
_UP = (pygame.FINGERUP, pygame.MOUSEBUTTONUP)


class EqGraph(Component):
    def __init__(
        self,
        rect: Rect,
        layout: EqLayout,
        values: dict[str, float],
        controls: dict[str, Control],
        font: Font,
        on_change: Callable[[str, float], None],
        on_select: Callable[[int], None],
    ):
        super().__init__(rect)
        self.layout = layout
        # Shared with the page, which owns it: the knobs beneath write the same
        # dict, so the curve follows them without being told.
        self.values = values
        self._controls = controls
        self.font = font
        self.on_change = on_change
        self.on_select = on_select
        self.selected = 0
        self._dragging = False
        self._grab_offset = (0, 0)
        self._cache_key: tuple | None = None
        self._curve: list[tuple[int, int]] = []
        self._band_curve: list[tuple[int, int]] = []
        self._fill: Surface | None = None

    # --- axes -------------------------------------------------------------

    @property
    def plot(self) -> Rect:
        """The graph proper, inside a margin for the axis labels."""
        return self.rect.inflate(-16, -28).move(0, -6)

    def x_for(self, freq: float) -> float:
        p = self.plot
        return p.x + math.log(freq / FREQ_LO) / math.log(FREQ_HI / FREQ_LO) * p.width

    def freq_for(self, x: float) -> float:
        p = self.plot
        ratio = max(0.0, min(1.0, (x - p.x) / p.width))
        return FREQ_LO * (FREQ_HI / FREQ_LO) ** ratio

    def y_for(self, db: float) -> float:
        p = self.plot
        db = max(-DB_RANGE, min(DB_RANGE, db))
        return p.centery - db / DB_RANGE * (p.height / 2)

    def db_for(self, y: float) -> float:
        p = self.plot
        db = (p.centery - y) / (p.height / 2) * DB_RANGE
        return max(-DB_RANGE, min(DB_RANGE, db))

    # --- handles ----------------------------------------------------------

    def handle_pos(self, band: Band) -> tuple[int, int]:
        freq = self.values[band.freq]
        db = self.layout.gain_db(band, self.values)
        return round(self.x_for(freq)), round(self.y_for(db))

    def _band_at(self, pos: tuple[int, int]) -> int | None:
        best, best_d = None, GRAB_R
        # Selected band first, so a tie goes to the one already being worked.
        order = [self.selected] + [
            i for i in range(len(self.layout.bands)) if i != self.selected
        ]
        for i in order:
            hx, hy = self.handle_pos(self.layout.bands[i])
            d = math.hypot(hx - pos[0], hy - pos[1])
            if d < best_d:
                best, best_d = i, d
        return best

    def select(self, index: int) -> None:
        if index != self.selected:
            self.selected = index
            self.on_select(index)

    def _set(self, symbol: str, value: float) -> None:
        control = self._controls.get(symbol)
        if control is not None:
            value = control.clamp(value)
        if self.values.get(symbol) != value:
            self.values[symbol] = value
            self.on_change(symbol, value)

    def _drag_to(self, pos: tuple[int, int]) -> None:
        band = self.layout.bands[self.selected]
        x = pos[0] + self._grab_offset[0]
        y = pos[1] + self._grab_offset[1]
        if not is_enabled(band, self.values):
            self._set(band.enable, 1.0)
        self._set(band.freq, self.freq_for(x))
        if band.has_gain:
            self._set(band.gain, self.layout.gain_value(self.db_for(y)))

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in _DOWN:
            if not self.rect.collidepoint(event.pos):
                return False
            index = self._band_at(event.pos)
            if index is None:
                return False
            self.select(index)
            hx, hy = self.handle_pos(self.layout.bands[index])
            # Keep the handle where it is under the finger rather than jumping
            # it to the touch point: a touch that grabs a handle's edge
            # shouldn't move the band.
            self._grab_offset = (hx - event.pos[0], hy - event.pos[1])
            self._dragging = True
            return True
        if not self._dragging:
            return False
        if event.type in _MOTION:
            self._drag_to(event.pos)
            return True
        if event.type in _UP:
            self._dragging = False
            return True
        return False

    # --- drawing ----------------------------------------------------------

    def _refresh_curves(self) -> None:
        key = (
            self.rect.topleft,
            self.selected,
            tuple(self.values.get(s) for s in sorted(self.layout.band_symbols)),
        )
        if key == self._cache_key:
            return
        self._cache_key = key
        freqs = log_freqs(max(2, self.plot.width // 3))
        total = self.layout.response(self.values, freqs)
        self._curve = [
            (round(self.x_for(f)), round(self.y_for(db))) for f, db in zip(freqs, total)
        ]
        band = self.layout.bands[self.selected]
        self._band_curve = (
            [
                (round(self.x_for(f)),
                 round(self.y_for(self.layout.band_db(band, self.values, f))))
                for f in freqs
            ]
            if is_enabled(band, self.values)
            else []
        )
        # The translucent area under the curve, built here rather than per
        # frame: an alpha surface is the costly part of drawing the graph.
        p = self.plot
        self._fill = Surface(p.size, pygame.SRCALPHA)
        zero = round(self.y_for(0.0)) - p.y
        local = [(x - p.x, y - p.y) for x, y in self._curve]
        pygame.draw.polygon(
            self._fill, (*SLIDER_FILL, 70),
            [(local[0][0], zero), *local, (local[-1][0], zero)],
        )

    def draw(self, surface: Surface) -> None:
        self._refresh_curves()
        pygame.draw.rect(surface, PANEL_BG, self.rect, border_radius=8)
        p = self.plot
        self._draw_grid(surface, p)

        previous = surface.get_clip()
        surface.set_clip(p)
        if self._fill is not None:
            surface.blit(self._fill, p.topleft)
        if len(self._band_curve) > 1:
            pygame.draw.aalines(surface, TEXT_SECONDARY, False, self._band_curve)
        if len(self._curve) > 1:
            pygame.draw.lines(surface, SLIDER_FILL, False, self._curve, 3)
        surface.set_clip(previous)

        for i, band in enumerate(self.layout.bands):
            if i != self.selected:
                self._draw_handle(surface, band, selected=False)
        self._draw_handle(surface, self.layout.bands[self.selected], selected=True)

    def _draw_grid(self, surface: Surface, p: Rect) -> None:
        for freq in _GRID_FREQS:
            x = round(self.x_for(freq))
            pygame.draw.line(surface, DIVIDER, (x, p.y), (x, p.bottom))
            if freq in _FREQ_LABELS:
                text = self.font.render(_FREQ_LABELS[freq], True, TEXT_DISABLED)
                surface.blit(text, text.get_rect(midtop=(x, p.bottom + 4)))
        for db in _GRID_DB:
            y = round(self.y_for(db))
            color = TEXT_DISABLED if db == 0 else DIVIDER
            pygame.draw.line(surface, color, (p.x, y), (p.right, y))
            if db:
                text = self.font.render(f"{db:+.0f}", True, TEXT_DISABLED)
                surface.blit(text, text.get_rect(midleft=(p.x + 4, y)))

    def _draw_handle(self, surface: Surface, band: Band, selected: bool) -> None:
        x, y = self.handle_pos(band)
        on = is_enabled(band, self.values)
        radius = HANDLE_R + (3 if selected else 0)
        color = BTN_ACTIVE if on else BTN_NORMAL
        gfxdraw.filled_circle(surface, x, y, radius, color)
        gfxdraw.aacircle(surface, x, y, radius, color)
        if selected:
            gfxdraw.aacircle(surface, x, y, radius, TEXT_ACTIVE)
            gfxdraw.aacircle(surface, x, y, radius - 1, TEXT_ACTIVE)
        text = self.font.render(
            band.label, True, TEXT_ACTIVE if on else TEXT_SECONDARY
        )
        surface.blit(text, text.get_rect(center=(x, y)))


class EqPage(Component):
    """The graph, with the selected band's controls in a row beneath it."""

    def __init__(
        self,
        rect: Rect,
        layout: EqLayout,
        values: dict[str, float],
        controls: dict[str, Control],
        font: Font,
        on_change: Callable[[str, float], None],
        cell_w: int,
        row_h: int,
    ):
        super().__init__(rect)
        self.layout = layout
        self.values = values
        self._controls = controls
        self._font = font
        self._on_change = on_change
        self._cell_w = cell_w
        self._row_h = row_h
        self._captured: Component | None = None
        self.graph = EqGraph(
            Rect(rect.x, rect.y, rect.width, rect.height - row_h),
            layout, values, controls, font, on_change, self._on_select,
        )
        self.band_widgets: list[ControlWidget] = []
        self._on_select(self.graph.selected)

    def _on_select(self, index: int) -> None:
        band = self.layout.bands[index]
        y = self.rect.bottom - self._row_h
        self.band_widgets = [
            widget_for(
                rect=Rect(self.rect.x + i * self._cell_w, y, self._cell_w, self._row_h),
                control=self._controls[symbol],
                value=self.values[symbol],
                on_change=(lambda v, s=symbol: self._widget_changed(s, v)),
                font=self._font,
            )
            for i, symbol in enumerate(band.symbols)
        ]

    def _widget_changed(self, symbol: str, value: float) -> None:
        self.values[symbol] = value
        self._on_change(symbol, value)

    def draw(self, surface: Surface) -> None:
        self.graph.draw(surface)
        for widget in self.band_widgets:
            # The graph moves these values too; a knob shows where its band is.
            widget.value = self.values[widget.control.symbol]
            widget.draw(surface)

    def handle_event(self, event: UIEvent) -> bool:
        if self._captured is not None:
            handled = self._captured.handle_event(event)
            if event.type in _UP:
                self._captured = None
            return handled
        if event.type in _DOWN:
            for target in (self.graph, *self.band_widgets):
                if target.rect.collidepoint(event.pos) and target.handle_event(event):
                    self._captured = target
                    return True
        return False
