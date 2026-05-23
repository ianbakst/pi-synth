"""TCP socket client for FluidSynth's command shell."""

import sys
import time
from socket import AF_INET, SOCK_STREAM, socket as Socket

from config import cfg


class SocketClient:
    """Per-command TCP connection to FluidSynth's command shell.

    Opens a fresh connection for each send() call. FluidSynth's shell closes
    the connection after each response, so persistent connections break.
    """

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        self._port = port or cfg.audio.fluidsynth_port

    def send(self, cmd: str) -> str | None:
        try:
            with Socket(AF_INET, SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect((self._host, self._port))
                s.recv(4096)  # consume welcome banner
                s.sendall((cmd + "\n").encode())
                time.sleep(0.1)
                return s.recv(4096).decode()
        except Exception as e:
            print(f"Command error: {e}", file=sys.stderr)
            return None
