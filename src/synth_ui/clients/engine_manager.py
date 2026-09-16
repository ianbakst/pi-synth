"""
EngineManager: the single audio-control surface the UI talks to.

Owns exactly one active Engine (see engine.py) and composes JackGraph + the
engine registry to switch instruments with:
  - connect-before-disconnect (MIDI *and* audio) — no silent gap on a switch,
  - panic-before-teardown — no stuck notes,
  - same-source in-place reload (sfizz <-> dexed inside
    mod-host) with no process restart.

Crucially, the audio wiring (engine outputs -> system:playback) happens here —
that is what makes mod-host-hosted instruments (sfizz/dexed) audible, which the
old shell-script path never did. This replaces engine-manager.sh / midi-connect.sh
entirely; the UI's public API is unchanged (load_voice / set_gain /
is_connected).
"""

import logging
import math
import re
import time

from synth_ui.clients.audio_devices import AudioDevices, Card
from synth_ui.clients.constants import (
    LOCALHOST,
)
from synth_ui.clients.effects_rack import Effect, EffectsRack
from synth_ui.clients.engine import ENGINE_REGISTRY, Engine, EngineContext
from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.lv2 import ControlPort, control_ports
from synth_ui.clients.master_chain import MasterChain, MasterStage, sink_for
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.rig import Rig, plan
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.velocity_filter import VelocityFilter
from synth_ui.clients.voice import Voice
from synth_ui.config import AUDIO_DEVICE_FILE, FIXED_VELOCITY, MASTER_CHAIN, MAX_GAIN

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
    """Volume slider position (0..MAX_GAIN) -> dB on the master chain.

    The top of the slider is 0 dB, never above. The volume stage sits AFTER the
    limiter, so a positive value isn't "louder" — it's past full scale, clipping
    at the DAC where the limiter can't help. The old mapping put unity at 20% of
    the slider's travel, so the top four-fifths were all clipping (moving it
    changed nothing audible) and every useful setting was crammed into the
    bottom fifth.

    Square-law taper (40*log10 rather than 20*log10): hearing is roughly
    logarithmic, so a linear-in-amplitude slider bunches all the audible change
    at the bottom. This spreads it: 50% is -12 dB, 25% is -24 dB.
    """
    ratio = max(0.0, min(1.0, gain / MAX_GAIN))
    if ratio <= 0:
        return _SILENCE_DB
    return max(_SILENCE_DB, 40.0 * math.log10(ratio))


class EngineManager:
    """High-level voice switcher over all engines. One active engine at a time."""

    def __init__(
        self,
        mod_host_host: str = LOCALHOST,
        mod_host_port: int = _MOD_HOST_PORT,
        audio_device_file: str = AUDIO_DEVICE_FILE,
        start_timeout: float = _MOD_HOST_START_TIMEOUT,
    ):
        self._mod_host = ModHostClient(host=mod_host_host, port=mod_host_port)
        self._jack = JackGraph()
        # Instrument plugins stay loaded across switches; this owns which
        # instance holds what (see slots.py). Shared by every ModHostEngine, so
        # it has to outlive them — the manager owns it, not the engine.
        self._slots = InstrumentSlots(self._mod_host)
        self._ctx = EngineContext(
            jack=self._jack,
            mod_host=self._mod_host,
            slots=self._slots,
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
        # The head of the MIDI path, for rigs that play at a fixed velocity.
        # Unlike the master chain this is loaded lazily — on the first rig that
        # asks for it — so a board that never uses it never carries the plugin.
        self._velocity = VelocityFilter(self._jack, self._mod_host)
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
        self._clear_host()
        # _clear_host just removed the filter along with everything else, so the
        # bookkeeping has to forget it too — believing it is loaded when it
        # isn't points _midi_sources() at a dead port.
        self._velocity.teardown()
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

    def _clear_host(self) -> None:
        """Remove every plugin mod-host is holding, so this session starts clean.

        mod-host outlives the UI: `deploy.sh` and any UI crash restart synth-ui
        but leave mod-host running, still holding the last session's plugins
        *and their JACK connections*, none of which this session knows about.
        That surfaced two ways:

          - a stale instrument -> DAC connection from a session where the master
            chain failed kept feeding the DAC directly, bypassing the volume
            stage — so the slider "didn't work" on exactly that instrument;
          - the previous session's limiter still sitting at instance 90, so this
            session's `add` at 90 is refused and the master chain silently
            degrades to direct-to-DAC.

        Removing a plugin destroys its JACK client, which drops every
        connection it had. What's loaded is read from the JACK graph (mod-host
        registers each instance as client `effect_<n>`), so only instances that
        actually exist are touched.
        """
        instances = sorted({
            int(m.group(1))
            for name in self._jack.snapshot()
            if (m := re.match(r"effect_(\d+):", name))
        })
        for instance in instances:
            self._mod_host.remove_plugin(instance, missing_ok=True)
        if instances:
            logger.info("cleared %d stale mod-host instances", len(instances))

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

        # Same engine already active -> switch in place (no restart, no gap).
        if self._active is not None and self._active.key == engine_cls.key:
            # Capture the outgoing ports *first*: with instruments resident, a
            # mod-host voice change moves to a different instance whose ports
            # are different, and the old instance's ports don't disappear —
            # they'd keep taking MIDI and feeding the sink alongside the new
            # one. (For a process engine the ports are stable: a no-op.)
            prev_midi, prev_outs = self._active.midi_port, self._active.audio_out_ports
            ok = self._active.load(voice)
            self._active.voice = voice
            self._wire(self._active)                        # connect new first,
            self._unwire(prev_midi, prev_outs, keep=self._active)  # then drop old
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
        # After load_voice, which is what makes an instrument active for the
        # filter to be patched in front of.
        self.set_fixed_velocity(rig.fixed_velocity)
        # Rig trim sits on top of the voice's calibrated trim: the measured
        # value matches instruments to each other, the rig value is the by-ear
        # nudge for this particular sound.
        self._rig_trim_db = rig.trim_db
        self._master.set_trim_db(voice.gain_trim_db + rig.trim_db)
        return ok

    def set_rig_trim(self, db: float) -> None:
        """The by-ear level nudge for the active rig, on top of its voice's
        measured `gain_trim_db` (see tools/calibrate_levels). Applied live so
        it's audible while the slider moves; persistence is the UI's job."""
        self._rig_trim_db = db
        voice_trim = self._active.voice.gain_trim_db if self._active else 0.0
        self._master.set_trim_db(voice_trim + db)

    def fixed_velocity_available(self) -> bool:
        """Whether this board can flatten velocity — i.e. whether x42-plugins is
        installed. False is a reason for the UI to hide the control, not an
        error: everything else about the rig still works."""
        return self._velocity.available

    def set_fixed_velocity(self, enabled: bool) -> bool:
        """Play every note at config.FIXED_VELOCITY, or as struck.

        Enabling the first time loads the filter and moves the keyboards behind
        it; after that both directions are a parameter change. Returns whether
        the setting is actually in force, so a rig asking for something this
        board can't do doesn't report success."""
        if enabled and not self._insert_velocity_filter():
            return False
        self._velocity.set_fixed(FIXED_VELOCITY if enabled else 0)
        return not enabled or self._velocity.is_loaded

    def _insert_velocity_filter(self) -> bool:
        """Put the filter between the keyboards and the instrument.

        Only ever runs once per session — see velocity_filter.py on why the
        filter stays in the path once it's in. The disconnect matters: _wire()
        adds the filtered edge but cannot know the direct keyboard->instrument
        edges it replaces are still there, and while both exist every note
        sounds twice.
        """
        if self._velocity.is_loaded:
            return True
        if not self._velocity.ensure():
            return False

        keyboards = self._jack.keyboard_midi_sources()
        self._velocity.attach(keyboards)
        if self._active is not None:
            self._wire(self._active)
            midi_in = self._active.midi_port
            if midi_in:
                for src in keyboards:
                    self._jack.disconnect(src, midi_in)
        return True

    def set_gain(self, gain: float) -> None:
        """Master volume, in slider units. Applies to every voice — previously
        this only reached fluidsynth, so most voices had no volume control at
        all. Summed with the active voice's trim inside the master chain."""
        self._volume_db = _gain_to_db(gain)
        self._master.set_volume_db(self._volume_db)

    def is_connected(self) -> bool:
        return self._active is not None and self._active.is_ready()

    @property
    def jack(self) -> JackGraph:
        """The JACK graph, for tools that need to patch connections of their
        own (calibrate_levels wires its passage player in by hand)."""
        return self._jack

    def active_midi_port(self) -> str | None:
        """The JACK MIDI input of the instrument currently loaded.

        _wire() only ever connects *physical* MIDI sources, which is right for
        keyboards but means a software sender (the calibration passage player)
        is never wired up automatically. Tools that need to play into the
        instrument ask for the port and connect it themselves.
        """
        return self._active.midi_port if self._active is not None else None

    def master_output_ports(self) -> list[str]:
        """Where to record the finished signal: the tail of the master chain,
        falling back to the active instrument if no master chain is up."""
        ports = self._master.output_ports()
        if ports:
            return ports
        return self._active.audio_out_ports if self._active is not None else []

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
        # Survives the restart with the voice: the card you play through has
        # nothing to do with how the rig treats velocity, and coming back with
        # dynamics silently switched on mid-set would be its own bug.
        was_fixed = bool(self._velocity.velocity)
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

        # mod-host cycles with jack (PartOf), so its plugins — the master chain
        # and every resident instrument — are gone. Drop the stale bookkeeping
        # and rebuild before re-establishing the voice, or we'd "reuse" slots
        # that no longer hold anything.
        self._slots.clear()
        self._master.teardown()
        # The filter went with mod-host too. It has to be forgotten before it
        # can be rebuilt: otherwise _midi_sources() keeps naming a port that no
        # longer exists, and the instrument is wired to nothing — silence.
        self._velocity.teardown()
        self.start()
        self._master.set_volume_db(self._volume_db)

        if voice is not None:
            ok = self.load_voice(voice)
            # After load_voice, so there is an active instrument to insert the
            # filter in front of.
            self.set_fixed_velocity(was_fixed)
            return ok
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
            old_midi, old_outs = old.midi_port, old.audio_out_ports
            old.stop()
            self._unwire(old_midi, old_outs, keep=new)

        self._active = new
        return True

    def _unwire(
        self, midi_port: str | None, audio_outs: list[str], keep: Engine
    ) -> None:
        """Drop the previous instrument's connections.

        This used to be unnecessary: tearing an engine down removed its JACK
        ports and jackd dropped the edges for us. Instruments now stay loaded
        (see slots.py), so an inactive mod-host instrument keeps its ports —
        and without this it would go on receiving the keyboard and feeding the
        sink, i.e. two instruments sounding at once.

        Ports shared with the incoming engine are left alone, so a switch that
        lands on the same instance doesn't disconnect what was just wired."""
        keep_outs = set(keep.audio_out_ports)
        if midi_port and midi_port != keep.midi_port:
            for src in self._midi_sources():
                self._jack.disconnect(src, midi_port)
        sinks = self._sinks()
        for src in audio_outs:
            if src in keep_outs:
                continue
            for dst in sinks:
                self._jack.disconnect(src, dst)

    def _wire(self, engine: Engine) -> None:
        """Patch keyboard MIDI -> engine, and engine audio -> DAC (or, if the
        effects rack is non-empty, -> the rack's input instead; the rack's own
        output -> DAC leg is owned by EffectsRack._rechain, not here).
        Idempotent — also the hook that re-establishes this leg after a rack
        mutation changes which effect is first in the chain."""
        # Keyboards feed the velocity filter when it's in the path, and the
        # instrument directly when it isn't. Re-attached on every wire so a
        # keyboard unplugged and plugged back in — a new JACK port — is picked
        # up without a restart, which is what this loop has always been for.
        self._velocity.attach(self._jack.keyboard_midi_sources())
        midi_in = engine.midi_port
        if midi_in:
            for src in self._midi_sources():
                self._jack.connect(src, midi_in)
        else:
            logger.warning("no MIDI input port found for engine '%s'", engine.key)

        # instrument -> rack (if any) -> master (if up) -> DAC. Each stage owns
        # only its own outgoing leg; this one moves on every instrument switch.
        outs = engine.audio_out_ports
        sinks = self._sinks()
        if outs and sinks:
            for src, dst in zip(outs, sinks):
                self._jack.connect(src, dst)
        else:
            logger.warning("no audio-out/DAC ports to wire for engine '%s'", engine.key)

    def _sinks(self) -> list[str]:
        """Where the active instrument's audio goes: the head of the effects
        rack if there is one, otherwise the master chain, otherwise the DAC."""
        if self._effects.is_empty():
            return self._master.input_ports() or self._jack.dac_sinks()
        return self._effects.input_ports()

    def _midi_sources(self) -> list[str]:
        """What feeds the active instrument's MIDI input: the velocity filter's
        output if it's in the path, otherwise the keyboards themselves.

        The MIDI-side counterpart of _sinks() — one place that answers "what is
        upstream of the instrument", so neither _wire nor _unwire has to know
        whether a filter exists."""
        filtered = self._velocity.output_port()
        if filtered:
            return [filtered]
        return self._jack.keyboard_midi_sources()

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
        a process engine (Pianoteq) is ever active alongside mod-host —
        see docs/voice-library.md."""
        return True

    def effects(self) -> list[Effect]:
        return self._effects.effects()

    def move_effect(self, source: int, target: int) -> bool:
        moved = self._effects.move(source, target)
        if moved and self._active is not None:
            self._wire(self._active)
        return moved

    def add_effect(self, uri: str, index: int | None = None) -> int | None:
        instance = self._effects.add(uri, index)
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

    def set_effect_bypass(self, instance: int, bypassed: bool) -> bool:
        return self._effects.set_bypass(instance, bypassed)

    def effect_controls(self, uri: str) -> list[ControlPort]:
        """The knobs this effect exposes, read from the plugin itself."""
        return control_ports(uri)

    def set_effect_param(self, instance: int, symbol: str, value: str) -> bool:
        return self._effects.set_param(instance, symbol, value)
