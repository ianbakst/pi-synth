"""
JackGraph: discover and patch the JACK port graph.

The control plane's *only* interaction with the audio graph is connecting and
disconnecting ports — a control action, never data. This wraps the `jack_*`
command-line tools (provided by jackd2), so it needs no native binding:

    jack_lsp        list ports (with -t types, -p properties, -c connections)
    jack_connect    connect two ports
    jack_disconnect disconnect two ports

Ports are *discovered* at runtime (by client / type / direction) rather than
hardcoded, so this adapts to whatever names the running engines actually
register — e.g. a fluidsynth client that carries a pid suffix, or mod-host's
plugin ports. It is deliberately swappable for the `JACK-Client` python binding
later without changing callers.

On a dev machine with no JACK running, every query returns empty and every
patch is a no-op — the tools just aren't found — so importing/using this off the
Pi is harmless.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# A runner executes a command (argv list) and returns (returncode, stdout).
# Injectable so tests can feed canned jack_lsp output without a JACK server.
Runner = Callable[[list[str]], "tuple[int, str]"]

_TIMEOUT = 5.0


def _subprocess_runner(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
        return p.returncode, p.stdout
    except FileNotFoundError:
        # jack_* not installed (e.g. dev machine) — treat as "empty graph".
        return 127, ""
    except subprocess.TimeoutExpired:
        logger.warning("jack tool timed out: %s", " ".join(cmd))
        return 1, ""


@dataclass
class Port:
    """A JACK port and the attributes we care about for patching."""

    name: str
    type: str = "other"          # "audio" | "midi" | "other"
    is_output: bool = False      # True = source (produces), False = sink (consumes)
    connections: list[str] = field(default_factory=list)

    @property
    def client(self) -> str:
        """The JACK client name — everything before the first ':'."""
        return self.name.split(":", 1)[0]


class JackGraph:
    def __init__(self, runner: Runner | None = None):
        self._run: Runner = runner or _subprocess_runner

    # ------------------------------------------------------------------
    # Snapshot + discovery
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Port]:
        """A fresh view of the whole graph: name -> Port (type, direction, conns).

        Three `jack_lsp` calls (types, properties, connections) merged by port
        name. Cheap for a control-plane op; the graph changes as engines start
        and stop, so callers should snapshot per operation and reuse within it.
        """
        ports: dict[str, Port] = {}

        # -t: each port name followed by an indented type description line.
        for name, attrs in self._grouped(self._lsp("-t")):
            blob = " ".join(attrs).lower()
            if "audio" in blob:
                ptype = "audio"
            elif "midi" in blob:
                ptype = "midi"
            else:
                ptype = "other"
            ports.setdefault(name, Port(name)).type = ptype

        # -p: each port name followed by an indented "properties: output,..." line.
        for name, attrs in self._grouped(self._lsp("-p")):
            blob = " ".join(attrs).lower()
            ports.setdefault(name, Port(name)).is_output = "output" in blob

        # -c: each port name followed by its connected ports (indented).
        for name, attrs in self._grouped(self._lsp("-c")):
            ports.setdefault(name, Port(name)).connections = list(attrs)

        return ports

    def ports(
        self,
        *,
        client: str | None = None,
        type: str | None = None,
        is_output: bool | None = None,
        contains: str | None = None,
        snapshot: dict[str, Port] | None = None,
    ) -> list[str]:
        """Port names matching the given filters.

        `client` matches case-insensitively as a prefix of the client segment
        (so "fluidsynth" also matches "fluidsynth-01"). `contains` matches a
        substring of the full port name (case-insensitive).
        """
        snap = snapshot if snapshot is not None else self.snapshot()
        out: list[str] = []
        for name, p in snap.items():
            if client is not None and not p.client.lower().startswith(client.lower()):
                continue
            if type is not None and p.type != type:
                continue
            if is_output is not None and p.is_output != is_output:
                continue
            if contains is not None and contains.lower() not in name.lower():
                continue
            out.append(name)
        return sorted(out)

    def keyboard_midi_sources(
        self, snapshot: dict[str, Port] | None = None
    ) -> list[str]:
        """Hardware keyboard MIDI, bridged into JACK by a2jmidid: the a2j
        *capture* ports (MIDI outputs that produce the keyboard's notes)."""
        return self.ports(
            client="a2j",
            type="midi",
            is_output=True,
            contains="capture",
            snapshot=snapshot,
        )

    def dac_sinks(self, snapshot: dict[str, Port] | None = None) -> list[str]:
        """The DAC: system playback ports (audio inputs you connect *into*)."""
        return self.ports(
            client="system", type="audio", is_output=False, snapshot=snapshot
        )

    # ------------------------------------------------------------------
    # Patching
    # ------------------------------------------------------------------

    def connect(self, src: str, dst: str) -> bool:
        """Connect src -> dst. Idempotent: already-connected counts as success."""
        rc, _ = self._run(["jack_connect", src, dst])
        if rc == 0:
            return True
        # jack_connect returns nonzero when the ports are already connected (or
        # a port is missing) — verify which.
        if self.is_connected(src, dst):
            return True
        logger.warning("jack_connect failed: %s -> %s", src, dst)
        return False

    def disconnect(self, src: str, dst: str) -> bool:
        """Disconnect src -> dst. A non-existent connection is treated as done."""
        rc, _ = self._run(["jack_disconnect", src, dst])
        if rc == 0:
            return True
        return not self.is_connected(src, dst)

    def is_connected(
        self, src: str, dst: str, snapshot: dict[str, Port] | None = None
    ) -> bool:
        snap = snapshot if snapshot is not None else self.snapshot()
        p = snap.get(src)
        return p is not None and dst in p.connections

    # ------------------------------------------------------------------
    # Readiness
    # ------------------------------------------------------------------

    def wait_for(
        self,
        *,
        client: str,
        type: str | None = None,
        is_output: bool | None = None,
        timeout: float = 3.0,
        interval: float = 0.05,
    ) -> bool:
        """Poll until at least one matching port appears, or timeout. Used to
        confirm an engine has registered its JACK ports after start()."""
        deadline = time.monotonic() + timeout
        while True:
            if self.ports(client=client, type=type, is_output=is_output):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lsp(self, *args: str) -> str:
        rc, out = self._run(["jack_lsp", *args])
        return out if rc == 0 else ""

    @staticmethod
    def _grouped(output: str) -> Iterator[tuple[str, list[str]]]:
        """Parse `jack_lsp` output: a non-indented line is a port name; the
        indented lines under it are its attributes (type / properties / a
        connected port), until the next port name."""
        name: str | None = None
        attrs: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            if line[0].isspace():
                attrs.append(line.strip())
            else:
                if name is not None:
                    yield name, attrs
                name, attrs = line.strip(), []
        if name is not None:
            yield name, attrs
