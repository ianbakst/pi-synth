"""
Engine layer: one uniform interface over every audio engine.

Each engine is either:
  - a ProcessEngine  — backed by a systemd unit (fluidsynth, setBfree, pianoteq).
    start()/stop() are `systemctl start/stop`; RT priority + core pinning come
    from the unit file, never from Python.
  - a ModHostEngine  — an LV2 plugin in the always-running mod-host (sfizz, dexed).
    start()/stop() are add/remove over mod-host's socket; there is no process to
    manage.

Reality-driven divergence from the design doc: sfizz and dexed are NOT separate
engines. They share mod-host's single plugin slot and its stable JACK ports, so
one ModHostEngine handles both — switching between them is an in-place plugin
swap (`load`), not a JACK re-patch. The manager decides in-place-reload vs. full
switch by comparing `Engine.key` (fluidsynth|setbfree|pianoteq|modhost).

A live engine's JACK ports are *discovered* through JackGraph (by client + type +
direction) rather than hardcoded, so this adapts to the names engines actually
register. NOTE: the exact client name mod-host uses for a plugin's *audio* output
ports should be confirmed on hardware (`jack_lsp -t`); if it isn't "mod-host",
change ModHostEngine.jack_client — discovery does the rest.
"""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.synth_client import FluidSynthController
from synth_ui.clients.voice import Voice

logger = logging.getLogger(__name__)

# LV2 plugin URIs and the LV2 symbol for their instrument-file parameter.
# Verify URIs with `lv2ls` on the Pi after installation.
_MODHOST_PLUGINS: dict[str, tuple[str, str]] = {
    "sfizz": ("http://sfztools.github.io/sfizz", "sfz_file"),
    "dexed": ("https://asb2m10.github.io/dexed", "sysex_file"),
}

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


@dataclass
class EngineContext:
    """Dependencies shared by every engine (so construction is uniform)."""

    jack: JackGraph
    mod_host: ModHostClient
    fluidsynth: FluidSynthController
    systemctl: Systemctl = _default_systemctl


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
    def midi_port(self) -> str | None:
        ports = self.ctx.jack.ports(
            client=self.jack_client, type="midi", is_output=False
        )
        return ports[0] if ports else None

    @property
    def audio_out_ports(self) -> list[str]:
        return self.ctx.jack.ports(
            client=self.jack_client, type="audio", is_output=True
        )


class ProcessEngine(Engine):
    """Engine backed by a systemd unit. RT comes from the unit, not Python."""

    unit: str = ""

    def start(self) -> None:
        self._systemctl("start")

    def stop(self, timeout: float = 2.0) -> None:
        # systemd manages SIGTERM->SIGKILL timeout itself.
        self._systemctl("stop")

    def load(self, voice: Voice) -> bool:
        return True  # single-voice engines have nothing to reload

    def _systemctl(self, action: str) -> None:
        rc = self.ctx.systemctl(["sudo", "systemctl", action, self.unit])
        if rc != 0:
            logger.error("systemctl %s %s failed (rc=%d)", action, self.unit, rc)


class FluidSynthEngine(ProcessEngine):
    key = "fluidsynth"
    jack_client = "fluidsynth"
    unit = "fluidsynth-engine.service"

    def load(self, voice: Voice) -> bool:
        if not voice.path:
            return True
        return self.ctx.fluidsynth.load_soundfont(voice.path)

    def panic(self) -> None:
        self.ctx.fluidsynth.reset()


class SetBfreeEngine(ProcessEngine):
    key = "setbfree"
    jack_client = "setBfree"
    unit = "setbfree.service"


class PianoteqEngine(ProcessEngine):
    key = "pianoteq"
    jack_client = "Pianoteq"
    # NOTE: needs a pianoteq.service (RT via chrt/taskset -c 2), added when
    # Pianoteq is installed. Proprietary — not shipped in the image.
    unit = "pianoteq.service"


class ModHostEngine(Engine):
    """sfizz + dexed: LV2 plugins in the always-running mod-host. They share one
    plugin slot (instance 0) and mod-host's stable JACK ports, so switching
    between them is an in-place plugin swap, never a JACK re-patch."""

    key = "modhost"
    jack_client = "mod-host"
    _instance = 0

    def __init__(self, voice: Voice, ctx: EngineContext):
        super().__init__(voice, ctx)
        self._loaded_uri: str | None = None

    def start(self) -> None:
        # mod-host is always running; "starting" this engine loads its plugin.
        self._ensure_plugin(self.voice)

    def stop(self, timeout: float = 2.0) -> None:
        self.ctx.mod_host.remove_plugin(self._instance)
        self._loaded_uri = None

    def load(self, voice: Voice) -> bool:
        if not self._ensure_plugin(voice):
            return False
        _, symbol = _MODHOST_PLUGINS[voice.engine]
        if not voice.path:
            return True
        return self.ctx.mod_host.set_param(self._instance, symbol, f"'{voice.path}'")

    def _ensure_plugin(self, voice: Voice) -> bool:
        """Load the plugin for this voice, swapping the current one if different."""
        uri, _ = _MODHOST_PLUGINS[voice.engine]
        if self._loaded_uri == uri:
            return True
        self.ctx.mod_host.remove_plugin(self._instance)  # clear any current plugin
        if not self.ctx.mod_host.load_plugin(uri, self._instance):
            logger.error("mod-host failed to load %s (%s)", voice.engine, uri)
            self._loaded_uri = None
            return False
        self._loaded_uri = uri
        return True


# engine string (from voices.json) -> Engine class. A new engine is one class +
# one entry; the manager and UI never change.
ENGINE_REGISTRY: dict[str, type[Engine]] = {
    "fluidsynth": FluidSynthEngine,
    "setbfree": SetBfreeEngine,
    "pianoteq": PianoteqEngine,
    "sfizz": ModHostEngine,
    "dexed": ModHostEngine,
}
