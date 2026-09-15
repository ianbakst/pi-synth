"""How a slider's position maps to its value.

Not yet used by Slider, which is linear throughout. Kept because the mapping is
the right place to solve the volume-taper problem: an audio level wants a
logarithmic fader, since equal finger movements should be equal *ratios*, not
equal numbers. See engine_manager._gain_to_db, which currently does that job in
the engine instead.

The two implementations here were previously swapped — LinearScale held the
logarithmic formula and vice versa. Nothing used them, so nothing broke, but
reaching for LinearScale would have given you a log fader.
"""

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
        """Where along the track (0..1) this value sits."""

    @abstractmethod
    def ratio_to_value(self, ratio: float) -> float:
        """The value at this point (0..1) along the track."""


class LinearScale(Scale):
    """Equal distance, equal increment. Right for pan, mix, bipolar trims."""

    def value_to_ratio(self, value: float) -> float:
        return (value - self.min_value) / (self.max_value - self.min_value)

    def ratio_to_value(self, ratio: float) -> float:
        return self.min_value + ratio * (self.max_value - self.min_value)


class LogScale(Scale):
    """Equal distance, equal ratio. Right for gain and frequency.

    min_value must be > 0: the ratio is undefined at zero, which is why a
    silence floor has to be handled by the caller rather than by the scale.
    """

    def value_to_ratio(self, value: float) -> float:
        return log(value / self.min_value) / log(self.max_value / self.min_value)

    def ratio_to_value(self, ratio: float) -> float:
        return self.min_value * (self.max_value / self.min_value) ** ratio
