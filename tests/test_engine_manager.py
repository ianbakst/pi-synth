"""Tests for EngineManager switching logic — fake engines, jack, and registry.

EngineManager() constructs real (but unused, lazily-connecting) clients; we then
swap in a FakeJack and a fake registry, so no JACK/mod-host/fluidsynth is needed.
"""

from unittest.mock import MagicMock

from synth_ui.clients.audio_devices import AudioDevices
from synth_ui.clients.engine_manager import EngineManager
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
    def __init__(self, ready=True):
        self.ready = ready
        self.connects: list = []

    def wait_for(self, *, client, type=None, is_output=None, timeout=0):
        return self.ready

    def keyboard_midi_sources(self, snapshot=None):
        return [KBD]

    def dac_sinks(self, snapshot=None):
        return ["system:playback_1", "system:playback_2"]

    def connect(self, src, dst):
        EVENTS.append(("connect", src, dst))
        self.connects.append((src, dst))
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


def make_mgr(jack=None):
    EVENTS.clear()
    m = EngineManager()
    m._jack = jack or FakeJack()
    m._registry = {"fluidsynth": FluidFake, "sfizz": ModFake, "dexed": ModFake}
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


# --- fluidsynth-only delegation ---------------------------------------------

def test_presets_and_gain_only_apply_to_fluidsynth():
    m = make_mgr()
    m._fluidsynth = MagicMock()
    m._fluidsynth.list_presets.return_value = ["p1", "p2"]

    m.load_voice(GM)
    assert m.list_presets() == ["p1", "p2"]
    m.set_gain(2.5)
    m._fluidsynth.set_gain.assert_called_once_with(2.5)

    m.load_voice(SFIZZ)                         # switch away from fluidsynth
    m._fluidsynth.reset_mock()
    assert m.list_presets() == []
    m.set_gain(1.0)
    m._fluidsynth.set_gain.assert_not_called()


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
