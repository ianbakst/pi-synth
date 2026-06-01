import os
import shutil
import threading
from collections.abc import Callable

import pygame

from synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_H,
    BTN_MARGIN,
    BTN_NORMAL,
    BTN_PAD_X,
    HEADER_H,
    SCREEN_H,
    SCREEN_W,
    SCROLL_BAR_W,
    SLIDER_BG,
    SLIDER_FILL,
    SOUNDFONT_DIR,
    STATUS_ERR,
    STATUS_OK,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.components.header import Header
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.utils import display_name, file_size_str, scan_usb_soundfonts


class _USBList(Component):
    def __init__(
        self,
        rect: pygame.Rect,
        paths: list[str],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_copy: Callable[[str], None],
    ):
        super().__init__(rect)
        self.paths = paths
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_copy = on_copy

        # Track which basenames are installed (already in library or copied this session)
        lib = os.path.realpath(SOUNDFONT_DIR)
        self._installed: set[str] = {
            p for p in paths
            if os.path.exists(os.path.join(lib, os.path.basename(p)))
        }
        self._copying: str | None = None

        self.scroll_offset: int = 0
        self._finger_moved = False
        self._tracking_touch = False

    def mark_installed(self, path: str) -> None:
        self._installed.add(path)
        self._copying = None

    def mark_failed(self, path: str) -> None:
        self._copying = None

    def draw(self, surface: pygame.Surface) -> None:
        list_rect = pygame.Rect(
            self.rect.x, self.rect.y, self.rect.width - SCROLL_BAR_W, self.rect.height
        )
        pygame.draw.rect(surface, BG, self.rect)

        if not self.paths:
            lines = [
                "No SoundFonts found on USB.",
                "Mount a USB drive containing .sf2 or .sf3 files.",
            ]
            y = self.rect.y + 20
            for line in lines:
                text = self.font_medium.render(line, True, TEXT_SECONDARY)
                surface.blit(text, (self.rect.x + 20, y))
                y += text.get_height() + 8
            return

        total_h = len(self.paths) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - self.rect.height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = surface.subsurface(list_rect)

        for i, path in enumerate(self.paths):
            btn_y = -self.scroll_offset + i * (BTN_H + BTN_MARGIN)
            if btn_y + BTN_H < 0 or btn_y > self.rect.height:
                continue

            installed = path in self._installed
            copying = path == self._copying
            btn_color = BTN_ACTIVE if copying else BTN_NORMAL
            btn_rect = pygame.Rect(BTN_PAD_X, btn_y, list_rect.width - BTN_PAD_X * 2, BTN_H)
            pygame.draw.rect(clip, btn_color, btn_rect, border_radius=6)

            name = display_name(path)
            text = self.font_medium.render(name, True, TEXT_ACTIVE if copying else TEXT_PRIMARY)
            max_text_w = btn_rect.width - 150
            if text.get_width() > max_text_w:
                while text.get_width() > max_text_w and len(name) > 3:
                    name = name[:-4] + "..."
                    text = self.font_medium.render(name, True, TEXT_ACTIVE if copying else TEXT_PRIMARY)
            clip.blit(text, (btn_rect.x + 12, btn_rect.y + 10))

            if copying:
                sub = "Copying..."
                sub_color = TEXT_ACTIVE
            elif installed:
                sub = f"{file_size_str(path)}  ✓ Installed"
                sub_color = STATUS_OK
            else:
                sub = file_size_str(path)
                sub_color = TEXT_SECONDARY
            clip.blit(self.font_small.render(sub, True, sub_color), (btn_rect.x + 12, btn_rect.y + 36))

        if total_h > self.rect.height and max_scroll > 0:
            bar_x = self.rect.right - SCROLL_BAR_W
            bar_h = max(30, int(self.rect.height * self.rect.height / total_h))
            bar_y = self.rect.y + int(self.scroll_offset / max_scroll * (self.rect.height - bar_h))
            pygame.draw.rect(surface, SLIDER_BG, (bar_x, self.rect.y, SCROLL_BAR_W, self.rect.height))
            pygame.draw.rect(surface, SLIDER_FILL, (bar_x, bar_y, SCROLL_BAR_W, bar_h), border_radius=4)

    def _tap(self, x: int, y: int) -> None:
        if self.loading or not self.paths or self._copying is not None:
            return
        relative_y = y - self.rect.y + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))
        if 0 <= index < len(self.paths):
            path = self.paths[index]
            if path not in self._installed:
                self._copying = path
                self.on_copy(path)

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


class USBScreen(Screen):
    def __init__(
        self,
        on_back: Callable,
        on_copy_complete: Callable,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
        )
        self.header.name = "Import Soundfonts"

        paths = scan_usb_soundfonts(SOUNDFONT_DIR)
        self._usb_list = _USBList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H),
            paths=paths,
            font_medium=font_medium,
            font_small=font_small,
            on_copy=self._do_copy,
        )
        self._on_copy_complete = on_copy_complete
        self.components = (self.header, self._usb_list)

    def _do_copy(self, src: str) -> None:
        threading.Thread(target=self._copy_worker, args=(src,), daemon=True).start()

    def _copy_worker(self, src: str) -> None:
        dest = os.path.join(SOUNDFONT_DIR, os.path.basename(src))
        try:
            os.makedirs(SOUNDFONT_DIR, exist_ok=True)
            shutil.copy2(src, dest)
            self._usb_list.mark_installed(src)
            self._on_copy_complete()
        except Exception:
            self._usb_list.mark_failed(src)
