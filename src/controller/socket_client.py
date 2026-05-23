"""TCP socket client for FluidSynth's command shell."""

import sys
import time
from socket import AF_INET, SOCK_STREAM, socket as Socket

from config import cfg


class SocketClient:
    """Persistent TCP connection to FluidSynth's command shell.

    Connects lazily on the first send and reconnects automatically if the
    connection drops. Use as a context manager to ensure the socket is closed.
    """

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        self._port = port or cfg.audio.fluidsynth_port
        self._sock: Socket | None = None

    def _connect(self) -> Socket:
        s = Socket(AF_INET, SOCK_STREAM)
        s.settimeout(2)
        s.connect((self._host, self._port))
        s.recv(4096)  # consume welcome banner
        self._sock = s
        return s

    def send(self, cmd: str) -> str | None:
        try:
            sock = self._sock or self._connect()
            sock.sendall((cmd + "\n").encode())
            time.sleep(0.1)
            return sock.recv(4096).decode()
        except Exception as e:
            print(f"Command error: {e}", file=sys.stderr)
            self.close()
            return None

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self) -> "SocketClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
