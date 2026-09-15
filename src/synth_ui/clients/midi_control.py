"""Listen for MIDI control messages that change rigs — a footswitch, mostly.

This is the control plane, not the audio path. Notes never come through here:
they go keyboard -> a2jmidid/ttymidi -> JACK -> the instrument, with no Python
in the way, and that stays true. All this does is watch for a couple of
occasional messages and call back, which is the same job the touchscreen does.

Implemented by reading `aseqdump`, not by binding an ALSA client of our own.
That sounds like a shortcut and is actually the right call here:

  - it needs no new Python dependency (python-rtmidi/mido aren't packaged into
    the image, and this isn't worth adding one for);
  - the ALSA sequencer is publish/subscribe, so subscribing alongside a2jmidid
    doesn't take the port away from it;
  - a dead subprocess can't wedge the UI, and a crash is a restart rather than
    a hung thread inside the process that draws the screen.

The cost is a process and some line parsing, both of which are already how this
codebase talks to jack_lsp, lv2ls and aconnect.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

# aseqdump prints one event per line, e.g.
#   24:0   Control change          0, controller 64, value 127
#   24:0   Program change          0, program 5
_CONTROL_RE = re.compile(
    r"Control change\s+\d+, controller (\d+), value (\d+)", re.I
)
_PROGRAM_RE = re.compile(r"Program change\s+\d+, program (\d+)", re.I)


class MidiControlListener:
    """Watches every MIDI source for the configured next/previous controls.

    `next_cc` / `prev_cc` are Control Change numbers. A momentary footswitch
    sends value 127 on press and 0 on release; only the press is acted on, or
    every tap would step twice.
    """

    def __init__(
        self,
        on_next: Callable[[], None],
        on_previous: Callable[[], None],
        next_cc: int,
        prev_cc: int,
        on_program: Callable[[int], None] | None = None,
        runner: Callable[[], subprocess.Popen] | None = None,
    ):
        self.on_next = on_next
        self.on_previous = on_previous
        self.next_cc = next_cc
        self.prev_cc = prev_cc
        self.on_program = on_program
        self._runner = runner or self._spawn
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @staticmethod
    def _spawn() -> subprocess.Popen:
        # No -p: subscribe to everything, so a footswitch works whichever port
        # it arrives on and keeps working when it is unplugged and back in on a
        # different client number.
        return subprocess.Popen(
            ["aseqdump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def start(self) -> bool:
        if self._thread is not None:
            return True
        try:
            self._process = self._runner()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("MIDI control listener unavailable: %s", exc)
            return False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None:
            self._process.terminate()
            self._process = None
        self._thread = None

    def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            if self._stop.is_set():
                return
            try:
                self.handle_line(line)
            except Exception:
                # A callback that raises must not kill the listener; losing rig
                # switching for the rest of the session is worse than one
                # dropped press.
                logger.exception("MIDI control callback failed")

    def handle_line(self, line: str) -> bool:
        """Act on one line of aseqdump output. Returns True if it did."""
        control = _CONTROL_RE.search(line)
        if control:
            number, value = int(control.group(1)), int(control.group(2))
            # Press only. A momentary switch also sends 0 on release, and
            # acting on both would step two rigs per tap.
            if value == 0:
                return False
            if number == self.next_cc:
                self.on_next()
                return True
            if number == self.prev_cc:
                self.on_previous()
                return True
            return False

        program = _PROGRAM_RE.search(line)
        if program and self.on_program is not None:
            self.on_program(int(program.group(1)))
            return True
        return False
