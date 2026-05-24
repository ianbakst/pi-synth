"""
FluidSynth TCP shell client.

Two classes:
  FluidSynthConnection  — one TCP connection, prompt-based framing
  FluidSynthClient      — persistent client that reconnects on failure

The FluidSynth shell sends "> " (greater-than space, no newline) after
every response. _read_until_prompt() accumulates recv() chunks until
that two-byte sequence appears at the end of the buffer.
"""

import logging
import socket

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9800
PROMPT = b"> "
DEFAULT_TIMEOUT = 5.0   # seconds; use a longer value for load commands


class FluidSynthConnection:
    """A single TCP connection to the FluidSynth shell."""

    def __init__(self, host=HOST, port=PORT, timeout=DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None

    def open(self):
        """Connect and consume the welcome banner. Raises on failure."""
        logger.info("Connecting to FluidSynth at %s:%d", self.host, self.port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        self._sock = sock
        banner = self._read_until_prompt()
        logger.info("Connected. Banner: %r", banner.splitlines()[0] if banner else "")
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            logger.debug("Connection closed")

    def send(self, cmd, timeout=None):
        """
        Send one command, return the response text (prompt stripped).
        Pass timeout to override the connection-level timeout for slow
        commands like 'load' that may take many seconds to respond.
        """
        assert self._sock is not None, "send() called before open()"
        old_timeout = self._sock.gettimeout()
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            logger.debug(">>> %s", cmd)
            self._sock.sendall((cmd + "\n").encode())
            response = self._read_until_prompt()
            logger.debug("<<< %s", response if response else "(empty)")
            return response
        finally:
            self._sock.settimeout(old_timeout)

    def _read_until_prompt(self):
        """
        Read from the socket until the FluidSynth prompt '> ' appears
        at the end of the buffer, then return everything before it.
        """
        assert self._sock is not None
        buf = b""
        while not buf.endswith(PROMPT):
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("FluidSynth closed the connection")
            buf += chunk
            logger.debug("recv %d bytes, total %d, tail=%r", len(chunk), len(buf), buf[-8:])
        text = buf[: -len(PROMPT)].decode(errors="replace").strip()
        return text

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()


class FluidSynthClient:
    """
    Stateless FluidSynth client. Opens a fresh connection per command.

    FluidSynth's TCP shell handles one connection at a time. Holding a
    persistent connection blocks every other caller. One-shot connections
    avoid this entirely and require no reconnect logic.
    """

    def __init__(self, host=HOST, port=PORT, timeout=DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send(self, cmd, timeout=None):
        """Open a connection, send one command, close, return response."""
        try:
            with FluidSynthConnection(self.host, self.port, self.timeout) as conn:
                return conn.send(cmd, timeout=timeout)
        except Exception as e:
            logger.error("Command %r failed: %s", cmd, e)
            return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    client = FluidSynthClient()

    print("\n--- fonts ---")
    print(client.send("fonts"))

    print("\n--- gain (read current) ---")
    print(client.send("gain"))

    sf = input("\nEnter soundfont path to test load (or blank to skip): ").strip()
    if sf:
        print("\n--- load ---")
        print(client.send(f"load {sf}", timeout=30.0))
        print("\n--- select ---")
        print(client.send("select 0 1 0 0"))
        print("\n--- reset ---")
        print(client.send("reset"))

