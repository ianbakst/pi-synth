from abc import ABC, abstractmethod
from math import log


class Scale(ABC):
    min_value: float
    max_value: float

    def __init__(self, min_value: float, max_value: float):
        self.min_value = min_value
        self.max_value = max_value

    @abstractmethod
    def value_to_ratio(self, value: float) -> float:
        pass

    @abstractmethod
    def ratio_to_value(self, ratio: float) -> float:
        pass


class LinearScale(Scale):
    def value_to_ratio(self, value: float) -> float:
        return log(value / self.min_value) / log(self.max_value / self.min_value)

    def ratio_to_value(self, ratio: float) -> float:
        return self.min_value * (self.max_value / self.min_value) ** ratio


class LogScale(Scale):
    def value_to_ratio(self, value: float) -> float:
        return (value - self.min_value) / (self.max_value - self.min_value)
    
    def ratio_to_value(self, ratio: float) -> float:
        return self.min_value + ratio * (self.max_value - self.min_value)
