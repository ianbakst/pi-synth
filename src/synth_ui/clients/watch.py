"""Two things that used to leave the box silent until a rig was changed, and
which it now notices and fixes by itself.

**A keyboard that appears after the graph was wired.** Keyboards are patched
into the instrument when a rig loads. One that shows up later — plugged in,
switched on, woken from Auto Off, replugged — was left unconnected, and its keys
were dead until the rig was changed or Settings -> Reconnect was tapped.
KeyboardWatcher does that tap itself.

**mod-host restarting underneath the UI.** mod-host holds every plugin; when it
crashes, systemd restarts it empty, and the UI — which has no way to rebuild in
place — went on believing its rig was loaded. HostWatcher notices mod-host's
process change and ends the UI, which systemd restarts; startup is the path that
already builds the whole graph from nothing and restores the last rig.

Both poll rather than listen for events: what matters is the state of the JACK
graph and of the service, and a keyboard's JACK port appears a moment after the
USB device does (a2jmidid has to bridge it), so an event would still need a
wait-and-look. Every POLL_S, on core 0 with the rest of the UI.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

POLL_S = 2.0


class _Poller:
    def __init__(self, interval: float):
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> bool:
        raise NotImplementedError

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:
                # A failed look (jack restarting, say) is the next tick's
                # problem; it must not end the watching.
                logger.exception("%s tick failed", type(self).__name__)


class KeyboardWatcher(_Poller):
    def __init__(
        self,
        find_unwired: Callable[[], list[str]],
        rewire: Callable[[], None],
        interval: float = POLL_S,
    ):
        """`find_unwired` runs on the watcher's thread and must only read.
        `rewire` is what to do about it — hand it to the UI thread; it is
        called at most once per tick."""
        super().__init__(interval)
        self._find_unwired = find_unwired
        self._rewire = rewire

    def tick(self) -> bool:
        """One look. Returns whether a re-wire was asked for."""
        unwired = self._find_unwired()
        if not unwired:
            return False
        logger.info("keyboard(s) not connected, re-wiring: %s", ", ".join(unwired))
        self._rewire()
        return True


def mod_host_pid() -> int:
    """mod-host's main PID, or 0 while it isn't running (or where there is no
    systemd, e.g. a dev machine). No root needed."""
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", "mod-host.service"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return int(out) if out.isdigit() else 0


class HostWatcher(_Poller):
    def __init__(
        self,
        on_restarted: Callable[[], None],
        read_pid: Callable[[], int] = mod_host_pid,
        interval: float = POLL_S,
    ):
        """`on_restarted` is called once, the first time mod-host is seen
        running as a different process from the one first seen."""
        super().__init__(interval)
        self._read_pid = read_pid
        self._on_restarted = on_restarted
        self._pid = 0
        self._fired = False

    def tick(self) -> bool:
        """One look. Returns whether mod-host was found to have restarted."""
        pid = self._read_pid()
        if pid == 0 or self._fired:
            return False            # down, mid-restart: wait for it to be back
        if self._pid == 0:
            self._pid = pid         # the process this UI built its graph in
            return False
        if pid == self._pid:
            return False
        logger.warning("mod-host restarted (pid %d -> %d); every plugin it held "
                       "is gone", self._pid, pid)
        self._fired = True
        self._on_restarted()
        return True
