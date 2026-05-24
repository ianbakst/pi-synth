import os
import threading

import pygame

from config import (
    BG,
    DEFAULT_GAIN,
    FOOTER_H,
    FRAMEBUFFER,
    HEADER_H,
    IS_PI,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
    STATE_FILE,
    TOUCH_DEVICE,
)
from src.clients import FluidSynthController

from .header import Header
from .utils import display_name, scan_soundfonts
from .voice_list import VoiceList
from .volume_slider import VolumeSlider


class VoiceSwitcherUI:
    def __init__(self):
        if IS_PI:
            os.environ["SDL_FBDEV"] = FRAMEBUFFER
            os.environ["SDL_MOUSEDEV"] = TOUCH_DEVICE
            os.environ["SDL_MOUSEDRV"] = "TSLIB"

        pygame.init()

        if IS_PI:
            self.screen = pygame.display.set_mode(
                (SCREEN_W, SCREEN_H), pygame.FULLSCREEN
            )
            pygame.mouse.set_visible(False)
        else:
            self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.synth = FluidSynthController()
        self.running = True

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
        )
        self.voice_list = VoiceList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            soundfonts=scan_soundfonts(SOUNDFONT_DIR),
            font_medium=font_medium,
            font_small=font_small,
            on_select=self._on_voice_select,
        )
        self.volume_slider = VolumeSlider(
            rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
            initial_gain=DEFAULT_GAIN,
            font=font_small,
            on_change=self.synth.set_gain,
        )

    def _set_loading(self, loading: bool) -> None:
        self.header.loading = loading
        self.voice_list.loading = loading

    def _on_voice_select(self, index: int, path: str) -> None:
        self.voice_list.selected_index = index
        self.header.name = display_name(path)
        self._save_state(path)
        self._set_loading(True)
        threading.Thread(target=self._do_load, args=(path,), daemon=True).start()

    def _do_load(self, path: str) -> None:
        try:
            self.synth.load_soundfont(path)
        finally:
            self._set_loading(False)

    def _load_state(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE) as f:
                path = f.read().strip()
            if path in self.voice_list.soundfonts:
                index = self.voice_list.soundfonts.index(path)
                self.voice_list.selected_index = index
                self.header.name = display_name(path)
                self._set_loading(True)
                self.synth.load_soundfont(path)
        except Exception:
            pass
        finally:
            self._set_loading(False)

    def _save_state(self, path: str) -> None:
        try:
            with open(STATE_FILE, "w") as f:
                f.write(path)
        except Exception:
            pass

    def _draw(self) -> None:
        self.screen.fill(BG)
        self.header.draw(self.screen)
        self.voice_list.draw(self.screen)
        self.volume_slider.draw(self.screen)
        pygame.display.flip()

    def run(self) -> None:
        clock = pygame.time.Clock()
        self._draw()
        threading.Thread(target=self._load_state, daemon=True).start()

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.running = False
                else:
                    self.volume_slider.handle_event(event)
                    self.voice_list.handle_event(event)

            self._draw()
            clock.tick(30)

        pygame.quit()
