import logging
from contextlib import contextmanager
from socket import AF_INET, SOCK_STREAM, socket
from typing import Callable, Generator

from synth_ui.clients.constants import (
    DEFAULT_PORT,
    FIRST_TIMEOUT,
    LOCALHOST,
    SILENCE_TIMEOUT,
)

logger = logging.getLogger(__name__)


class SocketClient:
    host: str
    port: int
    factory: Callable[[], socket]

    def __init__(
        self,
        host: str = LOCALHOST,
        port: int = DEFAULT_PORT,
        timeout: float = FIRST_TIMEOUT,
        silence_timeout: float = SILENCE_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.factory = lambda: socket(AF_INET, SOCK_STREAM)
        self.timeout = timeout
        self.silence_timeout = silence_timeout

    @contextmanager
    def connect(self, timeout: float | None = None) -> Generator[socket, None, None]:
        """Connect and yield self. Closes the socket on exit."""
        t = timeout if timeout is not None else self.timeout
        logger.info("Connecting to %s:%d", self.host, self.port)
        sock = self.factory()
        sock.settimeout(t)
        sock.connect((self.host, self.port))
        logger.info("Connected")
        try:
            yield sock
        finally:
            sock.close()
            logger.debug("Disconnected from %s:%d", self.host, self.port)

    def _fire(self, data: bytes) -> None:
        """Send data and close immediately without reading a response."""
        with self.connect() as sock:
            sock.sendall(data)
            logger.debug("Fired %d bytes", len(data))

    def fire_command(self, cmd: str) -> None:
        logger.debug("Firing command: %s", cmd)
        self._fire(self._encode_cmd(cmd))

    def send_command(self, cmd: str, timeout: float | None = None) -> str | None:
        logger.debug("Sending command: %s", cmd)
        raw = self._send(self._encode_cmd(cmd), timeout=timeout)
        return raw.decode() if raw else None

    def _send(self, data: bytes, timeout: float | None = None) -> bytes:
        """Send data and read the response, all on one connection. Returns raw bytes."""
        with self.connect(timeout) as sock:
            sock.sendall(data)
            logger.debug("Sent %d bytes", len(data))
            buf = b""
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    return b""
                buf += chunk
                logger.debug("recv %d bytes", len(chunk))
            except TimeoutError:
                logger.warning("No data within %.1fs", timeout or self.timeout)
                return b""

            sock.settimeout(self.silence_timeout)
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    logger.debug("recv %d bytes, total %d", len(chunk), len(buf))
            except TimeoutError:
                pass
            return buf

    @staticmethod
    def _encode_cmd(cmd: str) -> bytes:
        return (cmd + "\n").encode()
