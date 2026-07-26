from collections.abc import Callable

import pygame

from synth_ui.config import (
    BTN_NORMAL,
    DIVIDER,
    PANEL_BG,
    STATUS_ERR,
    TEXT_ACTIVE,
    TEXT_SECONDARY,
)
from synth_ui.ui.event import UIEvent

from .base import Component

_BACK_W = 48
_ACTION_PAD = 12
_ACTION_GAP = 8


class Header(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        font: pygame.font.Font,
        on_back: Callable | None = None,
        action_label: str | None = None,
        on_action: Callable | None = None,
        action2_label: str | None = None,
        on_action2: Callable | None = None,
        action3_label: str | None = None,
        on_action3: Callable | None = None,
    ):
        super().__init__(rect)
        self.font = font
        self.name: str | None = None
        self.error: bool = False
        self.on_back = on_back
        # Actions render right-to-left in the order given (first = rightmost).
        self.actions: list[tuple[str, Callable]] = []
        if action_label and on_action:
            self.actions.append((action_label, on_action))
        if action2_label and on_action2:
            self.actions.append((action2_label, on_action2))
        if action3_label and on_action3:
            self.actions.append((action3_label, on_action3))

    @Component.loading.setter
    def loading(self, value: bool) -> None:
        if value:
            self.error = False
        self._loading = value

    def _action_rects(self) -> list[tuple[pygame.Rect, str, Callable]]:
        rects: list[tuple[pygame.Rect, str, Callable]] = []
        x_right = self.rect.right - _ACTION_GAP
        for label, cb in self.actions:
            w = self.font.render(label, True, TEXT_ACTIVE).get_width() + _ACTION_PAD * 2
            r = pygame.Rect(x_right - w, self.rect.y + 10, w, self.rect.height - 20)
            rects.append((r, label, cb))
            x_right = r.x - _ACTION_GAP
        return rects

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
            ay = self.rect.y + (self.rect.height - arrow.get_height()) // 2
            surface.blit(arrow, (x, ay))
            x += _BACK_W

        right_margin = 16
        action_rects = self._action_rects()
        for r, label, _cb in action_rects:
            pygame.draw.rect(surface, BTN_NORMAL, r, border_radius=6)
            label_surf = self.font.render(label, True, TEXT_ACTIVE)
            surface.blit(
                label_surf,
                (r.x + _ACTION_PAD, r.y + (r.height - label_surf.get_height()) // 2),
            )
        if action_rects:
            # leftmost action is last in the list
            right_margin = self.rect.right - action_rects[-1][0].x + _ACTION_GAP

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
        ty = self.rect.y + (self.rect.height - text.get_height()) // 2
        surface.blit(text, (x, ty))

    def handle_event(self, event: UIEvent) -> bool:
        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONDOWN):
            if self.on_back:
                back_rect = pygame.Rect(
                    self.rect.x, self.rect.y, _BACK_W, self.rect.height
                )
                if back_rect.collidepoint(event.pos):
                    self.on_back()
                    return True
            for r, _label, cb in self._action_rects():
                if r.collidepoint(event.pos):
                    cb()
                    return True
        return False
