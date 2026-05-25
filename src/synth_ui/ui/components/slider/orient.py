from abc import ABC, abstractmethod

from pygame import Rect


class Orientation(ABC):
    @abstractmethod
    def ratio_from_pos(self, rect: Rect, pos: tuple[int, int]) -> float:
        pass

    @abstractmethod
    def fill_rect(self, rect: Rect, ratio: float) -> Rect:
        pass

    @abstractmethod
    def knob_pos(self, rect: Rect, ratio: float) -> tuple[int, int]:
        pass


class HorizontalOrientation(Orientation):
    def ratio_from_pos(self, rect: Rect, pos: tuple[int, int]) -> float:
        return max(0.0, min(1.0, (pos[0] - rect.x) / rect.width))

    def fill_rect(self, rect: Rect, ratio: float) -> Rect:
        return Rect(rect.x, rect.y, int(ratio * rect.width), rect.height)

    def knob_pos(self, rect: Rect, ratio: float) -> tuple[int, int]:
        return (rect.x + int(ratio * rect.width), rect.centery)


class VerticalOrientation(Orientation):
    def ratio_from_pos(self, rect: Rect, pos: tuple[int, int]) -> float:
        return max(0.0, min(1.0, 1.0 - (pos[1] - rect.y) / rect.height))

    def fill_rect(self, rect: Rect, ratio: float) -> Rect:
        fill_h = int(ratio * rect.height)
        return Rect(rect.x, rect.bottom - fill_h, rect.width, fill_h)

    def knob_pos(self, rect: Rect, ratio: float) -> tuple[int, int]:
        return (rect.centerx, rect.bottom - int(ratio * rect.height))
