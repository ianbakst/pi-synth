from typing import Callable

import pygame

from src.synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_H,
    BTN_MARGIN,
    BTN_NORMAL,
    BTN_PAD_X,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from .base import Component
from ..event import UIEvent
from ..utils import display_name, file_size_str


class VoiceList(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        soundfonts: list[str],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[int, str], None],
    ):
        super().__init__(rect)
        self.soundfonts = soundfonts
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_select = on_select

        self.selected_index: int = -1
        self.scroll_offset: int = 0
        self.loading: bool = False

        self._finger_moved: bool = False
        self._tracking_touch: bool = False

    def draw(self, surface: pygame.Surface) -> None:
        list_rect = pygame.Rect(
            self.rect.x,
            self.rect.y,
            self.rect.width - SCROLL_BAR_W,
            self.rect.height,
        )
        pygame.draw.rect(surface, BG, self.rect)

        if not self.soundfonts:
            text = self.font_medium.render(
                "No SoundFonts found in ~/soundfonts/", True, TEXT_SECONDARY
            )
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        total_h = len(self.soundfonts) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, sf_path in enumerate(self.soundfonts):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            color = BTN_ACTIVE if i == self.selected_index else BTN_NORMAL
            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, color, btn_rect, border_radius=6)

            name = display_name(sf_path)
            text_color = TEXT_ACTIVE if i == self.selected_index else TEXT_PRIMARY
            text = self.font_medium.render(name, True, text_color)
            max_text_w = btn_rect.width - 100
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, text_color)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            size_text = self.font_small.render(
                file_size_str(sf_path), True, TEXT_SECONDARY
            )
            clip.blit(size_text, (btn_rect.x + 12, btn_rect.y + 36))

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
        if self.loading or not self.soundfonts:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if 0 <= index < len(self.soundfonts) and index != self.selected_index:
            self.on_select(index, self.soundfonts[index])

    def handle_event(self, event: UIEvent) -> bool:
        if event.type == pygame.FINGERDOWN:
            if self.rect.collidepoint(event.pos):
                self._tracking_touch = True
                self._finger_moved = False

        elif event.type == pygame.FINGERMOTION:
            if self._tracking_touch:
                if abs(event.dy) > 2:
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
