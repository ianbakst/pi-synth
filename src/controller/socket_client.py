"""TCP socket client for FluidSynth's command shell."""

import sys
import time
from socket import AF_INET, SOCK_STREAM, socket as Socket

from config import cfg

_PROMPT = b">"


class SocketClient:
    """Persistent TCP connection to FluidSynth's command shell.

    Connects lazily on first use, retrying on ConnectionRefused so callers
    don't need to poll or sleep waiting for FluidSynth to start.
    """

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        self._port = port or cfg.audio.fluidsynth_port
        self._sock: Socket | None = None

    def _connect(self, timeout: float = 10.0) -> Socket:
        """Open connection, retrying on ConnectionRefused until FluidSynth is ready."""
        deadline = time.monotonic() + timeout
        while True:
            s = Socket(AF_INET, SOCK_STREAM)
            try:
                s.settimeout(min(2.0, max(0.1, deadline - time.monotonic())))
                s.connect((self._host, self._port))
                self._read_until_prompt(s)  # consume welcome banner
                self._sock = s
                return s
            except ConnectionRefusedError:
                s.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"FluidSynth shell not ready after {timeout:.0f}s"
                    )
            except Exception:
                s.close()
                raise

    def _read_until_prompt(self, sock: Socket) -> str:
        buf = b""
        while not buf.rstrip().endswith(_PROMPT):
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionResetError("FluidSynth closed the connection")
            buf += chunk
        return buf.decode(errors="replace")

    def send(self, cmd: str) -> str | None:
        for attempt in range(2):
            try:
                sock = self._sock or self._connect()
                sock.sendall((cmd + "\n").encode())
                return self._read_until_prompt(sock)
            except Exception as e:
                self.close()
                if attempt == 1:
                    print(f"Command error: {e}", file=sys.stderr)
        return None

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
