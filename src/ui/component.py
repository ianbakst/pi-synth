from abc import ABC, abstractmethod

import pygame

from config import SCREEN_H, SCREEN_W


class Component(ABC):
    def __init__(self, rect: pygame.Rect):
        self.rect = rect

    @abstractmethod
    def draw(self, surface: pygame.Surface) -> None:
        pass

    def handle_event(self, event: pygame.event.Event) -> bool:
        return False

    def _finger_pos(self, event: pygame.event.Event) -> tuple[int, int]:
        return int(event.x * SCREEN_W), int(event.y * SCREEN_H)

    def _finger_dy(self, event: pygame.event.Event) -> float:
        return event.dy * SCREEN_H
