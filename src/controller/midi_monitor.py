"""Hot-plug monitor for MIDI controllers via the ALSA sequencer announce port.

Calls libasound directly — no subprocess, no pipe parsing. The watch thread
blocks on select() and consumes zero CPU between plug/unplug events.
"""

import ctypes
import select
import sys
import threading
import time
from collections.abc import Callable

_lib = ctypes.CDLL("libasound.so.2", use_errno=True)

# snd_seq_open mode  (seq.h: OUTPUT=1, INPUT=2)
_SND_SEQ_OPEN_INPUT = 2
# port capabilities  (seq.h)
_SND_SEQ_PORT_CAP_WRITE      = 1 << 1
_SND_SEQ_PORT_CAP_SUBS_WRITE = 1 << 6
# port type
_SND_SEQ_PORT_TYPE_APPLICATION = 1 << 20
# event types  (seq_event.h: PORT_START=64, not 8 which is KEYPRESS)
_SND_SEQ_EVENT_PORT_START = 64
_POLLIN = 1


class _pollfd(ctypes.Structure):
    _fields_ = [
        ("fd",      ctypes.c_int),
        ("events",  ctypes.c_short),
        ("revents", ctypes.c_short),
    ]


class MidiHotplugMonitor:
    """Watches the ALSA sequencer announce port for new MIDI ports.

    Calls `on_connect` (after a brief settling delay) whenever a new port
    appears. Uses libasound directly via ctypes — no subprocess.
    """

    def __init__(self, on_connect: Callable[[], None], settle: float = 0.5) -> None:
        self._on_connect = on_connect
        self._settle = settle
        self._seq = ctypes.c_void_p(None)
        self._running = False

    def start(self) -> None:
        seq = ctypes.c_void_p(None)
        if _lib.snd_seq_open(ctypes.byref(seq), b"default", _SND_SEQ_OPEN_INPUT, 0) < 0:
            print("MIDI monitor: failed to open ALSA sequencer", file=sys.stderr)
            return

        _lib.snd_seq_set_client_name(seq, b"synth-midi-monitor")

        port = _lib.snd_seq_create_simple_port(
            seq,
            b"monitor",
            _SND_SEQ_PORT_CAP_WRITE | _SND_SEQ_PORT_CAP_SUBS_WRITE,
            _SND_SEQ_PORT_TYPE_APPLICATION,
        )
        if port < 0:
            print("MIDI monitor: failed to create sequencer port", file=sys.stderr)
            _lib.snd_seq_close(seq)
            return

        _lib.snd_seq_connect_from(seq, port, 0, 1)  # subscribe to announce port (0:1)

        self._seq = seq
        self._running = True
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self) -> None:
        self._running = False
        if self._seq.value:
            _lib.snd_seq_close(self._seq)
            self._seq = ctypes.c_void_p(None)

    def _watch(self) -> None:
        n = _lib.snd_seq_poll_descriptors_count(self._seq, _POLLIN)
        pfds = (_pollfd * n)()
        _lib.snd_seq_poll_descriptors(self._seq, pfds, n, _POLLIN)
        fds = [pfds[i].fd for i in range(n)]

        ev = ctypes.c_void_p(None)
        while self._running:
            try:
                if not select.select(fds, [], [], 1.0)[0]:
                    continue  # timeout — recheck _running
                if not self._running:
                    break
                if _lib.snd_seq_event_input(self._seq, ctypes.byref(ev)) < 0:
                    continue
                if not ev.value:
                    continue
                ev_type = ctypes.cast(ev.value, ctypes.POINTER(ctypes.c_uint8))[0]
                if ev_type == _SND_SEQ_EVENT_PORT_START:
                    time.sleep(self._settle)
                    self._on_connect()
            except Exception as e:
                if self._running:
                    print(f"MIDI monitor error: {e}", file=sys.stderr)
