"""
FluidSynth TCP shell client.

Two classes:
  FluidSynthConnection  — one TCP connection, silence-based framing
  FluidSynthClient      — one-shot-per-command wrapper (no persistent state)

Protocol (discovered empirically):
  - Connect → send "command\n" → read until silence → close
  - No banner is sent on connect
  - No prompt is sent after responses
  - Framing is purely by silence: when FluidSynth stops sending, it's done
"""

import logging
import socket

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 9800
DEFAULT_TIMEOUT = 5.0    # wait for first response byte (fast commands)
LOAD_TIMEOUT = 30.0      # wait for first response byte (soundfont load)
SILENCE = 0.2            # seconds of no data = response is complete


class FluidSynthConnection:
    """A single TCP connection to the FluidSynth shell."""

    def __init__(self, host=HOST, port=PORT, timeout=DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None

    def open(self):
        """Connect. Raises on failure."""
        logger.info("Connecting to FluidSynth at %s:%d", self.host, self.port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        self._sock = sock
        logger.info("Connected")
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
        Send one command, return the response text.
        Pass timeout to override the connection default for slow commands
        (e.g. 'load' which is silent while the soundfont is being read).
        """
        assert self._sock is not None, "send() called before open()"
        first_timeout = timeout if timeout is not None else self.timeout
        logger.debug(">>> %s", cmd)
        self._sock.sendall((cmd + "\n").encode())
        response = self._read_response(first_timeout)
        logger.debug("<<< %r", response)
        return response

    def _read_response(self, first_timeout):
        """
        Read until silence. Uses a long timeout for the first byte
        (FluidSynth may be busy) then a short silence gap to detect the end.
        """
        assert self._sock is not None
        buf = b""

        self._sock.settimeout(first_timeout)
        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                return ""
            buf += chunk
            logger.debug("recv %d bytes, total %d", len(chunk), len(buf))
        except socket.timeout:
            logger.warning("No response within %.1fs for current command", first_timeout)
            return None

        self._sock.settimeout(SILENCE)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                logger.debug("recv %d bytes, total %d", len(chunk), len(buf))
        except socket.timeout:
            pass

        return buf.decode(errors="replace").strip()

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()


class FluidSynthClient:
    """
    Stateless FluidSynth client. Opens a fresh connection per command.

    FluidSynth's TCP shell handles one connection at a time — holding a
    persistent connection would block all other callers.
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

    print("\n--- gain ---")
    print(client.send("gain"))

    sf = input("\nEnter soundfont path to test load (or blank to skip): ").strip()
    if sf:
        print("\n--- load ---")
        print(client.send(f"load {sf}", timeout=LOAD_TIMEOUT))
        print("\n--- select ---")
        print(client.send("select 0 1 0 0"))
        print("\n--- reset ---")
        print(client.send("reset"))
