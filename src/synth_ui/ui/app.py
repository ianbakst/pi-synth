import os

import pygame

from synth_ui.config import BG, FRAMEBUFFER, IS_PI, SCREEN_H, SCREEN_W, TOUCH_DEVICE
from synth_ui.clients import FluidSynthController
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.screens.home import HomeScreen
from synth_ui.ui.screens.splash import SplashScreen

SPLASH_DURATION_MS = 5000


class VoiceSwitcherUI:
    def __init__(self):
        if IS_PI:
            os.environ["SDL_FBDEV"] = FRAMEBUFFER
            os.environ["SDL_MOUSEDEV"] = TOUCH_DEVICE
            os.environ["SDL_MOUSEDRV"] = "TSLIB"

        pygame.init()

        if IS_PI:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        synth = FluidSynthController()
        self._home: Screen = HomeScreen(synth)
        self.screen: Screen = SplashScreen()
        self._splash_start = pygame.time.get_ticks()

    def _to_ui_event(self, event: pygame.event.Event) -> UIEvent | None:
        match event.type:
            case pygame.FINGERDOWN | pygame.FINGERUP:
                return UIEvent(event.type, pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)))
            case pygame.FINGERMOTION:
                return UIEvent(event.type, pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)), dy=int(event.dy * SCREEN_H))
            case pygame.MOUSEBUTTONDOWN | pygame.MOUSEBUTTONUP | pygame.MOUSEMOTION:
                return UIEvent(event.type, pos=event.pos)
            case pygame.MOUSEWHEEL:
                return UIEvent(event.type, dy=event.y * 40)
            case _:
                return None

    def run(self) -> None:
        clock = pygame.time.Clock()
        try:
            while True:
                for raw in pygame.event.get():
                    if raw.type == pygame.QUIT:
                        return
                    if raw.type == pygame.KEYDOWN and raw.key == pygame.K_ESCAPE:
                        return
                    if event := self._to_ui_event(raw):
                        self.screen.handle_event(event)

                if self.screen is not self._home:
                    if pygame.time.get_ticks() - self._splash_start >= SPLASH_DURATION_MS:
                        self.screen = self._home

                self.display.fill(BG)
                self.screen.draw(self.display)
                pygame.display.flip()
                clock.tick(30)
        finally:
            pygame.quit()
