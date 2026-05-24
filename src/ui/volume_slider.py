from typing import Callable

import pygame

from config import (
    DIVIDER,
    MAX_GAIN,
    PANEL_BG,
    SLIDER_BG,
    SLIDER_FILL,
    SLIDER_KNOB,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

from .component import Component


class VolumeSlider(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        initial_gain: float,
        font: pygame.font.Font,
        on_change: Callable[[float], None],
    ):
        super().__init__(rect)
        self.gain = initial_gain
        self.font = font
        self.on_change = on_change
        self.dragging: bool = False

        self._track = pygame.Rect(
            self.rect.x + 16,
            self.rect.y + 38,
            self.rect.width - 32,
            24,
        )

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        pygame.draw.line(
            surface,
            DIVIDER,
            (self.rect.left, self.rect.top),
            (self.rect.right, self.rect.top),
        )

        surface.blit(
            self.font.render("Volume", True, TEXT_SECONDARY),
            (self.rect.x + 16, self.rect.y + 10),
        )

        vol_val = self.font.render(
            f"{int(self.gain / MAX_GAIN * 100)}%", True, TEXT_PRIMARY
        )
        surface.blit(
            vol_val, (self.rect.right - vol_val.get_width() - 16, self.rect.y + 10)
        )

        pygame.draw.rect(surface, SLIDER_BG, self._track, border_radius=12)

        fill_w = int((self.gain / MAX_GAIN) * self._track.width)
        pygame.draw.rect(
            surface,
            SLIDER_FILL,
            pygame.Rect(self._track.x, self._track.y, fill_w, self._track.height),
            border_radius=12,
        )

        pygame.draw.circle(
            surface,
            SLIDER_KNOB,
            (self._track.x + fill_w, self._track.y + self._track.height // 2),
            14,
        )

    def _set_gain_from_x(self, x: int) -> None:
        ratio = max(0.0, min(1.0, (x - self._track.x) / self._track.width))
        self.gain = ratio * MAX_GAIN

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.FINGERDOWN:
            x, y = self._finger_pos(event)
            if self._track.collidepoint(x, y):
                self.dragging = True
                self._set_gain_from_x(x)
                return True

        elif event.type == pygame.FINGERMOTION:
            if self.dragging:
                self._set_gain_from_x(self._finger_pos(event)[0])
                return True

        elif event.type == pygame.FINGERUP:
            if self.dragging:
                self.dragging = False
                self.on_change(self.gain)
                return True

        elif event.type == pygame.MOUSEBUTTONDOWN:
            if self._track.collidepoint(event.pos):
                self.dragging = True
                self._set_gain_from_x(event.pos[0])
                return True

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging:
                self._set_gain_from_x(event.pos[0])
                return True

        elif event.type == pygame.MOUSEBUTTONUP:
            if self.dragging:
                self.dragging = False
                self.on_change(self.gain)
                return True

        return False
