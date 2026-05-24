import logging
from socket import AF_INET, SOCK_STREAM, socket
from contextlib import contextmanager
from typing import Callable, Generator


logger = logging.getLogger(__name__)

LOCALHOST = "127.0.0.1"
DEFAULT_PORT = 9800
FIRST_TIMEOUT = 5.0
SILENCE_TIMEOUT = 0.2  # seconds of no data = response complete


class SocketClient:
    host: str
    port: int
    factory: Callable[[], socket]

    def __init__(
        self,
        host: str,
        port: int,
        first_timeout: float = FIRST_TIMEOUT,
        silence_timeout: float = SILENCE_TIMEOUT,
    ):
        self.host = host
        self.port = port
        self.factory = lambda: socket(AF_INET, SOCK_STREAM)
        self.first_timeout = first_timeout
        self.silence_timeout = silence_timeout

    @contextmanager
    def connect(self) -> Generator[socket, None, None]:
        """Connect and yield self. Closes the socket on exit."""
        logger.info("Connecting to %s:%d", self.host, self.port)
        sock = self.factory()
        sock.settimeout(self.first_timeout)
        sock.connect((self.host, self.port))
        logger.info("Connected")
        try:
            yield sock
        finally:
            sock.close()
            logger.debug("Disconnected from %s:%d", self.host, self.port)

    def send(self, data: bytes, first_timeout: float | None = None) -> bytes:
        """Send data and read the response, all on one connection. Returns raw bytes."""
        t = first_timeout if first_timeout is not None else self.first_timeout
        with self.connect() as sock:
            sock.sendall(data)
            logger.debug("Sent %d bytes", len(data))

            buf = b""
            sock.settimeout(t)
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    return b""
                buf += chunk
                logger.debug("recv %d bytes", len(chunk))
            except TimeoutError:
                logger.warning("No data within %.1fs", t)
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
