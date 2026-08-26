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
import math
import time

from synth_ui.clients.audio_devices import AudioDevices, Card
from synth_ui.clients.constants import (
    DEFAULT_PORT,
    FIRST_TIMEOUT,
    LOAD_TIMEOUT,
    LOCALHOST,
    SILENCE_TIMEOUT,
)
from synth_ui.clients.effects_rack import Effect, EffectsRack
from synth_ui.clients.engine import ENGINE_REGISTRY, Engine, EngineContext
from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.master_chain import MasterChain, MasterStage, sink_for
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.rig import Rig, plan
from synth_ui.clients.synth_client import FluidSynthController, Preset
from synth_ui.clients.voice import Voice
from synth_ui.config import AUDIO_DEVICE_FILE, MASTER_CHAIN, UNITY_GAIN

logger = logging.getLogger(__name__)

_MOD_HOST_PORT = 5555
# How long to wait for a newly-started engine to register its JACK ports.
_READY_TIMEOUT = 6.0
# How long to wait for jack + mod-host to come back after a card-change restart.
_AUDIO_STACK_TIMEOUT = 10.0
_MOD_HOST_UNIT = "mod-host.service"
# Budget for mod-host to finish its LV2 world scan and accept plugin adds.
_MOD_HOST_START_TIMEOUT = 10.0

# Volume floor: the slider's bottom means silence, not a very small gain.
_SILENCE_DB = -60.0


def _gain_to_db(gain: float) -> float:
    """Slider position (linear, 0..MAX_GAIN) -> dB on the master chain, with
    UNITY_GAIN as 0 dB."""
    if gain <= 0:
        return _SILENCE_DB
    return max(_SILENCE_DB, 20.0 * math.log10(gain / UNITY_GAIN))


class EngineManager:
    """High-level voice switcher over all engines. One active engine at a time."""

    def __init__(
        self,
        fluidsynth_host: str = LOCALHOST,
        fluidsynth_port: int = DEFAULT_PORT,
        mod_host_host: str = LOCALHOST,
        mod_host_port: int = _MOD_HOST_PORT,
        audio_device_file: str = AUDIO_DEVICE_FILE,
        start_timeout: float = _MOD_HOST_START_TIMEOUT,
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
        # The master chain is the permanent tail: everything — every instrument
        # and the whole effects rack — feeds through it into the DAC. It carries
        # per-voice level trim and the output limiter (see master_chain.py).
        self._master = MasterChain(
            self._jack,
            self._mod_host,
            [MasterStage(**stage) for stage in MASTER_CHAIN],
        )
        # Effects share the instrument mod-host's own client/process (never a
        # second one — see effects_rack.py and docs/engine-architecture.md
        # "Effects rack"). Their tail feeds the master chain, not the DAC.
        self._effects = EffectsRack(
            self._jack, self._mod_host, sink=sink_for(self._master, self._jack)
        )
        # mod-host now hosts the master chain, so it must stay up for the whole
        # session — never stopped when switching away from a mod-host
        # instrument. Stopping it would take the master chain (and the rack)
        # down with the instrument.
        self._ctx.mod_host_needed = lambda: True
        self._volume_db = 0.0
        self._rig_trim_db = 0.0
        self._started = False
        self._start_timeout = start_timeout
        self._audio = AudioDevices()
        self._audio_device_file = audio_device_file
        self._registry = ENGINE_REGISTRY  # overridable in tests
        self._active: Engine | None = None

    # ------------------------------------------------------------------
    # Public API (UI contract — unchanged)
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Bring up the always-on part of the audio graph: mod-host and the
        master chain. Called once at UI startup, before any voice is loaded, so
        the master chain's output->DAC leg exists before anything feeds it.

        Idempotent: re-running after a jack restart re-establishes the chain.
        Also called lazily by load_voice, so the chain exists before anything is
        routed into it no matter which happens first."""
        self._started = True
        self._ctx.systemctl(["sudo", "systemctl", "start", _MOD_HOST_UNIT])
        # A freshly-started mod-host accepts TCP before it has finished scanning
        # the LV2 world, so the first plugin adds can bounce. Retry the load
        # itself, not just the connect — same race ModHostEngine handles.
        deadline = time.monotonic() + self._start_timeout
        while True:
            if self._master.ensure():
                return True
            if time.monotonic() >= deadline:
                logger.error("master chain did not come up; routing direct to DAC")
                return False
            time.sleep(0.2)

    def load_voice(self, voice: Voice) -> bool:
        engine_cls = self._registry.get(voice.engine)
        if engine_cls is None:
            logger.error("unknown engine: %s", voice.engine)
            return False

        # The output path has to exist before a voice is wired into it. Once
        # only — a missing master plugin must not re-run the 10s bring-up on
        # every voice change.
        if not self._started:
            self.start()

        # The voice's measured level offset, so switching instruments doesn't
        # jump in volume. Applied before the engine makes sound, not after.
        # Selecting a voice from the catalog leaves whatever rig was loaded, so
        # its by-ear nudge no longer applies; load_rig re-applies its own.
        self._rig_trim_db = 0.0
        self._master.set_trim_db(voice.gain_trim_db)

        # Same JACK source already active -> reload in place (no restart, no gap).
        if self._active is not None and self._active.key == engine_cls.key:
            ok = self._active.load(voice)
            self._active.voice = voice
            # Re-patch (idempotent): covers mod-host recreating a plugin's audio
            # ports on a sfizz<->dexed swap; a no-op for fluidsynth's stable ports.
            self._wire(self._active)
            return ok

        return self._switch_to(engine_cls, voice)

    def load_rig(self, rig: Rig, voice: Voice) -> bool:
        """Load a saved rig: its instrument, its effects chain, and its level, as
        one operation.

        The chain is applied as a *diff* (see rig.plan) rather than rebuilt, so
        effects shared between the outgoing and incoming rig are re-ordered
        instead of re-instantiated — the expensive part of a switch.

        Effects first, then the instrument: `load_voice` wires the instrument to
        whatever is at the head of the chain, so the chain has to be settled
        before that leg is patched, or the instrument would be connected to an
        effect that is about to move."""
        self._effects.apply(plan(self._effects.snapshot(), rig.effects))
        ok = self.load_voice(voice)
        # Rig trim sits on top of the voice's calibrated trim: the measured
        # value matches instruments to each other, the rig value is the by-ear
        # nudge for this particular sound.
        self._rig_trim_db = rig.trim_db
        self._master.set_trim_db(voice.gain_trim_db + rig.trim_db)
        return ok

    def list_presets(self) -> list[Preset]:
        if self._is_active("fluidsynth"):
            return self._fluidsynth.list_presets()
        return []

    def select_preset(self, channel: int, sfont_id: int, bank: int, prog: int) -> None:
        if self._is_active("fluidsynth"):
            self._fluidsynth.select_preset(channel, sfont_id, bank, prog)

    def set_gain(self, gain: float) -> None:
        """Master volume, in slider units. Applies to every voice — previously
        this only reached fluidsynth, so most voices had no volume control at
        all. Summed with the active voice's trim inside the master chain."""
        self._volume_db = _gain_to_db(gain)
        self._master.set_volume_db(self._volume_db)
        if not self._master.is_ready() and self._is_active("fluidsynth"):
            # No master chain loaded (plugin missing): fall back to the one
            # engine that has its own gain, so the slider still does something.
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

        # mod-host cycles with jack (PartOf), so its plugins — including the
        # master chain — are gone. Drop the stale bookkeeping and rebuild before
        # re-establishing the voice, or the voice would be wired to ports that
        # no longer exist.
        self._master.teardown()
        self.start()
        self._master.set_volume_db(self._volume_db)

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
        """Patch keyboard MIDI -> engine, and engine audio -> DAC (or, if the
        effects rack is non-empty, -> the rack's input instead; the rack's own
        output -> DAC leg is owned by EffectsRack._rechain, not here).
        Idempotent — also the hook that re-establishes this leg after a rack
        mutation changes which effect is first in the chain."""
        midi_in = engine.midi_port
        if midi_in:
            for src in self._jack.keyboard_midi_sources():
                self._jack.connect(src, midi_in)
        else:
            logger.warning("no MIDI input port found for engine '%s'", engine.key)

        # instrument -> rack (if any) -> master (if up) -> DAC. Each stage owns
        # only its own outgoing leg; this one moves on every instrument switch.
        outs = engine.audio_out_ports
        if self._effects.is_empty():
            sinks = self._master.input_ports() or self._jack.dac_sinks()
        else:
            sinks = self._effects.input_ports()
        if outs and sinks:
            for src, dst in zip(outs, sinks):
                self._jack.connect(src, dst)
        else:
            logger.warning("no audio-out/DAC ports to wire for engine '%s'", engine.key)

    def _is_active(self, key: str) -> bool:
        return self._active is not None and self._active.key == key

    # ------------------------------------------------------------------
    # Effects rack
    # ------------------------------------------------------------------

    def effects_available(self) -> bool:
        """Effects now work under every voice, not just sfizz/dexed.

        They used to require a mod-host instrument to be active, because
        mod-host was on-demand and only running in that case — which meant the
        Hammond and the GM piano could never have reverb. mod-host is always up
        now (it hosts the master chain), so the rack is always reachable; a
        process engine's audio simply feeds it over JACK like anything else.

        The reason mod-host was on-demand still stands as a *load* question, not
        a routing one: two RT clients on core 2 caused continuous xruns on the
        pi4. That measurement predates the CM5 and is the thing to re-check when
        a process engine (fluidsynth/setBfree) is active alongside mod-host —
        see docs/voice-library.md."""
        return True

    def effects(self) -> list[Effect]:
        return self._effects.effects()

    def add_effect(self, uri: str) -> int | None:
        instance = self._effects.add(uri)
        if instance is not None and self._active is not None:
            self._wire(self._active)
        return instance

    def remove_effect(self, instance: int) -> None:
        self._effects.remove(instance)
        if self._active is not None:
            self._wire(self._active)

    def clear_effects(self) -> None:
        self._effects.clear()
        if self._active is not None:
            self._wire(self._active)

    def set_effect_param(self, instance: int, symbol: str, value: str) -> bool:
        return self._effects.set_param(instance, symbol, value)
