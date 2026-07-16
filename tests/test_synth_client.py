"""Tests for the FluidSynth client stack: SocketClient (raw per-command TCP) and
FluidSynthController (high-level commands). All sockets are mocked."""

from unittest.mock import MagicMock

from synth_ui.clients.socket_client import SocketClient
from synth_ui.clients.synth_client import FluidSynthController, Preset


def _socket_client(mock_sock: MagicMock) -> SocketClient:
    c = SocketClient()
    c.factory = lambda: mock_sock  # every connect() uses our mock socket
    return c


class TestSocketClient:
    def test_send_command_returns_decoded_response(self):
        sock = MagicMock()
        # one chunk of data, then silence (TimeoutError) ends the read
        sock.recv.side_effect = [b"22-000 Piano\n", TimeoutError()]
        c = _socket_client(sock)
        assert c.send_command("inst 1") == "22-000 Piano\n"
        sock.sendall.assert_called_once_with(b"inst 1\n")
        sock.close.assert_called_once()  # connection closed per command

    def test_send_command_returns_none_on_no_data(self):
        sock = MagicMock()
        sock.recv.return_value = b""
        assert _socket_client(sock).send_command("fonts") is None

    def test_fire_command_sends_without_reading(self):
        sock = MagicMock()
        c = _socket_client(sock)
        c.fire_command("reset")
        sock.sendall.assert_called_once_with(b"reset\n")
        sock.recv.assert_not_called()
        sock.close.assert_called_once()


class TestFluidSynthController:
    @staticmethod
    def _ctrl(sock_client: MagicMock) -> FluidSynthController:
        ctrl = FluidSynthController()
        ctrl._socket = sock_client
        return ctrl

    def test_load_soundfont_false_when_load_gets_no_response(self):
        s = MagicMock()
        s.send_command.return_value = None
        ctrl = self._ctrl(s)
        assert ctrl.load_soundfont("/f.sf2") is False
        s.fire_command.assert_not_called()  # bails before select/reset

    def test_load_soundfont_true_selects_and_resets(self):
        s = MagicMock()
        s.send_command.return_value = "ok"
        ctrl = self._ctrl(s)
        assert ctrl.load_soundfont("/f.sf2") is True
        s.send_command.assert_called_once()          # the load
        assert s.fire_command.call_count == 2         # select + reset

    def test_is_connected_reflects_fonts_response(self):
        s = MagicMock()
        s.send_command.return_value = "1  FluidR3_GM.sf2\n"
        assert self._ctrl(s).is_connected() is True
        s.send_command.return_value = None
        assert self._ctrl(s).is_connected() is False

    def test_set_gain_fires_formatted_gain_command(self):
        s = MagicMock()
        self._ctrl(s).set_gain(2.5)
        s.fire_command.assert_called_once_with("gain 2.50")

    def test_list_presets_parses_bank_prog_name_lines(self):
        s = MagicMock()
        s.send_command.return_value = "000-000 Piano\n000-001 Bright\nnot a preset\n"
        assert self._ctrl(s).list_presets() == [
            Preset(0, 0, "Piano"),
            Preset(0, 1, "Bright"),
        ]
