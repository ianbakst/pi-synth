"""A left rail of categories, for filtering a long list down to a usable one.

The voice catalogue is ~140 entries once the split GM set is discovered. As a
flat list that is two dozen screens of scrolling to reach an organ, and the
category each voice already carries goes unused. This turns it into two taps.

Counts are shown because they answer "is there anything in here" before you
look, and because a category with one item in it is worth knowing about.
"""

from collections.abc import Callable, Sequence

import pygame

from synth_ui.config import (
    BTN_ACTIVE,
    DIVIDER,
    PANEL_BG,
    TEXT_ACTIVE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

WIDTH = 150
ROW_H = 48
ALL = "All"


def categories_of(
    items: Sequence, key: Callable[[object], str]
) -> list[tuple[str, int]]:
    """(category, count) in a stable order, with "All" first.

    Alphabetical after that rather than by count: the rail is something you
    learn the shape of and then reach for without reading, which a
    frequency-ordered list would break every time the library changed.
    """
    counts: dict[str, int] = {}
    for item in items:
        name = key(item) or "Other"
        counts[name] = counts.get(name, 0) + 1
    return [(ALL, len(items))] + sorted(counts.items())


class CategoryRail(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        categories: list[tuple[str, int]],
        selected: str,
        font: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[str], None],
    ):
        super().__init__(rect)
        self.categories = categories
        self.selected = selected
        self.font = font
        self.font_small = font_small
        self.on_select = on_select
        self.scroll_offset = 0
        self._tracking = False
        self._moved = False

    @property
    def _total_h(self) -> int:
        return len(self.categories) * ROW_H

    @property
    def _max_scroll(self) -> int:
        return max(0, self._total_h - self.rect.height)

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll))

        previous = surface.get_clip()
        surface.set_clip(self.rect)
        for i, (name, count) in enumerate(self.categories):
            y = self.rect.y + i * ROW_H - self.scroll_offset
            if y + ROW_H < self.rect.y or y > self.rect.bottom:
                continue
            row = pygame.Rect(self.rect.x, y, self.rect.width, ROW_H)
            is_selected = name == self.selected
            if is_selected:
                pygame.draw.rect(surface, BTN_ACTIVE, row.inflate(-8, -6),
                                 border_radius=6)
            number = self.font_small.render(
                str(count), True, TEXT_ACTIVE if is_selected else TEXT_SECONDARY
            )
            # Draw the count first and fit the name into what's left: otherwise
            # a long category ("Electric Piano") runs straight under its number.
            available = row.width - number.get_width() - 32
            label = self._fit(
                name, TEXT_ACTIVE if is_selected else TEXT_PRIMARY, available
            )
            surface.blit(label, (row.x + 12, row.y + 8))
            surface.blit(number, (row.right - number.get_width() - 12, row.y + 12))
        surface.set_clip(previous)

        pygame.draw.line(
            surface, DIVIDER,
            (self.rect.right - 1, self.rect.y),
            (self.rect.right - 1, self.rect.bottom),
        )
        if self._max_scroll > 0:
            # Thin and muted, not the grid's blue bar: this is a hint that the
            # rail scrolls, and at full weight it reads as a divider between the
            # rail and the content rather than as a scrollbar.
            bar_w = 4
            bar_x = self.rect.right - bar_w - 3
            bar_h = max(24, int(self.rect.height * self.rect.height / self._total_h))
            bar_y = self.rect.y + int(
                self.scroll_offset / self._max_scroll * (self.rect.height - bar_h)
            )
            pygame.draw.rect(
                surface, TEXT_DISABLED, (bar_x, bar_y, bar_w, bar_h), border_radius=2
            )

    def _fit(self, text: str, color: tuple, width: int) -> pygame.Surface:
        surface = self.font.render(text, True, color)
        while surface.get_width() > width and len(text) > 2:
            text = text[:-2] + "\u2026"
            surface = self.font.render(text, True, color)
        return surface

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            if not self.rect.collidepoint(event.pos):
                return False
            self._tracking = True
            self._moved = False
            return True
        if event.type in (pygame.FINGERMOTION, pygame.MOUSEMOTION) and self._tracking:
            if abs(event.dy) > 2:
                self._moved = True
                self.scroll_offset -= int(event.dy)
            return True
        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP) and self._tracking:
            self._tracking = False
            if self._moved:
                return True
            index = (event.pos[1] - self.rect.y + self.scroll_offset) // ROW_H
            if 0 <= index < len(self.categories):
                name = self.categories[index][0]
                self.selected = name
                self.on_select(name)
            return True
        return False
