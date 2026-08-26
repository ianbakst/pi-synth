from collections.abc import Callable

import pygame

from synth_ui.clients.voice import Voice
from synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_DISABLED,
    BTN_H,
    BTN_MARGIN,
    BTN_NORMAL,
    BTN_PAD_X,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    TEXT_ACTIVE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent


class VoiceList(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        voices: list[Voice],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[int, Voice], None],
    ):
        super().__init__(rect)
        self.voices = voices
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

        if not self.voices:
            text = self.font_medium.render(
                "No voices found. Add instruments to ~/instruments/voices.json", True, TEXT_SECONDARY
            )
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        total_h = len(self.voices) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, voice in enumerate(self.voices):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            if not voice.available:
                color = BTN_DISABLED
            elif i == self.selected_index:
                color = BTN_ACTIVE
            else:
                color = BTN_NORMAL
            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, color, btn_rect, border_radius=6)

            name = voice.name
            if not voice.available:
                text_color = TEXT_DISABLED
            elif i == self.selected_index:
                text_color = TEXT_ACTIVE
            else:
                text_color = TEXT_PRIMARY
            text = self.font_medium.render(name, True, text_color)
            max_text_w = btn_rect.width - 100
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, text_color)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            # An unusable voice says why right in the list — the manifest ships
            # in the image and can outrun what's actually installed, so "file
            # missing" / "plugin not installed" belongs on screen, not in a log.
            if not voice.available:
                sub = voice.unavailable_reason
                sub_color = TEXT_DISABLED
            else:
                sub = (
                    f"{voice.category}  ·  {voice.engine}"
                    if voice.category
                    else voice.engine
                )
                sub_color = TEXT_SECONDARY
            sub_text = self.font_small.render(sub, True, sub_color)
            clip.blit(sub_text, (btn_rect.x + 12, btn_rect.y + 36))

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
        if self.loading or not self.voices:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if not 0 <= index < len(self.voices) or index == self.selected_index:
            return
        if not self.voices[index].available:
            return  # greyed out; the row already shows why
        self.on_select(index, self.voices[index])

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
