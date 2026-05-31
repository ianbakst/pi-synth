from abc import ABC, abstractmethod

from pygame import Rect, Surface

from synth_ui.ui.event import UIEvent


class Component(ABC):
    rect: Rect
    _loading: bool = False

    def __init__(self, rect: Rect):
        self.rect = rect

    @abstractmethod
    def draw(self, surface: Surface) -> None:
        pass

    def handle_event(self, event: UIEvent) -> bool:
        return False
    
    @property
    def loading(self) -> bool:
        return self._loading
    
    @loading.setter
    def loading(self, value: bool) -> None:
        self._loading = value
