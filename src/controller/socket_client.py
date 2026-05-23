"""TCP client for FluidSynth's command shell (-s mode)."""

import select
import sys
import time
from socket import AF_INET, SOCK_STREAM, socket as Socket

from config import cfg

_PROMPT = b">"


class SocketClient:
    """Persistent connection to FluidSynth's TCP shell.

    Connects once (retrying until FluidSynth is ready), then keeps the
    connection alive. Each send() blocks until FluidSynth writes the '>'
    prompt, which is the only signal that a command has completed.
    """

    def __init__(self, host: str = "127.0.0.1", port: int | None = None) -> None:
        self._host = host
        self._port = port or cfg.audio.fluidsynth_port
        self._sock: Socket | None = None

    def connect(self, timeout: float = 15.0) -> None:
        """Block until FluidSynth's shell is accepting connections."""
        self.close()
        deadline = time.monotonic() + timeout
        while True:
            try:
                s = Socket(AF_INET, SOCK_STREAM)
                s.settimeout(2.0)
                s.connect((self._host, self._port))
                s.settimeout(None)  # blocking after connect; select handles waits
                self._sock = s
                self._read_until_prompt()  # consume welcome banner
                return
            except (ConnectionRefusedError, ConnectionResetError):
                self._sock = None
                s.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"FluidSynth shell not ready after {timeout:.0f}s"
                    )
                time.sleep(0.2)
            except Exception:
                s.close()
                raise

    def send(self, cmd: str) -> str | None:
        if not self._sock:
            return None
        try:
            self._sock.sendall((cmd + "\n").encode())
            return self._read_until_prompt()
        except Exception as e:
            print(f"Command error: {e}", file=sys.stderr)
            self.close()
            return None

    def _read_until_prompt(self) -> str:
        assert self._sock
        buf = b""
        while not buf.rstrip().endswith(_PROMPT):
            ready, _, _ = select.select([self._sock], [], [], 30.0)
            if not ready:
                raise TimeoutError("FluidSynth stopped responding")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionResetError("FluidSynth closed the connection")
            buf += chunk
        return buf.decode(errors="replace")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
