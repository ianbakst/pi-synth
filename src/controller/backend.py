"""Audio backend abstraction.

`create_backend()` returns a `FluidSynthController` on a Raspberry Pi and a
`StubBackend` on any other platform, detected via the device-tree model file.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path


class AudioBackend(ABC):
    @abstractmethod
    def start(self, soundfont_path: str) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def set_gain(self, gain: float) -> None: ...

    @abstractmethod
    def is_running(self) -> bool: ...


class StubBackend(AudioBackend):
    """No-op backend for running the UI on a dev machine without hardware."""

    _running: bool = False

    def start(self, soundfont_path: str) -> None:
        print(f"[stub] start: {os.path.basename(soundfont_path)}")
        self._running = True

    def stop(self) -> None:
        print("[stub] stop")
        self._running = False

    def set_gain(self, gain: float) -> None:
        print(f"[stub] gain: {gain:.2f}")

    def is_running(self) -> bool:
        return self._running


def create_backend() -> AudioBackend:
    if Path("/sys/firmware/devicetree/base/model").exists():
        from controller.fluidsynth import FluidSynthController
        return FluidSynthController()
    return StubBackend()
