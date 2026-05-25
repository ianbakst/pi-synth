"""
FluidSynth TCP shell client.

Protocol (discovered empirically):
  Connect → send "command\n" → read until silence → close.
  No banner on connect. No prompt after responses.
  FluidSynth's shell handles one connection at a time.
"""

import logging
import sys

from synth_ui.clients.constants import (
    DEFAULT_PORT,
    FIRST_TIMEOUT,
    LOAD_TIMEOUT,
    LOCALHOST,
    SILENCE_TIMEOUT,
)
from synth_ui.clients.socket_client import SocketClient

logger = logging.getLogger(__name__)


class FluidSynthController:
    """High-level controller for a running FluidSynth instance."""

    _socket: SocketClient
    load_timeout: float

    def __init__(
        self,
        host: str = LOCALHOST,
        port: int = DEFAULT_PORT,
        timeout: float = FIRST_TIMEOUT,
        silence_timeout: float = SILENCE_TIMEOUT,
        load_timeout: float = LOAD_TIMEOUT,
    ):
        self._socket = SocketClient(
            host=host, port=port, timeout=timeout, silence_timeout=silence_timeout
        )
        self.load_timeout = load_timeout

    def load_soundfont(self, path: str, load_timeout: float | None = None) -> bool:
        """Load a soundfont file and select it on MIDI channel 0."""
        t = self.load_timeout if load_timeout is None else load_timeout
        if self._socket.send_command(f"load {path}", timeout=t) is None:
            logger.error("Failed to load soundfont: %s", path)
            return False
        self._socket.fire_command("select 0 1 0 0")
        self._socket.fire_command("reset")
        logger.info("Loaded soundfont: %s", path)
        return True

    def select_preset(
        self, channel: int, sfont_id: int, bank: int, preset: int
    ) -> None:
        """Select a specific bank/preset on a MIDI channel."""
        self._socket.send_command(f"select {channel} {sfont_id} {bank} {preset}")

    def set_gain(self, gain: float) -> None:
        """Set the master gain (0.0–5.0)."""
        self._socket.fire_command(f"gain {gain:.2f}")
        logger.info("Gain set to %.2f", gain)

    def list_fonts(self) -> str | None:
        """Return the list of loaded soundfonts, or None if unreachable."""
        return self._socket.send_command("fonts")

    def reset(self) -> None:
        """Reset all MIDI channels to defaults."""
        self._socket.send_command("reset")

    def is_connected(self) -> bool:
        """Return True if FluidSynth is reachable."""
        return self.list_fonts() is not None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    ctrl = FluidSynthController()

    print("\n--- fonts ---")
    print(ctrl.list_fonts())

    print("\n--- connected ---")
    print(ctrl.is_connected())

    sf = input("\nEnter soundfont path to test load (or blank to skip): ").strip()
    if sf:
        print("\n--- load ---")
        print(ctrl.load_soundfont(sf))
