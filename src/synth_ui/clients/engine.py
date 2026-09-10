"""
Engine layer: one uniform interface over every audio engine.

Each engine is either:
  - a ProcessEngine  — backed by a systemd unit (fluidsynth, pianoteq).
    start()/stop() are `systemctl start/stop`; RT priority + core pinning come
    from the unit file, never from Python.
  - a ModHostEngine  — an LV2 plugin in mod-host (sfizz, dexed). mod-host is
    on-demand like any ProcessEngine's unit: start()/stop() start/stop
    mod-host.service *and* add/remove the plugin over its socket. It is not
    always-running — see the ModHostEngine docstring for why.

Reality-driven divergence from the design doc: sfizz and dexed are NOT separate
engines. They share mod-host's single plugin slot and its stable JACK ports, so
one ModHostEngine handles both — switching between them is an in-place plugin
swap (`load`), not a JACK re-patch. The manager decides in-place-reload vs. full
switch by comparing `Engine.key` (fluidsynth|pianoteq|modhost).

A live engine's JACK ports are *discovered* through JackGraph (by client + type +
direction) rather than hardcoded, so this adapts to the names engines actually
register. NOTE: the exact client name mod-host uses for a plugin's *audio* output
ports should be confirmed on hardware (`jack_lsp -t`); if it isn't "mod-host",
change ModHostEngine.jack_client — discovery does the rest.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.lv2 import GENERIC_ENGINE
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.synth_client import FluidSynthController
from synth_ui.clients.voice import Voice
from synth_ui.config import SOUNDFONT_DIR

logger = logging.getLogger(__name__)

# Runs `sudo systemctl <action> <unit>` and returns the exit code. Injectable so
# tests don't shell out.
Systemctl = Callable[[list[str]], int]


def _default_systemctl(argv: list[str]) -> int:
    try:
        return subprocess.run(argv, timeout=15).returncode
    except FileNotFoundError:
        return 127
    except subprocess.TimeoutExpired:
        logger.error("timed out: %s", " ".join(argv))
        return 1


def _systemctl_unit(ctx: EngineContext, action: str, unit: str) -> None:
    rc = ctx.systemctl(["sudo", "systemctl", action, unit])
    if rc != 0:
        logger.error("systemctl %s %s failed (rc=%d)", action, unit, rc)


@dataclass
class EngineContext:
    """Dependencies shared by every engine (so construction is uniform)."""

    jack: JackGraph
    mod_host: ModHostClient
    fluidsynth: FluidSynthController
    # Which mod-host instance holds which instrument. Shared across engines and
    # across switches, which is what lets plugins stay loaded (see slots.py).
    slots: InstrumentSlots
    systemctl: Systemctl = _default_systemctl
    # EngineManager wires this to "the effects rack is non-empty" once it owns
    # one. Lets ModHostEngine.stop() leave mod-host.service running when
    # switching to a non-mod-host instrument with effects loaded, instead of
    # killing every loaded effect along with the instrument's own slot.
    mod_host_needed: Callable[[], bool] = lambda: False


class Engine(ABC):
    key: str = ""            # JACK-source identity, for same-source detection
    jack_client: str = ""    # client-name prefix used to discover this engine's ports

    def __init__(self, voice: Voice, ctx: EngineContext):
        self.voice = voice
        self.ctx = ctx

    @abstractmethod
    def start(self) -> None:
        """Bring the sound source up (start the unit / load the plugin). Does not
        block until ready — the manager polls is_ready()."""

    @abstractmethod
    def stop(self, timeout: float = 2.0) -> None:
        """Tear down. The manager panics + disconnects MIDI/audio first."""

    @abstractmethod
    def load(self, voice: Voice) -> bool:
        """Load a specific instrument into the running engine (SF2 / SFZ / patch).
        Also the in-place path when switching voices within the same engine."""

    def is_ready(self) -> bool:
        """Non-blocking: has the engine registered its JACK audio outputs yet?"""
        return bool(self.audio_out_ports)

    def panic(self) -> None:
        """All-notes-off before teardown. Best effort — tearing the source down
        (stop/remove) silences it anyway, so the base is a no-op."""

    @property
    def audio_client(self) -> str:
        """JACK client under which this engine's audio OUTPUT ports register.
        Same as jack_client for most engines; mod-host plugins differ (their
        audio lives under a per-instance client), so they override this."""
        return self.jack_client

    @property
    def midi_port(self) -> str | None:
        ports = self.ctx.jack.ports(
            client=self.jack_client, type="midi", is_output=False
        )
        return ports[0] if ports else None

    @property
    def audio_out_ports(self) -> list[str]:
        return self.ctx.jack.ports(
            client=self.audio_client, type="audio", is_output=True
        )


class ProcessEngine(Engine):
    """Engine backed by a systemd unit. RT comes from the unit, not Python."""

    unit: str = ""

    def start(self) -> None:
        _systemctl_unit(self.ctx, "start", self.unit)

    def stop(self, timeout: float = 2.0) -> None:
        # systemd manages SIGTERM->SIGKILL timeout itself.
        _systemctl_unit(self.ctx, "stop", self.unit)

    def load(self, voice: Voice) -> bool:
        return True  # single-voice engines have nothing to reload


# fluidsynth-engine.service launches fluidsynth with this soundfont already on
# its command line, so it's resident as sfont 1 for the whole process lifetime.
_DEFAULT_SOUNDFONT = os.path.join(SOUNDFONT_DIR, "default.sf2")


def _same_file(a: str, b: str) -> bool:
    """True if a and b are the same file (following symlinks, e.g. default.sf2)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


class FluidSynthEngine(ProcessEngine):
    key = "fluidsynth"
    jack_client = "fluidsynth"
    unit = "fluidsynth-engine.service"

    def load(self, voice: Voice) -> bool:
        if not voice.path:
            return True
        # The startup soundfont is already resident as sfont 1 for the life of
        # the process, so switching to a voice from it is just a preset select,
        # NOT a second (multi-hundred-MB) reload off the SD card. This is what
        # makes these voices switch quickly instead of re-reading a font that
        # fluidsynth already has loaded.
        if _same_file(voice.path, _DEFAULT_SOUNDFONT):
            self._select(voice, sfont_id=1)
            return True
        if not self.ctx.fluidsynth.load_soundfont(voice.path):
            return False
        # A freshly loaded font becomes the highest-numbered sfont; the client
        # reports it, and load_soundfont already selects program 0 of it. Only
        # re-select when this voice wants a specific instrument.
        if voice.program >= 0:
            self._select(voice, sfont_id=self.ctx.fluidsynth.current_sfont_id())
        return True

    def _select(self, voice: Voice, sfont_id: int) -> None:
        """Pick the instrument inside the soundfont. One .sf2 holds up to 128
        programs per bank, so this is what makes a GM font a *library* of voices
        (Rhodes, Wurlitzer, drawbar organ, synth brass) rather than one entry."""
        bank, program = voice.bank, max(0, voice.program)
        self.ctx.fluidsynth.select_preset(0, sfont_id, bank, program)

    def panic(self) -> None:
        self.ctx.fluidsynth.reset()


class PianoteqEngine(ProcessEngine):
    key = "pianoteq"
    jack_client = "Pianoteq"
    # NOTE: needs a pianoteq.service (RT via chrt/taskset -c 2), added when
    # Pianoteq is installed. Proprietary — not shipped in the image.
    unit = "pianoteq.service"


_MOD_HOST_START_TIMEOUT = 10.0


class ModHostEngine(Engine):
    """Any LV2 instrument hosted in mod-host.

    Which plugin comes from the voice, not from a table here: `engine:
    "modhost"` plus a `uri` is enough, so a new LV2 instrument is a voices.json
    edit. (`sfizz`/`dexed` still name their engine instead of a URI; see
    lv2.PLUGIN_SPECS.)

    **Instruments stay loaded.** Each voice gets its own mod-host instance via
    InstrumentSlots and keeps it, so switching between two already-loaded voices
    costs a bypass flip and a JACK re-patch rather than an instantiate. That's
    the whole reason for consolidating instruments into one LV2 host. Only large
    sample libraries (`resident: false`) share a scratch slot and reload.

    Because the ports of an inactive instrument now *persist*, the manager can
    no longer rely on teardown dropping their connections — see
    EngineManager._switch_to.
    """

    key = "modhost"
    jack_client = "mod-host"
    unit = "mod-host.service"

    def __init__(self, voice: Voice, ctx: EngineContext):
        super().__init__(voice, ctx)
        self._instance: int | None = None

    @property
    def instance(self) -> int | None:
        return self._instance

    @property
    def audio_client(self) -> str:
        # mod-host registers each plugin instance's ports under a per-instance
        # client "effect_<instance>" (confirmed on hardware via jack_lsp).
        # Unresolved slot -> a name that matches nothing, so port discovery
        # returns empty rather than silently matching another instrument.
        if self._instance is None:
            return "effect_unallocated"
        return f"effect_{self._instance}"

    @property
    def midi_port(self) -> str | None:
        # An LV2 instrument receives MIDI on its instance's own atom/control
        # port (effect_<n>:control), NOT the shared mod-host:midi_in — mod-host
        # does not forward its midi_in into the hosted plugin, so wiring the
        # keyboard there was a silent dead end. Confirmed on hardware.
        ports = self.ctx.jack.ports(
            client=self.audio_client, type="midi", is_output=False
        )
        return ports[0] if ports else None

    def start(self) -> None:
        _systemctl_unit(self.ctx, "start", self.unit)
        self._acquire_with_retry(self.voice)

    def stop(self, timeout: float = 2.0) -> None:
        """Bypass rather than unload: the plugin stays resident so switching
        back is instant. mod-host itself always keeps running — it hosts the
        master chain and the effects rack, not just this instrument."""
        if self._instance is not None:
            self.ctx.mod_host.bypass(self._instance, True)

    def load(self, voice: Voice) -> bool:
        """Switch this engine to `voice`, reusing its slot if already loaded."""
        instance = self.ctx.slots.acquire(voice)
        if instance is None:
            return False
        self._instance = instance
        self.ctx.slots.activate(instance)
        return True

    def _acquire_with_retry(
        self, voice: Voice, timeout: float = _MOD_HOST_START_TIMEOUT
    ) -> bool:
        """A freshly-started mod-host accepts TCP connections before it has
        finished its LV2 world scan, so the first add can bounce with an error
        or get no response at all. Retry the load itself, not just the socket
        connect, until mod-host is actually ready to host a plugin."""
        deadline = time.monotonic() + timeout
        while True:
            if self.load(voice):
                return True
            if time.monotonic() >= deadline:
                logger.error("mod-host did not become ready within %.1fs", timeout)
                return False
            time.sleep(0.2)


# engine string (from voices.json) -> Engine class. A new engine is one class +
# one entry; the manager and UI never change.
ENGINE_REGISTRY: dict[str, type[Engine]] = {
    "fluidsynth": FluidSynthEngine,
    "pianoteq": PianoteqEngine,
    # Any LV2 instrument: the voice carries the URI. Adding one is a manifest
    # edit, not a code change.
    GENERIC_ENGINE: ModHostEngine,
    # Legacy engine names for the two plugins that predate `modhost`.
    "sfizz": ModHostEngine,
    "dexed": ModHostEngine,
}
