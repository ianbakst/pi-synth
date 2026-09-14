"""Tests for ModHostClient against a real socket speaking mod-host's protocol.

Everything else in the suite mocks `load_plugin` to return True, which is how a
wrong reading of mod-host's replies went unnoticed: nothing ever exercised the
parsing. This fake server answers the way mod-host does — `resp <n>` terminated
by a NUL — so the client's interpretation is actually tested.
"""

import socket
import threading

import pytest

from synth_ui.clients.mod_host_client import ModHostClient


class FakeModHost:
    """Replies to each line with whatever `handler(cmd)` returns, NUL-terminated."""

    def __init__(self, handler):
        self.handler = handler
        self.received: list[str] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self._srv.accept()
        buf = b""
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode()
                    self.received.append(cmd)
                    conn.sendall(self.handler(cmd).encode() + b"\0")

    def close(self):
        self._srv.close()


def mod_host_add_reply(cmd: str) -> str:
    """mod-host's real behaviour: a successful add returns the instance number."""
    parts = cmd.split()
    if parts[0] == "add":
        return f"resp {parts[2]}"
    return "resp 0"


@pytest.fixture
def host():
    servers = []

    def make(handler=mod_host_add_reply):
        srv = FakeModHost(handler)
        servers.append(srv)
        return srv, ModHostClient(port=srv.port)

    yield make
    for srv in servers:
        srv.close()


@pytest.mark.parametrize("instance", [0, 1, 9, 10, 90])
def test_add_succeeds_at_every_instance_not_just_zero(host, instance):
    # The bug: success was judged by `resp 0`, so only instance 0 ever loaded.
    # Slot 9 (soundfonts), 1 (a second resident voice), 10 (effects) and 90
    # (the master-chain limiter) all silently failed.
    _srv, mh = host()
    assert mh.load_plugin("urn:plugin", instance) is True


def test_a_negative_reply_is_a_failure(host):
    _srv, mh = host(lambda cmd: "resp -101")   # e.g. invalid URI
    assert mh.load_plugin("urn:nope", 3) is False


def test_an_unparseable_reply_is_a_failure(host):
    _srv, mh = host(lambda cmd: "garbage")
    assert mh.load_plugin("urn:plugin", 3) is False


def test_ordinary_commands_still_require_resp_zero(host):
    # Only `add` returns a value; the rest report 0 for success.
    _srv, mh = host(lambda cmd: "resp -3")
    assert mh.set_param(3, "gain", "0.5") is False
    _srv, mh = host(lambda cmd: "resp 0")
    assert mh.set_param(3, "gain", "0.5") is True


def test_removing_a_missing_instance_is_fine_when_asked(host):
    # Slots clear an instance defensively before adding; that must not count
    # as a failure (or log as one) when nothing was there.
    _srv, mh = host(lambda cmd: "resp -3")
    assert mh.remove_plugin(9, missing_ok=True) is True
    assert mh.remove_plugin(9) is False


def test_commands_go_out_one_per_line(host):
    srv, mh = host()
    mh.load_plugin("urn:a", 9)
    mh.bypass(9, False)
    assert srv.received == ["add urn:a 9", "bypass 9 0"]
