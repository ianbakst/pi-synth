"""
Touchscreen UI for voice selection and volume control.

Renders directly to the framebuffer via Pygame. Handles both
touch (finger) events and mouse events for SSH/VNC testing.
"""

import glob
import os

import pygame

from config import cfg
from controller import AudioBackend, create_backend
from .colors import (
    BG,
    BTN_ACTIVE,
    BTN_NORMAL,
    DIVIDER,
    PANEL_BG,
    SLIDER_BG,
    SLIDER_FILL,
    SLIDER_KNOB,
    STATUS_ERR,
    STATUS_OK,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from .layout import BTN_H, BTN_MARGIN, BTN_PAD_X, FOOTER_H, HEADER_H, SCROLL_BAR_W


LIST_TOP = HEADER_H
LIST_BOTTOM = cfg.display.height - FOOTER_H
VISIBLE_AREA_H = LIST_BOTTOM - LIST_TOP


def scan_soundfonts(directory):
    """Recursively find all .sf2 and .sf3 files."""
    fonts = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        fonts.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
    fonts.sort(key=lambda f: os.path.basename(f).lower())
    return fonts


def display_name(path):
    """Clean up a SoundFont filename for display."""
    name = os.path.basename(path)
    name = os.path.splitext(name)[0]
    name = name.replace("_", " ").replace("-", " ")
    while "  " in name:
        name = name.replace("  ", " ")
    return name.strip()


def file_size_str(path):
    """Return human-readable file size."""
    size = os.path.getsize(path)
    if size < 1024 * 1024:
        return f"{size // 1024} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class VoiceSwitcherUI:
    """Touchscreen UI for voice selection."""

    def __init__(self):
        os.environ["SDL_FBDEV"] = cfg.display.framebuffer
        os.environ["SDL_MOUSEDEV"] = cfg.display.touch_device
        os.environ["SDL_MOUSEDRV"] = "TSLIB"

        pygame.init()
        pygame.mouse.set_visible(False)

        self.screen = pygame.display.set_mode(
            (cfg.display.width, cfg.display.height), pygame.FULLSCREEN
        )
        pygame.display.set_caption("MIDI Instrument")

        self.font_large = pygame.font.Font(None, 36)
        self.font_medium = pygame.font.Font(None, 28)
        self.font_small = pygame.font.Font(None, 22)

        self.soundfonts = scan_soundfonts(cfg.paths.soundfont_dir)
        self.selected_index = -1
        self.scroll_offset = 0
        self.gain = cfg.audio.default_gain
        self.dragging_slider = False
        self.running = True
        self.slider_rect = pygame.Rect(0, 0, 0, 0)

        self.synth: AudioBackend = create_backend()

        self._load_state()

    def _load_state(self):
        """Restore last selected SoundFont."""
        if os.path.exists(cfg.paths.state_file):
            try:
                with open(cfg.paths.state_file, "r") as f:
                    last_font = f.read().strip()
                if last_font in self.soundfonts:
                    self.selected_index = self.soundfonts.index(last_font)
                    self.synth.start(last_font)
            except Exception:
                pass

    def _save_state(self):
        """Save current selection."""
        if 0 <= self.selected_index < len(self.soundfonts):
            try:
                with open(cfg.paths.state_file, "w") as f:
                    f.write(self.soundfonts[self.selected_index])
            except Exception:
                pass

    def _draw_header(self):
        """Draw the top bar with current voice name."""
        W = cfg.display.width
        pygame.draw.rect(self.screen, PANEL_BG, (0, 0, W, HEADER_H))
        pygame.draw.line(
            self.screen, DIVIDER, (0, HEADER_H - 1), (W, HEADER_H - 1)
        )

        if 0 <= self.selected_index < len(self.soundfonts):
            name = display_name(self.soundfonts[self.selected_index])
            color = TEXT_ACTIVE
            status_color = STATUS_OK if self.synth.is_running() else STATUS_ERR
            pygame.draw.circle(
                self.screen, status_color, (20, HEADER_H // 2), 6
            )
        else:
            name = "No voice selected"
            color = TEXT_SECONDARY

        text = self.font_large.render(name, True, color)
        max_w = W - 50
        if text.get_width() > max_w:
            while text.get_width() > max_w and len(name) > 3:
                name = name[:-4] + "..."
                text = self.font_large.render(name, True, color)
        self.screen.blit(text, (36, (HEADER_H - text.get_height()) // 2))

    def _draw_voice_list(self):
        """Draw the scrollable list of SoundFonts."""
        W = cfg.display.width
        list_rect = pygame.Rect(0, LIST_TOP, W - SCROLL_BAR_W, VISIBLE_AREA_H)
        pygame.draw.rect(self.screen, BG, (0, LIST_TOP, W, VISIBLE_AREA_H))

        if not self.soundfonts:
            text = self.font_medium.render(
                "No SoundFonts found in ~/soundfonts/", True, TEXT_SECONDARY
            )
            self.screen.blit(text, (20, LIST_TOP + 20))
            return

        total_h = len(self.soundfonts) * (BTN_H + BTN_MARGIN)
        max_scroll = max(0, total_h - VISIBLE_AREA_H)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        clip = self.screen.subsurface(list_rect)
        y = -self.scroll_offset

        for i, sf_path in enumerate(self.soundfonts):
            btn_y = y + i * (BTN_H + BTN_MARGIN)

            if btn_y + BTN_H < 0 or btn_y > VISIBLE_AREA_H:
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

            size_str = file_size_str(sf_path)
            size_text = self.font_small.render(size_str, True, TEXT_SECONDARY)
            clip.blit(size_text, (btn_rect.x + 12, btn_rect.y + 36))

        if total_h > VISIBLE_AREA_H:
            bar_x = W - SCROLL_BAR_W
            bar_h = max(30, int(VISIBLE_AREA_H * VISIBLE_AREA_H / total_h))
            if max_scroll > 0:
                bar_y = LIST_TOP + int(
                    self.scroll_offset / max_scroll * (VISIBLE_AREA_H - bar_h)
                )
            else:
                bar_y = LIST_TOP
            pygame.draw.rect(
                self.screen,
                SLIDER_BG,
                (bar_x, LIST_TOP, SCROLL_BAR_W, VISIBLE_AREA_H),
            )
            pygame.draw.rect(
                self.screen,
                SLIDER_FILL,
                (bar_x, bar_y, SCROLL_BAR_W, bar_h),
                border_radius=4,
            )

    def _draw_footer(self):
        """Draw volume slider and controls."""
        W = cfg.display.width
        H = cfg.display.height
        max_gain = cfg.audio.max_gain
        footer_y = H - FOOTER_H
        pygame.draw.rect(self.screen, PANEL_BG, (0, footer_y, W, FOOTER_H))
        pygame.draw.line(self.screen, DIVIDER, (0, footer_y), (W, footer_y))

        vol_label = self.font_small.render("Volume", True, TEXT_SECONDARY)
        self.screen.blit(vol_label, (16, footer_y + 10))

        vol_pct = int(self.gain / max_gain * 100)
        vol_val = self.font_small.render(f"{vol_pct}%", True, TEXT_PRIMARY)
        self.screen.blit(vol_val, (W - vol_val.get_width() - 16, footer_y + 10))

        self.slider_rect = pygame.Rect(16, footer_y + 38, W - 32, 24)
        pygame.draw.rect(self.screen, SLIDER_BG, self.slider_rect, border_radius=12)

        fill_w = int((self.gain / max_gain) * self.slider_rect.width)
        fill_rect = pygame.Rect(
            self.slider_rect.x, self.slider_rect.y, fill_w, self.slider_rect.height
        )
        pygame.draw.rect(self.screen, SLIDER_FILL, fill_rect, border_radius=12)

        knob_x = self.slider_rect.x + fill_w
        knob_y = self.slider_rect.y + self.slider_rect.height // 2
        pygame.draw.circle(self.screen, SLIDER_KNOB, (knob_x, knob_y), 14)

    def _handle_list_tap(self, y):
        """Handle a tap in the voice list area."""
        if not self.soundfonts:
            return

        relative_y = y - LIST_TOP + self.scroll_offset
        index = int(relative_y / (BTN_H + BTN_MARGIN))

        if 0 <= index < len(self.soundfonts):
            if index != self.selected_index:
                self.selected_index = index
                self.synth.start(self.soundfonts[index])
                self._save_state()

    def _handle_slider(self, x):
        """Handle volume slider interaction."""
        relative_x = x - self.slider_rect.x
        ratio = max(0.0, min(1.0, relative_x / self.slider_rect.width))
        self.gain = ratio * cfg.audio.max_gain
        self.synth.set_gain(self.gain)

    def run(self):
        """Main event loop."""
        W = cfg.display.width
        H = cfg.display.height
        clock = pygame.time.Clock()
        finger_moved = False

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.running = False

                # --- Touch events ---
                elif event.type == pygame.FINGERDOWN:
                    finger_moved = False
                    x = int(event.x * W)
                    y = int(event.y * H)

                    if self.slider_rect.collidepoint(x, y):
                        self.dragging_slider = True
                        self._handle_slider(x)

                elif event.type == pygame.FINGERMOTION:
                    x = int(event.x * W)
                    y = int(event.y * H)

                    if self.dragging_slider:
                        self._handle_slider(x)
                    else:
                        dy = event.dy * H
                        if abs(dy) > 2:
                            finger_moved = True
                            self.scroll_offset -= int(dy)

                elif event.type == pygame.FINGERUP:
                    y = int(event.y * H)

                    if self.dragging_slider:
                        self.dragging_slider = False
                    elif not finger_moved and LIST_TOP <= y < LIST_BOTTOM:
                        self._handle_list_tap(y)

                    finger_moved = False

                # --- Mouse events (for SSH/VNC testing) ---
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    x, y = event.pos
                    if self.slider_rect.collidepoint(x, y):
                        self.dragging_slider = True
                        self._handle_slider(x)
                    elif LIST_TOP <= y < LIST_BOTTOM:
                        self._handle_list_tap(y)

                elif event.type == pygame.MOUSEBUTTONUP:
                    if self.dragging_slider:
                        self.dragging_slider = False

                elif event.type == pygame.MOUSEMOTION:
                    if self.dragging_slider:
                        self._handle_slider(event.pos[0])

                elif event.type == pygame.MOUSEWHEEL:
                    self.scroll_offset -= event.y * 40

            # Draw
            self.screen.fill(BG)
            self._draw_header()
            self._draw_voice_list()
            self._draw_footer()
            pygame.display.flip()

            clock.tick(30)

        # Cleanup
        self.synth.stop()
        pygame.quit()
