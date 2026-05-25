from abc import ABC, abstractmethod

from pygame import Rect, Surface

from src.synth_ui.ui.event import UIEvent


class Component(ABC):
    rect: Rect

    def __init__(self, rect: Rect):
        self.rect = rect

    @abstractmethod
    def draw(self, surface: Surface) -> None:
        pass

    def handle_event(self, event: UIEvent) -> bool:
        return False
