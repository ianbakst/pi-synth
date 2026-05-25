import os

import pygame
from pygame.surface import Surface

from synth_ui.config import BG, IMAGES_DIR, SCREEN_H, SCREEN_W
from synth_ui.ui.screens.base import Screen


class SplashScreen(Screen):
    components = ()

    def __init__(self, image_name: str = "splash.png"):
        path = os.path.join(IMAGES_DIR, image_name)
        raw = pygame.image.load(path).convert_alpha()
        self._image = pygame.transform.smoothscale(raw, (SCREEN_W, SCREEN_H))

    def draw(self, surface: Surface) -> None:
        surface.fill(BG)
        surface.blit(self._image, (0, 0))

    def handle_event(self, _event) -> None:
        pass
