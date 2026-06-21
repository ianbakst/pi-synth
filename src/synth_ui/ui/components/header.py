from collections.abc import Callable

import pygame

from synth_ui.config import BTN_NORMAL, DIVIDER, PANEL_BG, STATUS_ERR, TEXT_ACTIVE, TEXT_SECONDARY

from .base import Component
from synth_ui.ui.event import UIEvent

_BACK_W = 48
_ACTION_PAD = 12


class Header(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        font: pygame.font.Font,
        on_back: Callable | None = None,
        action_label: str | None = None,
        on_action: Callable | None = None,
    ):
        super().__init__(rect)
        self.font = font
        self.name: str | None = None
        self.error: bool = False
        self.on_back = on_back
        self.action_label = action_label
        self.on_action = on_action

    @Component.loading.setter
    def loading(self, value: bool) -> None:
        if value:
            self.error = False
        self._loading = value

    def _action_rect(self) -> pygame.Rect | None:
        if not self.action_label:
            return None
        label_surf = self.font.render(self.action_label, True, TEXT_ACTIVE)
        w = label_surf.get_width() + _ACTION_PAD * 2
        return pygame.Rect(self.rect.right - w - 8, self.rect.y + 10, w, self.rect.height - 20)

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        pygame.draw.line(
            surface,
            DIVIDER,
            (self.rect.left, self.rect.bottom - 1),
            (self.rect.right, self.rect.bottom - 1),
        )

        x = 16
        if self.on_back:
            arrow = self.font.render("<", True, TEXT_ACTIVE)
            surface.blit(arrow, (x, self.rect.y + (self.rect.height - arrow.get_height()) // 2))
            x += _BACK_W

        right_margin = 16
        action_r = self._action_rect()
        if action_r:
            pygame.draw.rect(surface, BTN_NORMAL, action_r, border_radius=6)
            label_surf = self.font.render(self.action_label, True, TEXT_ACTIVE)
            surface.blit(
                label_surf,
                (action_r.x + _ACTION_PAD, action_r.y + (action_r.height - label_surf.get_height()) // 2),
            )
            right_margin = self.rect.right - action_r.x + 8

        name = self.name or "No voice selected"
        if self.loading:
            color = TEXT_SECONDARY
        elif self.error:
            color = STATUS_ERR
        else:
            color = TEXT_ACTIVE if self.name else TEXT_SECONDARY

        text = self.font.render(name, True, color)
        max_w = self.rect.width - x - right_margin
        if text.get_width() > max_w:
            while text.get_width() > max_w and len(name) > 3:
                name = name[:-4] + "..."
                text = self.font.render(name, True, color)
        surface.blit(text, (x, self.rect.y + (self.rect.height - text.get_height()) // 2))

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONDOWN):
            if self.on_back:
                back_rect = pygame.Rect(self.rect.x, self.rect.y, _BACK_W, self.rect.height)
                if back_rect.collidepoint(event.pos):
                    self.on_back()
                    return True
            action_r = self._action_rect()
            if self.on_action and action_r and action_r.collidepoint(event.pos):
                self.on_action()
                return True
        return False
