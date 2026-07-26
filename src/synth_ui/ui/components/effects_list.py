from collections.abc import Callable

import pygame

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.config import (
    BG,
    BTN_H,
    BTN_MARGIN,
    BTN_NORMAL,
    BTN_PAD_X,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    STATUS_ERR,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

_REMOVE_W = 48


class RackEffectsList(Component):
    """The loaded effects chain (in signal-flow order); each row has a remove-X."""

    def __init__(
        self,
        rect: pygame.Rect,
        effects: list[Effect],
        catalog: list[EffectCatalogEntry],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_remove: Callable[[int], None],
    ):
        super().__init__(rect)
        self.effects = effects
        self._names = {entry.uri: entry.name for entry in catalog}
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_remove = on_remove

        self.scroll_offset: int = 0
        self._finger_moved: bool = False
        self._tracking_touch: bool = False

    def _name_for(self, effect: Effect) -> str:
        return self._names.get(effect.uri, effect.uri)

    def draw(self, surface: pygame.Surface) -> None:
        list_rect = pygame.Rect(
            self.rect.x, self.rect.y, self.rect.width - SCROLL_BAR_W, self.rect.height
        )
        pygame.draw.rect(surface, BG, self.rect)

        if not self.effects:
            text = self.font_medium.render("No effects loaded", True, TEXT_SECONDARY)
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        total_h = len(self.effects) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, effect in enumerate(self.effects):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, BTN_NORMAL, btn_rect, border_radius=6)

            name = self._name_for(effect)
            text = self.font_medium.render(name, True, TEXT_PRIMARY)
            max_text_w = btn_rect.width - _REMOVE_W - 24
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, TEXT_PRIMARY)
            text_y = btn_rect.y + (btn_rect.height - text.get_height()) // 2
            clip.blit(text, (btn_rect.x + 12, text_y))

            remove_rect = self._remove_rect(btn_rect)
            pygame.draw.rect(clip, STATUS_ERR, remove_rect, border_radius=6)
            x_text = self.font_small.render("X", True, TEXT_ACTIVE)
            clip.blit(
                x_text,
                (
                    remove_rect.x + (remove_rect.width - x_text.get_width()) // 2,
                    remove_rect.y + (remove_rect.height - x_text.get_height()) // 2,
                ),
            )

        if total_h > self.rect.height and max_scroll > 0:
            bar_x = self.rect.right - SCROLL_BAR_W
            bar_h = max(30, int(self.rect.height * self.rect.height / total_h))
            bar_y = self.rect.y + int(
                self.scroll_offset / max_scroll * (self.rect.height - bar_h)
            )
            pygame.draw.rect(
                surface, SLIDER_BG, (bar_x, self.rect.y, SCROLL_BAR_W, self.rect.height)
            )
            pygame.draw.rect(
                surface,
                SLIDER_FILL,
                (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    def _remove_rect(self, btn_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(
            btn_rect.right - _REMOVE_W, btn_rect.y, _REMOVE_W, btn_rect.height
        )

    def _tap(self, x: int, y: int) -> None:
        if self.loading or not self.effects:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if not (0 <= index < len(self.effects)):
            return
        btn_y = index * (BTN_H + BTN_MARGIN) - self.scroll_offset
        btn_w = self.rect.width - SCROLL_BAR_W - BTN_PAD_X * 2
        btn_rect = pygame.Rect(BTN_PAD_X, btn_y, btn_w, BTN_H)
        remove_rect = self._remove_rect(btn_rect)
        relative_x = x - self.rect.x
        if remove_rect.collidepoint(relative_x, y - self.rect.y):
            self.on_remove(self.effects[index].instance)

    def handle_event(self, event: UIEvent) -> bool:
        if event.type == pygame.FINGERDOWN:
            if self.rect.collidepoint(event.pos):
                self._tracking_touch = True
                self._finger_moved = False
        elif event.type == pygame.FINGERMOTION:
            if self._tracking_touch and abs(event.dy) > 2:
                self._finger_moved = True
                self.scroll_offset -= event.dy
        elif event.type == pygame.FINGERUP:
            if self._tracking_touch:
                if not self._finger_moved and self.rect.collidepoint(event.pos):
                    self._tap(*event.pos)
                self._tracking_touch = False
                self._finger_moved = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self._tap(*event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.dy
        return False


class EffectsCatalogList(Component):
    """Browsable catalog of installed LV2 effects; tap a row to add it."""

    def __init__(
        self,
        rect: pygame.Rect,
        catalog: list[EffectCatalogEntry],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[EffectCatalogEntry], None],
    ):
        super().__init__(rect)
        self.catalog = catalog
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_select = on_select

        self.scroll_offset: int = 0
        self._finger_moved: bool = False
        self._tracking_touch: bool = False

    def draw(self, surface: pygame.Surface) -> None:
        list_rect = pygame.Rect(
            self.rect.x, self.rect.y, self.rect.width - SCROLL_BAR_W, self.rect.height
        )
        pygame.draw.rect(surface, BG, self.rect)

        if not self.catalog:
            text = self.font_medium.render("No effects installed", True, TEXT_SECONDARY)
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        total_h = len(self.catalog) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, entry in enumerate(self.catalog):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, BTN_NORMAL, btn_rect, border_radius=6)

            name = entry.name
            text = self.font_medium.render(name, True, TEXT_PRIMARY)
            max_text_w = btn_rect.width - 24
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, TEXT_PRIMARY)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            if entry.category:
                sub = self.font_small.render(entry.category, True, TEXT_SECONDARY)
                clip.blit(sub, (btn_rect.x + 12, btn_rect.y + 36))

        if total_h > self.rect.height and max_scroll > 0:
            bar_x = self.rect.right - SCROLL_BAR_W
            bar_h = max(30, int(self.rect.height * self.rect.height / total_h))
            bar_y = self.rect.y + int(
                self.scroll_offset / max_scroll * (self.rect.height - bar_h)
            )
            pygame.draw.rect(
                surface, SLIDER_BG, (bar_x, self.rect.y, SCROLL_BAR_W, self.rect.height)
            )
            pygame.draw.rect(
                surface,
                SLIDER_FILL,
                (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    def _tap(self, x: int, y: int) -> None:
        if self.loading or not self.catalog:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if 0 <= index < len(self.catalog):
            self.on_select(self.catalog[index])

    def handle_event(self, event: UIEvent) -> bool:
        if event.type == pygame.FINGERDOWN:
            if self.rect.collidepoint(event.pos):
                self._tracking_touch = True
                self._finger_moved = False
        elif event.type == pygame.FINGERMOTION:
            if self._tracking_touch and abs(event.dy) > 2:
                self._finger_moved = True
                self.scroll_offset -= event.dy
        elif event.type == pygame.FINGERUP:
            if self._tracking_touch:
                if not self._finger_moved and self.rect.collidepoint(event.pos):
                    self._tap(*event.pos)
                self._tracking_touch = False
                self._finger_moved = False
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self._tap(*event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.dy
        return False
