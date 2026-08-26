"""The saved-rig list — the instrument's main screen.

Each row is a whole sound: instrument plus its effects chain. The subtitle shows
what's in it, so you can tell "Gospel B3" from "Dry B3" without loading either.

A rig whose instrument isn't usable on this unit (missing sample library, plugin
never built — see clients/voice.py validate) is greyed and says why, rather than
loading to silence.
"""

from collections.abc import Callable

import pygame

from synth_ui.clients.rig import Rig
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

_REMOVE_W = 48


class RigList(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        rigs: list[Rig],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_select: Callable[[int, Rig], None],
        on_remove: Callable[[Rig], None],
        effect_names: dict[str, str] | None = None,
        unavailable: Callable[[Rig], str] | None = None,
    ):
        super().__init__(rect)
        self.rigs = rigs
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_select = on_select
        self.on_remove = on_remove
        # uri -> display name, from the effects catalog; falls back to the URI's
        # last path segment for anything not in the catalog.
        self._effect_names = effect_names or {}
        self._unavailable = unavailable or (lambda rig: "")

        self.selected_index: int = -1
        self.scroll_offset: int = 0
        self._finger_moved: bool = False
        self._tracking_touch: bool = False

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _summary(self, rig: Rig) -> str:
        """"Hammond B3 · reverb, EQ" — the instrument and what's stacked on it."""
        if not rig.effects:
            return rig.voice
        names = [self._effect_label(e.uri) for e in rig.effects]
        return f"{rig.voice}  ·  {', '.join(names)}"

    def _effect_label(self, uri: str) -> str:
        if uri in self._effect_names:
            return self._effect_names[uri]
        return uri.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]

    def draw(self, surface: pygame.Surface) -> None:
        list_rect = pygame.Rect(
            self.rect.x, self.rect.y, self.rect.width - SCROLL_BAR_W, self.rect.height
        )
        pygame.draw.rect(surface, BG, self.rect)

        if not self.rigs:
            text = self.font_medium.render(
                'No rigs yet — tap "New" to build one', True, TEXT_SECONDARY
            )
            surface.blit(text, (self.rect.x + 20, self.rect.y + 20))
            return

        total_h = len(self.rigs) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, rig in enumerate(self.rigs):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            reason = self._unavailable(rig)
            if reason:
                color, text_color = BTN_DISABLED, TEXT_DISABLED
            elif i == self.selected_index:
                color, text_color = BTN_ACTIVE, TEXT_ACTIVE
            else:
                color, text_color = BTN_NORMAL, TEXT_PRIMARY

            btn_rect = pygame.Rect(
                BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H
            )
            pygame.draw.rect(clip, color, btn_rect, border_radius=6)

            name = rig.name
            text = self.font_medium.render(name, True, text_color)
            max_text_w = btn_rect.width - _REMOVE_W - 24
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, text_color)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            sub = reason or self._summary(rig)
            sub_color = TEXT_DISABLED if reason else TEXT_SECONDARY
            sub_surf = self.font_small.render(sub, True, sub_color)
            max_sub_w = btn_rect.width - _REMOVE_W - 24
            if sub_surf.get_width() > max_sub_w:
                while sub_surf.get_width() > max_sub_w and len(sub) > 3:
                    sub = sub[:-4] + "..."
                    sub_surf = self.font_small.render(sub, True, sub_color)
            clip.blit(sub_surf, (btn_rect.x + 12, btn_rect.y + 36))

            x_surf = self.font_medium.render("x", True, TEXT_SECONDARY)
            clip.blit(
                x_surf,
                (
                    btn_rect.right - _REMOVE_W // 2 - x_surf.get_width() // 2,
                    btn_rect.y + (BTN_H - x_surf.get_height()) // 2,
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
                surface, SLIDER_FILL, (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _tap(self, x: int, y: int) -> None:
        if self.loading or not self.rigs:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if not 0 <= index < len(self.rigs):
            return
        rig = self.rigs[index]

        row_right = self.rect.x + self.rect.width - SCROLL_BAR_W - BTN_PAD_X
        if x >= row_right - _REMOVE_W:
            self.on_remove(rig)
            return

        if self._unavailable(rig):
            return  # greyed out; the row already shows why
        if index != self.selected_index:
            self.on_select(index, rig)

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
