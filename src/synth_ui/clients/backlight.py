"""Screen brightness, via the DSI panel's sysfs backlight device.

The panel is a Waveshare 4.3" DSI on the official 7-inch overlay, whose driver
exposes an I2C backlight controller at `/sys/class/backlight/0-0045` with a
0..255 range. Nothing about that is guaranteed, so this degrades to a no-op
rather than failing: a dev machine has no backlight, an HDMI build has none, and
the instrument must start and play either way.

**There is a floor, and it is not cosmetic.** Brightness 0 turns the backlight
fully off — a black screen on a dark stage, with the control you would need to
undo it now invisible. `MINIMUM` keeps the panel dim but readable, so the slider
can always be found again.
"""

from __future__ import annotations

import glob
import logging
import os

logger = logging.getLogger(__name__)

_CLASS_DIR = "/sys/class/backlight"

# 8% of a 255 range: clearly dimmed, still legible in a dark room. Low enough to
# be worth having, high enough that the screen never goes black.
MINIMUM_FRACTION = 0.08


class Backlight:
    """The panel's backlight, or a working stand-in for one that isn't there."""

    def __init__(self, class_dir: str = _CLASS_DIR):
        self._device = self._find(class_dir)
        self._maximum = self._read_max()
        self._minimum = max(1, int(self._maximum * MINIMUM_FRACTION))

    @staticmethod
    def _find(class_dir: str) -> str | None:
        devices = sorted(glob.glob(os.path.join(class_dir, "*")))
        if not devices:
            logger.info("no backlight device; brightness control disabled")
            return None
        # One panel, one device. If a board ever has two, the first is the
        # built-in one — an external monitor sorts later by bus id.
        return devices[0]

    def _read_max(self) -> int:
        if self._device is None:
            return 0
        try:
            with open(os.path.join(self._device, "max_brightness")) as f:
                return max(1, int(f.read().strip()))
        except (OSError, ValueError) as exc:
            logger.warning("backlight max_brightness unreadable: %s", exc)
            return 0

    @property
    def available(self) -> bool:
        return self._device is not None and self._maximum > 0

    @property
    def maximum(self) -> int:
        return self._maximum

    @property
    def minimum(self) -> int:
        """Never zero. See the module docstring: a black screen hides its own
        remedy."""
        return self._minimum

    def get(self) -> int:
        if not self.available:
            return 0
        try:
            with open(os.path.join(self._device, "brightness")) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return 0

    def set(self, value: int) -> bool:
        """Set brightness, clamped into [minimum, maximum]."""
        if not self.available:
            return False
        value = max(self._minimum, min(self._maximum, int(value)))
        try:
            with open(os.path.join(self._device, "brightness"), "w") as f:
                f.write(str(value))
            return True
        except OSError as exc:
            # Usually permissions: the node is root-owned unless the image's
            # udev rule granting the video group write access is installed.
            logger.warning("could not set brightness: %s", exc)
            return False

    def set_fraction(self, fraction: float) -> bool:
        return self.set(round(fraction * self._maximum))

    def fraction(self) -> float:
        return self.get() / self._maximum if self.available else 1.0
