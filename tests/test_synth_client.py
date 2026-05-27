"""Tests for FluidSynthClient and FluidSynthController."""

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

from synth_ui.clients.synth_client import FluidSynthController


class TestFluidSynthClient(unittest.TestCase):
    def test_send_returns_none_when_connect_fails(self):
        client = FluidSynthClient()
        with patch("socket.socket") as mock_sock_cls:
            mock_sock_cls.return_value.connect.side_effect = ConnectionRefusedError
            result = client.send("fonts")
        self.assertIsNone(result)

    def test_close_is_safe_when_not_connected(self):
        client = FluidSynthClient()
        client.close()  # should not raise

    def test_send_reconnects_after_dropped_connection(self):
        client = FluidSynthClient()
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b"FluidSynth>\n"
        with patch("socket.socket", return_value=mock_sock):
            client.connect()
            mock_sock.sendall.side_effect = BrokenPipeError
            result = client.send("fonts")
        self.assertIsNone(result)
        self.assertIsNone(client.sock)


class TestFluidSynthController(unittest.TestCase):
    def test_load_soundfont_returns_false_when_disconnected(self):
        ctrl = FluidSynthController()
        with patch.object(ctrl.client, "send", return_value=None):
            result = ctrl.load_soundfont("/path/to/font.sf2")
        self.assertFalse(result)

    def test_load_soundfont_returns_true_on_success(self):
        ctrl = FluidSynthController()
        with patch.object(ctrl.client, "send", return_value="ok"):
            result = ctrl.load_soundfont("/path/to/font.sf2")
        self.assertTrue(result)
        self.assertEqual(ctrl.current_font, "/path/to/font.sf2")

    def test_is_connected_false_when_send_returns_none(self):
        ctrl = FluidSynthController()
        with patch.object(ctrl.client, "send", return_value=None):
            self.assertFalse(ctrl.is_connected())

    def test_is_connected_true_when_send_returns_response(self):
        ctrl = FluidSynthController()
        with patch.object(ctrl.client, "send", return_value="ID  Filename\n"):
            self.assertTrue(ctrl.is_connected())


if __name__ == "__main__":
    unittest.main()
