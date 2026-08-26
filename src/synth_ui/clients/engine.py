"""
Engine layer: one uniform interface over every audio engine.

Each engine is either:
  - a ProcessEngine  — backed by a systemd unit (fluidsynth, setBfree, pianoteq).
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
switch by comparing `Engine.key` (fluidsynth|setbfree|pianoteq|modhost).

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
from synth_ui.clients.lv2 import GENERIC_ENGINE, PluginSpec, spec_for
from synth_ui.clients.mod_host_client import ModHostClient
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
        # the process, so switching to the default voice is just a preset select,
        # NOT a second (multi-hundred-MB) reload off the SD card. This is what
        # makes "General MIDI" switch quickly instead of re-reading the whole
        # font that fluidsynth already loaded at start.
        if _same_file(voice.path, _DEFAULT_SOUNDFONT):
            self.ctx.fluidsynth.select_preset(0, 1, 0, 0)
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


_MOD_HOST_START_TIMEOUT = 10.0


def _spec(voice: Voice) -> PluginSpec | None:
    return spec_for(voice.engine, voice.uri, voice.file_property)


class ModHostEngine(Engine):
    """Any LV2 instrument hosted in mod-host. All such voices share one plugin
    slot (instance 0) and mod-host's stable JACK ports, so switching between
    them is an in-place plugin swap, never a JACK re-patch.

    Which plugin comes from the voice, not from a table here: `engine:
    "modhost"` plus a `uri` is enough, so a new LV2 instrument is a voices.json
    edit. (`sfizz`/`dexed` still name their engine instead of a URI; see
    lv2.PLUGIN_SPECS.)

    mod-host itself is on-demand here (started/stopped like any ProcessEngine's
    unit), not always-running: hardware validation showed mod-host sitting on
    core 2 alongside another active instrument engine (e.g. fluidsynth) causes
    continuous JACK XRuns even fully idle, since core 2 only has RT budget for
    one resident engine at a time."""

    key = "modhost"
    jack_client = "mod-host"      # MIDI arrives at the shared mod-host:midi_in
    unit = "mod-host.service"
    _instance = 0

    def __init__(self, voice: Voice, ctx: EngineContext):
        super().__init__(voice, ctx)
        self._loaded_uri: str | None = None

    @property
    def audio_client(self) -> str:
        # mod-host registers each plugin instance's ports under a per-instance
        # client "effect_<instance>" (confirmed on hardware via jack_lsp).
        return f"effect_{self._instance}"

    @property
    def midi_port(self) -> str | None:
        # sfizz/dexed receive MIDI on the plugin instance's own atom/control port
        # (effect_<instance>:control), NOT the shared mod-host:midi_in — mod-host
        # does not forward its midi_in into the hosted plugin here, so wiring the
        # keyboard to mod-host:midi_in was a silent dead end. Confirmed on
        # hardware: connecting to effect_0:control is what makes notes sound.
        ports = self.ctx.jack.ports(
            client=self.audio_client, type="midi", is_output=False
        )
        return ports[0] if ports else None

    def start(self) -> None:
        _systemctl_unit(self.ctx, "start", self.unit)
        self._load_plugin_with_retry(self.voice)

    def stop(self, timeout: float = 2.0) -> None:
        self.ctx.mod_host.remove_plugin(self._instance)
        self._loaded_uri = None
        # Leave mod-host running if the effects rack still needs it (e.g.
        # switching sfizz -> fluidsynth with effects loaded) -- otherwise
        # stopping the process would kill every loaded effect too, not just
        # this instrument's own instance-0 slot.
        if not self.ctx.mod_host_needed():
            _systemctl_unit(self.ctx, "stop", self.unit)

    def _load_plugin_with_retry(
        self, voice: Voice, timeout: float = _MOD_HOST_START_TIMEOUT
    ) -> bool:
        """A freshly-started mod-host accepts TCP connections before it's done
        initializing (LV2 plugin world scan), so the first add can bounce with an
        error or get no response at all. Retry the load itself, not just the
        socket connect, until mod-host is actually ready to host a plugin."""
        spec = _spec(voice)
        if spec is None:
            logger.error("voice '%s' names no LV2 plugin URI", voice.name)
            return False
        deadline = time.monotonic() + timeout
        while True:
            if self._ensure_plugin(spec):
                return True
            if time.monotonic() >= deadline:
                logger.error("mod-host did not become ready within %.1fs", timeout)
                return False
            time.sleep(0.2)

    def load(self, voice: Voice) -> bool:
        spec = _spec(voice)
        if spec is None:
            logger.error("voice '%s' names no LV2 plugin URI", voice.name)
            return False
        if not self._ensure_plugin(spec):
            return False

        # Preset first, then explicit params — so a voice can start from a stock
        # LV2 preset and override a few controls (e.g. one b_synth registration
        # per organ voice). This is what lets one plugin back many voices.
        if voice.preset and not self.ctx.mod_host.preset_load(
            self._instance, voice.preset
        ):
            logger.error("mod-host failed to load preset %s", voice.preset)
        for symbol, value in voice.params.items():
            self.ctx.mod_host.set_param(self._instance, symbol, str(value))

        if not voice.path or not spec.file_property:
            return True
        # The instrument file is an LV2 patch property (atom), set via patch_set.
        # param_set can't reach it — that was why sfizz loaded but stayed on its
        # default instrument (silent). No quotes: mod-host takes the rest of the
        # line as the value.
        return self.ctx.mod_host.patch_set(
            self._instance, spec.file_property, voice.path
        )

    def _ensure_plugin(self, spec: PluginSpec) -> bool:
        """Load the plugin for this voice, swapping the current one if different."""
        if self._loaded_uri == spec.uri:
            return True
        self.ctx.mod_host.remove_plugin(self._instance)  # clear any current plugin
        if not self.ctx.mod_host.load_plugin(spec.uri, self._instance):
            logger.error("mod-host failed to load plugin %s", spec.uri)
            self._loaded_uri = None
            return False
        self._loaded_uri = spec.uri
        return True


# engine string (from voices.json) -> Engine class. A new engine is one class +
# one entry; the manager and UI never change.
ENGINE_REGISTRY: dict[str, type[Engine]] = {
    "fluidsynth": FluidSynthEngine,
    "setbfree": SetBfreeEngine,
    "pianoteq": PianoteqEngine,
    # Any LV2 instrument: the voice carries the URI. Adding one is a manifest
    # edit, not a code change.
    GENERIC_ENGINE: ModHostEngine,
    # Legacy engine names for the two plugins that predate `modhost`.
    "sfizz": ModHostEngine,
    "dexed": ModHostEngine,
}
