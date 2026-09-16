"""Tests for EngineManager switching logic — fake engines, jack, and registry.

EngineManager() constructs real (but unused, lazily-connecting) clients; we then
swap in a FakeJack and a fake registry, so no JACK/mod-host/fluidsynth is needed.
"""

from unittest.mock import MagicMock

from synth_ui.clients.audio_devices import AudioDevices
from synth_ui.clients.effects_rack import EffectsRack
from synth_ui.clients.engine_manager import EngineManager
from synth_ui.clients.master_chain import sink_for
from synth_ui.clients.rig import Rig, RigEffect
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.velocity_filter import VelocityFilter
from synth_ui.clients.voice import Voice

APLAY = (
    "card 0: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: HifiBerry DAC\n"
    "card 1: Headphones [bcm2835 Headphones], device 0: Headphones\n"
)

# Shared ordered event log, so tests can assert connect-before-disconnect.
EVENTS: list = []

GM = Voice("GM", "fluidsynth", "/sf/gm.sf2", "GM")
GM2 = Voice("GM2", "fluidsynth", "/sf/gm2.sf2", "GM")
SFIZZ = Voice("Piano", "sfizz", "/sfz/p.sfz", "Piano")
DEXED = Voice("EP", "dexed", "/dx/ep.syx", "EP")
KBD = "a2j:KBD (capture): MIDI 1"


class FakeJack:
    def __init__(self, ready=True, ports=None, stale=None):
        self.ready = ready
        self.connects: list = []
        self._ports = ports or {}
        self.stale: list = list(stale or [])

    def wait_for(self, *, client, type=None, is_output=None, timeout=0):
        return self.ready

    def snapshot(self):
        # Port names the manager can see; start() reads this to find stale
        # mod-host instances left over from a previous UI session.
        names = {p for ports in self._ports.values() for p in ports}
        return dict.fromkeys(self.stale + sorted(names))

    def keyboard_midi_sources(self, snapshot=None):
        return [KBD]

    def dac_sinks(self, snapshot=None):
        return ["system:playback_1", "system:playback_2"]

    def ports(
        self, *, client=None, type=None, is_output=None, contains=None, snapshot=None
    ):
        return list(self._ports.get((client, type, is_output), []))

    def connect(self, src, dst):
        EVENTS.append(("connect", src, dst))
        self.connects.append((src, dst))
        return True

    def disconnect(self, src, dst):
        EVENTS.append(("disconnect", src, dst))
        if (src, dst) in self.connects:
            self.connects.remove((src, dst))
        return True


class _Rec:
    """Recording fake engine (matches the Engine interface the manager uses)."""

    key = "x"
    jack_client = "x"
    midi = "x:midi"
    audio = ("x:l", "x:r")
    ready = True

    def __init__(self, voice, ctx):
        self.voice = voice
        self.ctx = ctx
        self.calls: list = []

    def start(self):
        EVENTS.append(("start", self.key))
        self.calls.append("start")

    def stop(self, timeout=2.0):
        EVENTS.append(("stop", self.key))
        self.calls.append("stop")

    def load(self, voice):
        self.calls.append(("load", voice.name))
        return True

    def panic(self):
        EVENTS.append(("panic", self.key))
        self.calls.append("panic")

    def is_ready(self):
        return self.ready

    @property
    def audio_client(self):
        return self.jack_client

    @property
    def midi_port(self):
        return self.midi

    @property
    def audio_out_ports(self):
        return list(self.audio)


class FluidFake(_Rec):
    key = "fluidsynth"
    jack_client = "fluidsynth"
    midi = "fluidsynth:midi"
    audio = ("fluidsynth:l", "fluidsynth:r")


class ModFake(_Rec):
    key = "modhost"
    jack_client = "mod-host"
    midi = "mod-host:midi_in"
    audio = ("mod-host:o1", "mod-host:o2")


class FakeMaster:
    """Stand-in for MasterChain. `ready=False` (the default) models a unit whose
    master plugins aren't installed, where everything routes straight to the DAC."""

    def __init__(self, ready=False, ports=("effect_90:in_l", "effect_90:in_r")):
        self.ready = ready
        self._ports = list(ports)
        self.volume_db: float | None = None
        self.trim_db: float | None = None
        self.torn_down = False

    def ensure(self):
        return self.ready

    def teardown(self):
        self.torn_down = True

    def is_ready(self):
        return self.ready

    def input_ports(self):
        return list(self._ports) if self.ready else []

    def set_volume_db(self, db):
        self.volume_db = db

    def set_trim_db(self, db):
        self.trim_db = db


VELOCITY_PORTS = {
    ("effect_100", "midi", False): ["effect_100:midiin"],
    ("effect_100", "midi", True): ["effect_100:midiout"],
}


def fake_world(installed=True):
    w = MagicMock()
    w.has.return_value = installed
    return w


def make_mgr(jack=None, master=None, velocity_installed=True):
    EVENTS.clear()
    # start_timeout=0: no master-chain bring-up retry loop in tests.
    m = EngineManager(start_timeout=0)
    m._jack = jack or FakeJack()
    m._mod_host = MagicMock()
    m._mod_host.load_plugin.return_value = True
    m._registry = {"fluidsynth": FluidFake, "sfizz": ModFake, "dexed": ModFake}
    # __init__ already built self._effects/_master/_slots against the pre-swap
    # real jack/mod_host; rebuild against the fakes so tests never touch real
    # JACK or a mod-host socket.
    m._slots = InstrumentSlots(m._mod_host)
    m._ctx.slots = m._slots
    m._master = master or FakeMaster()
    m._effects = EffectsRack(m._jack, m._mod_host, sink=sink_for(m._master, m._jack))
    # Injected rather than left real: LV2World is optimistic when it can't run
    # lv2ls, so on a dev machine the filter would report itself available and
    # the tests would depend on whether x42-plugins happens to be installed.
    m._velocity = VelocityFilter(
        m._jack, m._mod_host, lv2=fake_world(velocity_installed)
    )
    m._ctx.mod_host_needed = lambda: True
    return m


# --- first load / wiring ----------------------------------------------------

def test_first_load_starts_and_wires_midi_and_audio():
    jack = FakeJack()
    m = make_mgr(jack)
    assert m.load_voice(GM) is True
    eng = m._active
    assert isinstance(eng, FluidFake)
    assert "start" in eng.calls and ("load", "GM") in eng.calls
    assert (KBD, "fluidsynth:midi") in jack.connects            # MIDI in
    assert ("fluidsynth:l", "system:playback_1") in jack.connects  # audio -> DAC
    assert ("fluidsynth:r", "system:playback_2") in jack.connects


# --- full switch: connect-before-disconnect, panic-before-teardown ----------

def test_full_switch_wires_new_audio_then_panics_and_stops_old():
    jack = FakeJack()
    m = make_mgr(jack)
    m.load_voice(GM)
    old = m._active
    assert m.load_voice(SFIZZ) is True
    new = m._active
    assert isinstance(new, ModFake) and new is not old
    # old torn down in order, only after new was wired
    assert old.calls[-2:] == ["panic", "stop"]
    # the fix: mod-host audio actually reaches the DAC
    assert ("mod-host:o1", "system:playback_1") in jack.connects
    # connect-before-disconnect: new audio wired BEFORE old is stopped
    new_wire = EVENTS.index(("connect", "mod-host:o1", "system:playback_1"))
    old_stop = EVENTS.index(("stop", "fluidsynth"))
    assert new_wire < old_stop


# --- in-place reload (same JACK source) -------------------------------------

def test_same_engine_reloads_in_place_without_restart():
    m = make_mgr()
    m.load_voice(GM)
    eng = m._active
    assert m.load_voice(GM2) is True
    assert m._active is eng                    # same instance, no switch
    assert ("load", "GM2") in eng.calls
    assert eng.calls.count("start") == 1       # not restarted


def test_sfizz_to_dexed_is_in_place_swap():
    m = make_mgr()
    m.load_voice(SFIZZ)
    eng = m._active
    assert m.load_voice(DEXED) is True
    assert m._active is eng                    # shared modhost source
    assert ("load", "EP") in eng.calls


# --- failure / edge cases ---------------------------------------------------

def test_switch_fails_when_engine_never_ready():
    m = make_mgr(FakeJack(ready=False))
    assert m.load_voice(GM) is False
    assert m._active is None                   # not left half-switched
    assert ("start", "fluidsynth") in EVENTS and ("stop", "fluidsynth") in EVENTS


def test_unknown_engine_returns_false():
    m = make_mgr()
    assert m.load_voice(Voice("X", "nope", "", "")) is False
    assert m._active is None




def test_is_connected_reflects_active_readiness():
    m = make_mgr()
    assert m.is_connected() is False           # nothing active
    m.load_voice(GM)
    assert m.is_connected() is True


# --- audio device selection -------------------------------------------------

def test_list_and_current_audio_device(tmp_path):
    m = make_mgr()
    m._audio = AudioDevices(reader=lambda: APLAY)
    m._audio_device_file = str(tmp_path / "dev")
    assert [c.id for c in m.list_audio_cards()] == ["sndrpihifiberry", "Headphones"]
    assert m.current_audio_device() == "sndrpihifiberry"   # no save -> default
    (tmp_path / "dev").write_text("Headphones")
    assert m.current_audio_device() == "Headphones"        # honours saved choice


def test_set_audio_device_persists_restarts_and_reloads(tmp_path):
    m = make_mgr()
    m._audio_device_file = str(tmp_path / "dev")
    calls: list = []
    m._ctx.systemctl = lambda argv: calls.append(argv) or 0

    m.load_voice(GM)
    assert m.set_audio_device("Headphones") is True
    assert (tmp_path / "dev").read_text() == "Headphones"
    assert ["sudo", "systemctl", "restart", "jack.service"] in calls
    # the active voice was rebuilt on the new server
    assert m._active is not None and m._active.voice.name == "GM"


def test_set_audio_device_fails_when_jack_restart_fails(tmp_path):
    m = make_mgr()
    m._audio_device_file = str(tmp_path / "dev")
    m._ctx.systemctl = lambda argv: 1          # restart fails
    assert m.set_audio_device("Headphones") is False


# --- effects rack ------------------------------------------------------------

EFFECT_10_PORTS = {
    ("effect_10", "audio", False): ["effect_10:in_left", "effect_10:in_right"],
    ("effect_10", "audio", True): ["effect_10:out_left", "effect_10:out_right"],
}
EFFECT_10_AND_11_PORTS = {
    **EFFECT_10_PORTS,
    ("effect_11", "audio", False): ["effect_11:in_left", "effect_11:in_right"],
    ("effect_11", "audio", True): ["effect_11:out_left", "effect_11:out_right"],
}


def test_effects_are_available_under_every_voice():
    # Reversal of prior behaviour: effects used to require a mod-host instrument
    # to be active, so the Hammond and the GM piano could never have reverb.
    # mod-host is always up now (it hosts the master chain), so the rack is
    # always reachable — a process engine just feeds it over JACK.
    m = make_mgr()
    assert m.effects_available() is True
    m.load_voice(GM)
    assert m.effects_available() is True
    m.load_voice(SFIZZ)
    assert m.effects_available() is True


def test_wire_routes_engine_audio_to_rack_when_nonempty():
    jack = FakeJack(ports=EFFECT_10_PORTS)
    m = make_mgr(jack)
    assert m.add_effect("urn:reverb") == 10
    assert m.load_voice(SFIZZ) is True
    assert ("mod-host:o1", "effect_10:in_left") in jack.connects
    assert ("mod-host:o2", "effect_10:in_right") in jack.connects
    assert ("mod-host:o1", "system:playback_1") not in jack.connects


def test_add_effect_rewires_instrument_into_new_first_effect():
    jack = FakeJack(ports=EFFECT_10_PORTS)
    m = make_mgr(jack)
    assert m.load_voice(SFIZZ) is True
    # straight to DAC while the rack is empty
    assert ("mod-host:o1", "system:playback_1") in jack.connects
    assert m.add_effect("urn:reverb") == 10
    # re-wired after the mutation
    assert ("mod-host:o1", "effect_10:in_left") in jack.connects


def test_remove_first_effect_rewires_instrument_to_new_first():
    jack = FakeJack(ports=EFFECT_10_AND_11_PORTS)
    m = make_mgr(jack)
    assert m.load_voice(SFIZZ) is True
    assert m.add_effect("urn:a") == 10
    assert m.add_effect("urn:b") == 11
    assert ("mod-host:o1", "effect_10:in_left") in jack.connects
    m.remove_effect(10)
    assert ("mod-host:o1", "effect_11:in_left") in jack.connects


def test_mod_host_is_never_stopped_by_an_instrument_switch():
    # mod-host hosts the master chain, which every voice feeds through, so
    # ModHostEngine.stop() must leave the process running whatever the rack
    # holds. Stopping it would take the whole output path down with the
    # instrument.
    jack = FakeJack(ports=EFFECT_10_PORTS)
    m = make_mgr(jack)
    assert m._ctx.mod_host_needed() is True
    assert m.add_effect("urn:reverb") == 10
    assert m._ctx.mod_host_needed() is True
    m.remove_effect(10)
    assert m._ctx.mod_host_needed() is True


# --- master chain routing + level ------------------------------------------

def test_instrument_routes_through_the_master_chain_when_it_is_up():
    jack = FakeJack()
    m = make_mgr(jack, master=FakeMaster(ready=True))
    assert m.load_voice(GM) is True
    assert ("fluidsynth:l", "effect_90:in_l") in jack.connects
    assert ("fluidsynth:r", "effect_90:in_r") in jack.connects
    # the master owns the leg to the DAC; the instrument must not bypass it
    assert ("fluidsynth:l", "system:playback_1") not in jack.connects


def test_instrument_falls_back_to_the_dac_when_no_master_chain_loaded():
    # A missing master plugin costs level control, never sound.
    jack = FakeJack()
    m = make_mgr(jack, master=FakeMaster(ready=False))
    assert m.load_voice(GM) is True
    assert ("fluidsynth:l", "system:playback_1") in jack.connects


def test_effects_rack_tail_feeds_the_master_not_the_dac():
    jack = FakeJack(ports=EFFECT_10_PORTS)
    m = make_mgr(jack, master=FakeMaster(ready=True))
    assert m.add_effect("urn:reverb") == 10
    assert ("effect_10:out_left", "effect_90:in_l") in jack.connects
    assert ("effect_10:out_left", "system:playback_1") not in jack.connects


def test_loading_a_voice_applies_its_measured_trim():
    # The whole point of gain_trim_db: switching instruments shouldn't jump in
    # level. Applied before the engine makes sound, not after.
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    quiet = Voice("Quiet", "fluidsynth", "/sf/q.sf2", "GM", gain_trim_db=-4.5)
    assert m.load_voice(quiet) is True
    assert master.trim_db == -4.5


def test_volume_reaches_every_voice_not_just_fluidsynth():
    # set_gain used to only reach fluidsynth, so most voices had no volume
    # control at all.
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    m.load_voice(SFIZZ)
    m.set_gain(5.0)
    assert master.volume_db == 0.0     # top of the slider is full scale
    m.set_gain(2.5)
    assert abs(master.volume_db - -12.04) < 0.05
    m.set_gain(0.0)
    assert master.volume_db == -60.0   # silence floor, not a tiny gain




def test_changing_audio_card_rebuilds_the_master_chain(tmp_path):
    # mod-host cycles with jack, so its plugins are gone: stale wiring must be
    # dropped and the chain rebuilt before the voice is re-established.
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    m._audio_device_file = str(tmp_path / "dev")
    m._ctx.systemctl = lambda argv: 0
    m.load_voice(GM)
    assert m.set_audio_device("Headphones") is True
    assert master.torn_down is True


# --- rigs -------------------------------------------------------------------

def test_load_rig_applies_chain_then_instrument_then_level():
    jack = FakeJack(ports=EFFECT_10_PORTS)
    master = FakeMaster(ready=True)
    m = make_mgr(jack, master=master)
    voice = Voice("Piano", "sfizz", "/sfz/p.sfz", "Piano", gain_trim_db=-2.0)
    rig = Rig(name="Wet Piano", voice="Piano", effects=[RigEffect("urn:reverb")],
              trim_db=-1.0)

    assert m.load_rig(rig, voice) is True
    # effects settled before the instrument was wired into the chain head
    assert ("mod-host:o1", "effect_10:in_left") in jack.connects
    # rig trim stacks on the voice's calibrated trim
    assert master.trim_db == -3.0


def test_switching_rigs_reuses_a_shared_effect():
    jack = FakeJack(ports=EFFECT_10_AND_11_PORTS)
    m = make_mgr(jack, master=FakeMaster(ready=True))
    voice = Voice("Piano", "sfizz", "/sfz/p.sfz", "Piano")
    m.load_rig(Rig(name="A", voice="Piano", effects=[RigEffect("urn:reverb")]), voice)
    m._mod_host.reset_mock()

    m.load_rig(
        Rig(name="B", voice="Piano",
            effects=[RigEffect("urn:reverb"), RigEffect("urn:delay")]),
        voice,
    )
    # the shared reverb was never re-instantiated
    m._mod_host.load_plugin.assert_called_once_with("urn:delay", 11)


def test_selecting_a_catalog_voice_drops_the_rig_trim():
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    voice = Voice("Piano", "sfizz", "/sfz/p.sfz", "Piano", gain_trim_db=-2.0)
    m.load_rig(Rig(name="A", voice="Piano", trim_db=-5.0), voice)
    assert master.trim_db == -7.0
    m.load_voice(voice)      # straight from the catalog, no rig
    assert master.trim_db == -2.0


# --- residency: inactive instruments keep their ports -----------------------

class SlotFake(_Rec):
    """A mod-host engine whose ports move per voice, as resident slots do."""

    key = "modhost"
    jack_client = "mod-host"

    def load(self, voice):
        self.calls.append(("load", voice.name))
        self.midi = f"effect_{voice.name}:control"
        self.audio = (f"effect_{voice.name}:o1", f"effect_{voice.name}:o2")
        return True


def test_switching_within_mod_host_disconnects_the_previous_instrument():
    # Instruments stay loaded now, so jackd no longer drops the old edges when
    # a voice is torn down. Without an explicit disconnect the previous voice
    # would keep taking MIDI and keep feeding the sink — two at once.
    jack = FakeJack()
    m = make_mgr(jack)
    m._registry = {"sfizz": SlotFake, "dexed": SlotFake}
    a = Voice("A", "sfizz", "/sfz/a.sfz", "Piano")
    b = Voice("B", "sfizz", "/sfz/b.sfz", "Piano")

    m.load_voice(a)
    m.load_voice(b)

    assert ("effect_B:o1", "system:playback_1") in jack.connects
    assert ("effect_A:o1", "system:playback_1") not in jack.connects
    assert (KBD, "effect_A:control") not in jack.connects
    assert (KBD, "effect_B:control") in jack.connects


def test_new_ports_are_connected_before_the_old_are_dropped():
    # No silent gap on a switch.
    jack = FakeJack()
    m = make_mgr(jack)
    m._registry = {"sfizz": SlotFake}
    m.load_voice(Voice("A", "sfizz", "/sfz/a.sfz", "Piano"))
    EVENTS.clear()
    m.load_voice(Voice("B", "sfizz", "/sfz/b.sfz", "Piano"))

    connect_new = EVENTS.index(("connect", "effect_B:o1", "system:playback_1"))
    drop_old = EVENTS.index(("disconnect", "effect_A:o1", "system:playback_1"))
    assert connect_new < drop_old


def test_a_switch_landing_on_the_same_slot_keeps_its_wiring():
    # Re-selecting the active voice must not disconnect what it just wired.
    jack = FakeJack()
    m = make_mgr(jack)
    m._registry = {"sfizz": SlotFake}
    voice = Voice("A", "sfizz", "/sfz/a.sfz", "Piano")
    m.load_voice(voice)
    EVENTS.clear()
    m.load_voice(voice)
    assert not [e for e in EVENTS if e[0] == "disconnect"]
    assert ("effect_A:o1", "system:playback_1") in jack.connects


def test_changing_audio_card_drops_stale_slot_bookkeeping(tmp_path):
    # mod-host cycles with jack, so every resident plugin is gone; reusing the
    # remembered slots would wire a voice to nothing.
    m = make_mgr(master=FakeMaster(ready=True))
    m._audio_device_file = str(tmp_path / "dev")
    m._ctx.systemctl = lambda argv: 0
    m._slots.acquire(
        Voice("A", "modhost", "", "", uri="urn:p", resident=True)
    )
    assert m._slots.loaded_instances() == [0]
    m.set_audio_device("Headphones")
    assert m._slots.loaded_instances() == []


def test_rig_trim_stacks_on_the_active_voices_measured_trim():
    # The by-ear nudge sits on top of the calibrated per-voice offset, and has
    # to be audible while the slider moves.
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    voice = Voice("Piano", "sfizz", "/sfz/p.sfz", "Piano", gain_trim_db=-2.0)
    m.load_voice(voice)
    m.set_rig_trim(-3.5)
    assert master.trim_db == -5.5


def test_rig_trim_before_any_voice_is_loaded_does_not_crash():
    master = FakeMaster(ready=True)
    m = make_mgr(master=master)
    m.set_rig_trim(-3.0)
    assert master.trim_db == -3.0


# --- volume never exceeds full scale -----------------------------------------

def test_the_volume_slider_never_goes_above_full_scale():
    # Volume sits AFTER the limiter, so a positive dB value clips at the DAC
    # where the limiter can't help. The old mapping put 0 dB at 20% of the
    # slider, making the top four-fifths all clipping.
    from synth_ui.clients.engine_manager import _gain_to_db
    from synth_ui.config import MAX_GAIN

    for pct in range(0, 101, 5):
        assert _gain_to_db(MAX_GAIN * pct / 100) <= 0.0


def test_the_slider_spreads_audible_change_across_its_travel():
    # A usable fader has meaningful attenuation across its range, not crammed
    # into the bottom fifth.
    from synth_ui.clients.engine_manager import _gain_to_db
    from synth_ui.config import MAX_GAIN

    assert -15 < _gain_to_db(MAX_GAIN * 0.5) < -9     # halfway is clearly quieter
    assert _gain_to_db(MAX_GAIN * 0.9) > -3            # near the top is near full


def test_the_boot_volume_leaves_headroom():
    from synth_ui.clients.engine_manager import _gain_to_db
    from synth_ui.config import DEFAULT_GAIN

    assert -12 < _gain_to_db(DEFAULT_GAIN) < 0


# --- a UI session starts from a clean mod-host --------------------------------

def test_start_clears_plugins_left_by_a_previous_ui_session():
    # mod-host outlives a UI restart, keeping the old session's plugins and
    # their JACK connections. A stale instrument -> DAC edge bypassed the
    # volume stage; a stale limiter at 90 made this session's add refused.
    jack = FakeJack(stale=["effect_0:outL", "effect_0:control", "effect_90:out_l"])
    m = make_mgr(jack, master=FakeMaster(ready=True))
    m.start()
    removed = sorted({c.args[0] for c in m._mod_host.remove_plugin.call_args_list})
    assert removed == [0, 90]


def test_start_on_a_fresh_mod_host_removes_nothing():
    m = make_mgr(FakeJack(), master=FakeMaster(ready=True))
    m.start()
    m._mod_host.remove_plugin.assert_not_called()


# --- fixed velocity (MIDI path) ---------------------------------------------

def test_fixed_velocity_is_unavailable_without_the_plugin():
    m = make_mgr(velocity_installed=False)
    assert m.fixed_velocity_available() is False
    assert m.set_fixed_velocity(True) is False


def test_the_filter_is_not_loaded_until_a_rig_asks_for_it():
    # A board that never uses this must not carry an extra RT plugin: the whole
    # reason the filter is lazy and the master chain isn't.
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_voice(GM)
    assert m._velocity.is_loaded is False
    assert (KBD, "fluidsynth:midi") in jack.connects


def test_enabling_puts_the_filter_between_keyboard_and_instrument():
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_voice(GM)
    assert m.set_fixed_velocity(True) is True
    assert (KBD, "effect_100:midiin") in jack.connects
    assert ("effect_100:midiout", "fluidsynth:midi") in jack.connects


def test_enabling_drops_the_direct_keyboard_edge_it_replaces():
    # The bug this exists to prevent: with both the raw and the filtered edge
    # live, every note sounds twice — once as struck, once flattened.
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_voice(GM)
    m.set_fixed_velocity(True)
    assert ("disconnect", KBD, "fluidsynth:midi") in EVENTS
    assert (KBD, "fluidsynth:midi") not in jack.connects


def test_disabling_keeps_the_filter_in_the_path():
    # Off is identity parameters, not removal — see velocity_filter.py. Taking
    # it out would re-open the double-note window on every toggle.
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_voice(GM)
    m.set_fixed_velocity(True)
    assert m.set_fixed_velocity(False) is True
    assert m._velocity.is_loaded is True
    assert m._velocity.velocity == 0
    assert ("effect_100:midiout", "fluidsynth:midi") in jack.connects


def test_switching_voices_moves_the_filter_output_not_the_keyboard():
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_voice(GM)
    m.set_fixed_velocity(True)
    m.load_voice(SFIZZ)
    assert ("effect_100:midiout", "mod-host:midi_in") in jack.connects
    assert ("disconnect", "effect_100:midiout", "fluidsynth:midi") in EVENTS
    # The keyboard leg belongs to the filter now and must not be torn down with
    # the outgoing instrument.
    assert (KBD, "effect_100:midiin") in jack.connects


def test_loading_a_rig_applies_its_velocity_setting():
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    rig = Rig(name="Pad", voice="GM", fixed_velocity=True)
    assert m.load_rig(rig, GM) is True
    assert m._velocity.velocity > 0
    assert ("effect_100:midiout", "fluidsynth:midi") in jack.connects


def test_loading_a_rig_without_the_setting_turns_it_back_off():
    # Rigs are switched with a footswitch mid-song; a setting that leaked from
    # the previous rig would flatten a piano part without being asked.
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack)
    m.load_rig(Rig(name="Pad", voice="GM", fixed_velocity=True), GM)
    m.load_rig(Rig(name="Piano", voice="GM2"), GM2)
    assert m._velocity.velocity == 0


def test_a_rig_asking_for_velocity_this_board_cannot_do_still_loads():
    jack = FakeJack(ports=VELOCITY_PORTS)
    m = make_mgr(jack, velocity_installed=False)
    rig = Rig(name="Pad", voice="GM", fixed_velocity=True)
    assert m.load_rig(rig, GM) is True
    assert (KBD, "fluidsynth:midi") in jack.connects
