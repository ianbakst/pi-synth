"""
mod-host LV2 plugin host client.

mod-host listens on a TCP socket (default port 5555).
Protocol: send "command\n", read until "resp <code>\0".
Response code 0 = OK, non-zero = error.
Connection is persistent (unlike FluidSynth which is per-command).
"""

import logging
from socket import AF_INET, SOCK_STREAM, socket

logger = logging.getLogger(__name__)

_RESP_TIMEOUT = 3.0


class ModHostClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 5555):
        self.host = host
        self.port = port
        self._sock: socket | None = None

    def _connect(self) -> bool:
        self._close()
        try:
            s = socket(AF_INET, SOCK_STREAM)
            s.settimeout(_RESP_TIMEOUT)
            s.connect((self.host, self.port))
            self._sock = s
            return True
        except Exception as e:
            logger.error("mod-host connect error: %s", e)
            self._sock = None
            return False

    def _close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send(self, cmd: str) -> str | None:
        """Send a command and return the response string, reconnecting if needed."""
        if not self._sock and not self._connect():
            return None
        try:
            self._sock.sendall((cmd + "\n").encode())
            buf = b""
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # mod-host terminates responses with a null byte
                if b"\x00" in buf:
                    break
        except Exception:
            self._close()
            return None
        return buf.decode(errors="replace").strip().rstrip("\x00")

    def _ok(self, cmd: str) -> bool:
        resp = self._send(cmd)
        if resp is None:
            logger.error("mod-host no response to: %s", cmd)
            return False
        if not resp.startswith("resp 0"):
            logger.error("mod-host error for %r: %s", cmd, resp)
            return False
        return True

    def load_plugin(self, uri: str, instance: int = 0) -> bool:
        """Add an LV2 plugin instance."""
        return self._ok(f"add {uri} {instance}")

    def remove_plugin(self, instance: int = 0) -> bool:
        """Remove an LV2 plugin instance."""
        return self._ok(f"remove {instance}")

    def set_param(self, instance: int, symbol: str, value: str) -> bool:
        """Set a plugin parameter by LV2 symbol."""
        return self._ok(f"param_set {instance} {symbol} {value}")

    def connect_ports(self, from_port: str, to_port: str) -> bool:
        """Connect two JACK ports."""
        return self._ok(f"connect {from_port} {to_port}")

    def disconnect_ports(self, from_port: str, to_port: str) -> bool:
        """Disconnect two JACK ports."""
        return self._ok(f"disconnect {from_port} {to_port}")

    def is_connected(self) -> bool:
        """Return True if mod-host is reachable."""
        if not self._sock and not self._connect():
            return False
        return True

    def close(self) -> None:
        self._close()
