"""
EngineManager: the single audio-control surface the UI talks to.

Owns exactly one active Engine (see engine.py) and composes JackGraph + the
engine registry to switch instruments with:
  - connect-before-disconnect (MIDI *and* audio) — no silent gap on a switch,
  - panic-before-teardown — no stuck notes,
  - same-source in-place reload (fluidsynth SF2 -> SF2, or sfizz <-> dexed inside
    mod-host) with no process restart.

Crucially, the audio wiring (engine outputs -> system:playback) happens here —
that is what makes mod-host-hosted instruments (sfizz/dexed) audible, which the
old shell-script path never did. This replaces engine-manager.sh / midi-connect.sh
entirely; the UI's public API is unchanged (load_voice / list_presets /
select_preset / set_gain / is_connected).
"""

import logging

from synth_ui.clients.audio_devices import AudioDevices, Card
from synth_ui.clients.constants import (
    DEFAULT_PORT,
    FIRST_TIMEOUT,
    LOAD_TIMEOUT,
    LOCALHOST,
    SILENCE_TIMEOUT,
)
from synth_ui.clients.engine import ENGINE_REGISTRY, Engine, EngineContext
from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.synth_client import FluidSynthController, Preset
from synth_ui.clients.voice import Voice
from synth_ui.config import AUDIO_DEVICE_FILE

logger = logging.getLogger(__name__)

_MOD_HOST_PORT = 5555
# How long to wait for a newly-started engine to register its JACK ports.
_READY_TIMEOUT = 6.0
# How long to wait for jack + mod-host to come back after a card-change restart.
_AUDIO_STACK_TIMEOUT = 10.0


class EngineManager:
    """High-level voice switcher over all engines. One active engine at a time."""

    def __init__(
        self,
        fluidsynth_host: str = LOCALHOST,
        fluidsynth_port: int = DEFAULT_PORT,
        mod_host_host: str = LOCALHOST,
        mod_host_port: int = _MOD_HOST_PORT,
        audio_device_file: str = AUDIO_DEVICE_FILE,
    ):
        self._fluidsynth = FluidSynthController(
            host=fluidsynth_host,
            port=fluidsynth_port,
            timeout=FIRST_TIMEOUT,
            silence_timeout=SILENCE_TIMEOUT,
            load_timeout=LOAD_TIMEOUT,
        )
        self._mod_host = ModHostClient(host=mod_host_host, port=mod_host_port)
        self._jack = JackGraph()
        self._ctx = EngineContext(
            jack=self._jack, mod_host=self._mod_host, fluidsynth=self._fluidsynth
        )
        self._audio = AudioDevices()
        self._audio_device_file = audio_device_file
        self._registry = ENGINE_REGISTRY  # overridable in tests
        self._active: Engine | None = None

    # ------------------------------------------------------------------
    # Public API (UI contract — unchanged)
    # ------------------------------------------------------------------

    def load_voice(self, voice: Voice) -> bool:
        engine_cls = self._registry.get(voice.engine)
        if engine_cls is None:
            logger.error("unknown engine: %s", voice.engine)
            return False

        # Same JACK source already active -> reload in place (no restart, no gap).
        if self._active is not None and self._active.key == engine_cls.key:
            ok = self._active.load(voice)
            self._active.voice = voice
            # Re-patch (idempotent): covers mod-host recreating a plugin's audio
            # ports on a sfizz<->dexed swap; a no-op for fluidsynth's stable ports.
            self._wire(self._active)
            return ok

        return self._switch_to(engine_cls, voice)

    def list_presets(self) -> list[Preset]:
        if self._is_active("fluidsynth"):
            return self._fluidsynth.list_presets()
        return []

    def select_preset(self, channel: int, sfont_id: int, bank: int, prog: int) -> None:
        if self._is_active("fluidsynth"):
            self._fluidsynth.select_preset(channel, sfont_id, bank, prog)

    def set_gain(self, gain: float) -> None:
        # Only FluidSynth exposes gain today; other engines: TODO (JACK volume or
        # a plugin parameter).
        if self._is_active("fluidsynth"):
            self._fluidsynth.set_gain(gain)

    def is_connected(self) -> bool:
        return self._active is not None and self._active.is_ready()

    # ------------------------------------------------------------------
    # Audio device (which ALSA card JACK opens)
    # ------------------------------------------------------------------

    def list_audio_cards(self) -> list[Card]:
        return self._audio.list_cards()

    def current_audio_device(self) -> str | None:
        """The card id currently in effect (saved choice resolved by precedence)."""
        return self._audio.resolve(self._read_audio_device())

    def set_audio_device(self, card_id: str) -> bool:
        """Persist the card, restart the JACK stack onto it, and re-establish the
        active voice. JACK binds its device at startup, so this restarts jackd
        (mod-host/a2jmidid cycle via PartOf); the previous voice is rebuilt on the
        new server."""
        voice = self._active.voice if self._active is not None else None
        if self._active is not None:
            self._active.panic()
            self._active.stop()
            self._active = None

        self._write_audio_device(card_id)
        if self._ctx.systemctl(["sudo", "systemctl", "restart", "jack.service"]) != 0:
            logger.error("jack restart failed while switching audio device")
            return False
        if not self._wait_audio_stack():
            return False

        if voice is not None:
            return self.load_voice(voice)
        return True

    def _wait_audio_stack(self, timeout: float = _AUDIO_STACK_TIMEOUT) -> bool:
        """Block until jack is back after a restart. mod-host is on-demand now
        (started by ModHostEngine.start() itself, which waits for it) — nothing
        to wait for here if it isn't the active engine."""
        if not self._jack.wait_for(
            client="system", type="audio", is_output=False, timeout=timeout
        ):
            logger.error("jack did not return after restart")
            return False
        return True

    def _read_audio_device(self) -> str | None:
        try:
            with open(self._audio_device_file) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _write_audio_device(self, card_id: str) -> None:
        try:
            with open(self._audio_device_file, "w") as f:
                f.write(card_id)
        except OSError as e:
            logger.error("could not persist audio device selection: %s", e)

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    def _switch_to(self, engine_cls: type[Engine], voice: Voice) -> bool:
        new = engine_cls(voice, self._ctx)
        new.start()
        if not self._jack.wait_for(
            client=new.audio_client,
            type="audio",
            is_output=True,
            timeout=_READY_TIMEOUT,
        ):
            logger.error("engine %s did not register JACK ports in time", voice.engine)
            new.stop()
            return False
        new.load(voice)

        # Connect the new engine fully BEFORE tearing the old one down, so there
        # is no silent gap (brief overlap is fine and inaudible-ish).
        self._wire(new)

        old = self._active
        if old is not None:
            old.panic()
            # Stopping old removes its JACK ports; jackd drops their connections
            # automatically, so no explicit disconnect is needed.
            old.stop()

        self._active = new
        return True

    def _wire(self, engine: Engine) -> None:
        """Patch keyboard MIDI -> engine, and engine audio -> DAC. Idempotent."""
        midi_in = engine.midi_port
        if midi_in:
            for src in self._jack.keyboard_midi_sources():
                self._jack.connect(src, midi_in)
        else:
            logger.warning("no MIDI input port found for engine '%s'", engine.key)

        outs = engine.audio_out_ports
        sinks = self._jack.dac_sinks()
        if outs and sinks:
            for src, dst in zip(outs, sinks):
                self._jack.connect(src, dst)
        else:
            logger.warning("no audio-out/DAC ports to wire for engine '%s'", engine.key)

    def _is_active(self, key: str) -> bool:
        return self._active is not None and self._active.key == key
