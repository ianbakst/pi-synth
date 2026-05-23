"""
TCP client for communicating with a running FluidSynth server.

This module does NOT manage the FluidSynth process. FluidSynth runs
as an independent systemd service. This client connects to its TCP
shell (port 9800) to send commands like loading SoundFonts and
changing gain.

Each command opens a fresh connection, sends one command, drains the
response by waiting for silence, then closes. This avoids all prompt-
parsing and buffer-synchronisation problems inherent in keeping a
persistent connection to a human-oriented REPL.
"""

import socket
import sys
import time

from config import FLUIDSYNTH_HOST, FLUIDSYNTH_PORT

_CONNECT_TIMEOUT = 3.0   # TCP handshake
_BANNER_SILENCE = 0.2    # gap that means "banner is done"
_RESPONSE_TIMEOUT = 30.0 # wait for first byte of response (covers large soundfont loads)
_RESPONSE_SILENCE = 0.2  # gap that means "response is done"


def _drain(sock, first_timeout, silence):
    """Read from sock until a silence gap. Returns decoded text or None."""
    buf = b""
    sock.settimeout(first_timeout)
    try:
        chunk = sock.recv(4096)
        if not chunk:
            return ""
        buf += chunk
    except socket.timeout:
        return None

    sock.settimeout(silence)
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass

    return buf.decode(errors="replace")


class FluidSynthClient:
    """One-shot TCP commands to a running FluidSynth server."""

    def __init__(self, host=FLUIDSYNTH_HOST, port=FLUIDSYNTH_PORT):
        self.host = host
        self.port = port

    def send(self, cmd):
        """Open a connection, send one command, return response text or None."""
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=_CONNECT_TIMEOUT
            ) as sock:
                _drain(sock, first_timeout=2.0, silence=_BANNER_SILENCE)
                sock.sendall((cmd + "\n").encode())
                return _drain(sock, first_timeout=_RESPONSE_TIMEOUT, silence=_RESPONSE_SILENCE)
        except Exception as e:
            print(f"FluidSynth error: {e}", file=sys.stderr)
            return None


class FluidSynthController:
    """High-level interface for controlling FluidSynth."""

    def __init__(self):
        self.client = FluidSynthClient()
        self.current_font = None
        self.gain = 2.0
        self._connected = False
        self._last_connection_check = 0.0

    def load_soundfont(self, path):
        """Hot-swap the SoundFont in the running FluidSynth."""
        response = self.client.send(f"load {path}")
        if response is not None:
            self.client.send("select 0 1 0 0")
            self.client.send("reset")
            self.current_font = path
            self._connected = True
            return True
        self._connected = False
        return False

    def set_gain(self, gain):
        """Change volume."""
        self.gain = gain
        self.client.send(f"gain {gain:.2f}")

    def is_connected(self):
        """Return cached connection status, rechecked at most once every 5 seconds."""
        now = time.monotonic()
        if now - self._last_connection_check > 5.0:
            self._connected = self.client.send("fonts") is not None
            self._last_connection_check = now
        return self._connected
