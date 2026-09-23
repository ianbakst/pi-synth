"""
mod-host LV2 plugin host client.

mod-host listens on a TCP socket (default port 5555).
Protocol: send "command\n", read until "resp <code>\0".
Response code 0 = OK, non-zero = error.
Connection is persistent (unlike FluidSynth which is per-command).
"""

import logging
import re
import threading
from socket import AF_INET, SOCK_STREAM, socket

logger = logging.getLogger(__name__)

_RESP_TIMEOUT = 3.0


class ModHostClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 5555):
        self.host = host
        self.port = port
        self._sock: socket | None = None
        # One socket, one command in flight. See _send.
        self._lock = threading.Lock()

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
        """Send a command and return the response string, reconnecting if needed.

        Serialised: this is one socket shared by every caller — instruments,
        effects, the master chain — and the protocol is strictly
        command-then-reply with no request ids to match them up. Two threads
        writing at once interleave, and each then reads whichever reply arrives
        first, so a load reports the result of somebody else's bypass. That is
        not hypothetical: warming a set while a rig was loading produced exactly
        that, with params set on instances that didn't exist yet.
        """
        with self._lock:
            return self._send_locked(cmd)

    def _send_locked(self, cmd: str) -> str | None:
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

    def _code(self, cmd: str) -> int | None:
        """The numeric status from mod-host's `resp <n>`, or None if there's no
        parseable reply."""
        resp = self._send(cmd)
        if resp is None:
            logger.error("mod-host no response to: %s", cmd)
            return None
        match = re.match(r"resp\s+(-?\d+)", resp)
        if not match:
            logger.error("mod-host unparseable reply to %r: %s", cmd, resp)
            return None
        return int(match.group(1))

    def load_plugin(self, uri: str, instance: int = 0) -> bool:
        """Add an LV2 plugin instance.

        NOT `_ok`: mod-host answers a successful `add` with the *instance
        number* (`resp 9` for instance 9), not `resp 0`; only a negative code is
        an error. Checking for `resp 0` meant every add succeeded only at
        instance 0 — invisible while every instrument lived there, and it broke
        every other slot the moment instruments became resident: only the one
        voice that happened to land at 0 would load, and the master chain (90)
        and effects rack (10+) never loaded at all.
        """
        code = self._code(f"add {uri} {instance}")
        if code is None:
            return False
        if code < 0:
            logger.error("mod-host refused %s at %d (resp %d)", uri, instance, code)
            return False
        return True

    def remove_plugin(self, instance: int = 0, missing_ok: bool = False) -> bool:
        """Remove an LV2 plugin instance.

        `missing_ok` is for clearing a slot defensively before an add: removing
        an instance that isn't there returns an error code, and logging that on
        every load would bury real failures in the journal under noise.
        """
        if not missing_ok:
            return self._ok(f"remove {instance}")
        code = self._code(f"remove {instance}")
        return code is not None

    def set_param(self, instance: int, symbol: str, value: str) -> bool:
        """Set a plugin parameter by LV2 symbol (a lv2:ControlPort)."""
        return self._ok(f"param_set {instance} {symbol} {value}")

    def bypass(self, instance: int, bypassed: bool) -> bool:
        """Bypass/unbypass a plugin instance.

        This is what makes instant voice switching possible: instruments stay
        instantiated and a switch flips which one is live, instead of unloading
        and re-instantiating (which is the slow part — an LV2 world scan and,
        for samplers, re-reading the library).

        Note we don't rely on bypass alone for silence, and bypass is not a
        skip: mod-host zeroes the buffers and calls the plugin's run() anyway,
        deliberately, so a bypassed delay's tail doesn't freeze. A bypassed
        instrument therefore still costs its DSP (~0.2% of a core, measured);
        what makes it silent is the zeroed output, and EngineManager also
        disconnects its MIDI so it gets no notes either way."""
        return self._ok(f"bypass {instance} {1 if bypassed else 0}")

    def preset_load(self, instance: int, preset_uri: str) -> bool:
        """Apply an LV2 preset (a preset URI from the plugin's own bundle) to an
        instance. This is what lets one plugin back many library voices — e.g.
        several drawbar registrations off a single organ plugin."""
        return self._ok(f"preset_load {instance} {preset_uri}")

    def patch_set(self, instance: int, property_uri: str, value: str) -> bool:
        """Set an LV2 patch property (atom-based), e.g. a plugin's instrument-file
        path. Distinct from set_param: file-loading params like sfizz's SFZ file
        are patch:writable properties, which param_set can't reach."""
        return self._ok(f"patch_set {instance} {property_uri} {value}")

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
