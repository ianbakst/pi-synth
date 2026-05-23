"""
TCP client for communicating with a running FluidSynth server.

This module does NOT manage the FluidSynth process. FluidSynth runs
as an independent systemd service. This client connects to its TCP
shell (port 9800) to send commands like loading SoundFonts and
changing gain.

If the connection drops, it reconnects automatically on the next command.
"""

import socket
import sys

from config import FLUIDSYNTH_HOST, FLUIDSYNTH_PORT


class FluidSynthClient:
    """Persistent TCP connection to a running FluidSynth server."""

    def __init__(self, host=FLUIDSYNTH_HOST, port=FLUIDSYNTH_PORT, timeout=2):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        """Establish connection to FluidSynth."""
        self.close()
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            self.sock.recv(4096)  # consume welcome banner
            return True
        except Exception as e:
            print(f"FluidSynth connect error: {e}", file=sys.stderr)
            self.sock = None
            return False

    def send(self, cmd):
        """Send a command, reconnecting if needed. Returns response or None."""
        if not self.sock:
            if not self.connect():
                return None
        try:
            self.sock.sendall((cmd + "\n").encode())
            return self.sock.recv(4096).decode()
        except Exception:
            self.sock = None
            return None

    def close(self):
        """Close the connection."""
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class FluidSynthController:
    """High-level interface for controlling FluidSynth."""

    def __init__(self):
        self.client = FluidSynthClient()
        self.current_font = None
        self.gain = 2.0

    def load_soundfont(self, path):
        """Hot-swap the SoundFont in the running FluidSynth."""
        response = self.client.send(f"load {path}")
        if response is not None:
            self.client.send("select 0 1 0 0")
            self.client.send("reset")
            self.current_font = path
            return True
        return False

    def set_gain(self, gain):
        """Change volume instantly."""
        self.gain = gain
        self.client.send(f"gain {gain}")

    def is_connected(self):
        """Check if we can talk to FluidSynth."""
        return self.client.send("fonts") is not None
