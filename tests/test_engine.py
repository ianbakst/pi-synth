"""Tests for the Engine layer — all dependencies mocked, no hardware."""

from unittest.mock import MagicMock, call

from synth_ui.clients.engine import (
    ENGINE_REGISTRY,
    EngineContext,
    ModHostEngine,
)
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.voice import Voice

SFIZZ = Voice(name="Piano", engine="sfizz", path="/sfz/piano.sfz", category="Piano")
SFIZZ2 = Voice(name="Piano2", engine="sfizz", path="/sfz/p2.sfz", category="Piano")
DEXED = Voice(name="EP", engine="dexed", path="/dx/ep.syx", category="EP")

SFIZZ_URI = "http://sfztools.github.io/sfizz"
DEXED_URI = "https://asb2m10.github.io/dexed"


class FakeJack:
    """Stub JackGraph.ports: keyed on (client, type, is_output)."""

    def __init__(self, ports=None):
        self._q = ports or {}

    def ports(
        self, *, client=None, type=None, is_output=None, contains=None, snapshot=None
    ):
        return list(self._q.get((client, type, is_output), []))


def ctx_for(
    *,
    jack=None,
    mod_host=None,
    systemctl=None,
    mod_host_needed=None,
    slots=None,
):
    mh = mod_host or MagicMock()
    return EngineContext(
        jack=jack or FakeJack(),
        mod_host=mh,
        # A real InstrumentSlots over the mocked mod-host: slot allocation is
        # the behaviour under test here, not something worth faking.
        slots=slots if slots is not None else InstrumentSlots(mh),
        systemctl=systemctl or (lambda argv: 0),
        mod_host_needed=mod_host_needed or (lambda: False),
    )


# --- registry ---------------------------------------------------------------

def test_registry_maps_engines_and_shares_modhost_key():
    assert ENGINE_REGISTRY["sfizz"] is ModHostEngine
    assert ENGINE_REGISTRY["dexed"] is ModHostEngine
    # sfizz and dexed resolve to the same JACK source, so a swap is in-place
    assert ENGINE_REGISTRY["sfizz"].key == ENGINE_REGISTRY["dexed"].key == "modhost"


# --- process engines (systemd) ----------------------------------------------









# --- port discovery / readiness ---------------------------------------------





# --- mod-host engine (instruments stay resident) ----------------------------
#
# Plugin loading, presets, params and slot allocation are InstrumentSlots'
# business and are tested in test_slots.py. What matters here is how the engine
# uses a slot: which one it lands on, that its ports follow that slot, and that
# stopping bypasses rather than unloads.

MDA_EPIANO = "http://drobilla.net/plugins/mda/EPiano"
EPIANO = Voice(
    name="Rhodes EP", engine="modhost", path="", category="EP",
    uri=MDA_EPIANO, resident=True,
)
JX10 = Voice(
    name="JX10", engine="modhost", path="", category="Synth",
    uri="http://drobilla.net/plugins/mda/JX10", resident=True,
)


def _mh_ok():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.patch_set.return_value = True
    return mh


def test_start_acquires_a_slot_and_loads_the_plugin():
    mh = _mh_ok()
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    e.start()
    mh.load_plugin.assert_called_once_with(MDA_EPIANO, 0)
    assert e.instance == 0


def test_start_brings_up_mod_host_but_stop_never_takes_it_down():
    # mod-host hosts the master chain and the effects rack, not just this
    # instrument, so an instrument switch must never stop the process.
    calls: list[list[str]] = []
    ctx = ctx_for(mod_host=_mh_ok(), systemctl=lambda argv: calls.append(argv) or 0)
    e = ModHostEngine(EPIANO, ctx)
    e.start()
    e.stop()
    assert ["sudo", "systemctl", "start", "mod-host.service"] in calls
    assert ["sudo", "systemctl", "stop", "mod-host.service"] not in calls


def test_stop_bypasses_instead_of_unloading():
    # The plugin stays instantiated so switching back is instant.
    mh = _mh_ok()
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    e.start()
    mh.reset_mock()
    e.stop()
    mh.bypass.assert_called_once_with(0, True)
    mh.remove_plugin.assert_not_called()


def test_start_retries_until_mod_host_is_ready():
    # A freshly-started mod-host accepts TCP before its LV2 world scan is done,
    # so the first adds can bounce even though the socket is up.
    mh = MagicMock()
    mh.load_plugin.side_effect = [False, False, True]
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    e.start()
    assert mh.load_plugin.call_count == 3
    assert e.instance == 0


def test_start_gives_up_after_timeout():
    mh = MagicMock()
    mh.load_plugin.return_value = False
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    assert e._acquire_with_retry(EPIANO, timeout=0.3) is False
    assert mh.load_plugin.call_count > 1


def test_a_second_voice_gets_its_own_slot_and_the_first_stays_loaded():
    # The residency payoff: two instruments live at once, so switching between
    # them never re-instantiates either.
    mh = _mh_ok()
    ctx = ctx_for(mod_host=mh)
    e = ModHostEngine(EPIANO, ctx)
    e.start()
    mh.reset_mock()          # only what the *second* load does matters here
    assert e.load(JX10) is True
    assert e.instance == 1
    # Both stay loaded: taking a new slot must not evict the first voice.
    # (Slots clear the target instance before adding, so remove_plugin(1) is
    # expected; removing instance 0 would mean the first was unloaded.)
    assert ctx.slots.loaded_instances() == [0, 1]
    assert 0 not in [c.args[0] for c in mh.remove_plugin.call_args_list]


def test_switching_back_reuses_the_loaded_slot_without_touching_mod_host():
    mh = _mh_ok()
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    e.start()
    e.load(JX10)
    mh.reset_mock()
    assert e.load(EPIANO) is True
    assert e.instance == 0
    mh.load_plugin.assert_not_called()   # instant: nothing was instantiated


def test_activating_a_voice_bypasses_every_other_loaded_instrument():
    mh = _mh_ok()
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    e.start()
    mh.reset_mock()
    e.load(JX10)
    assert mh.bypass.call_args_list == [call(0, True), call(1, False)]


def test_ports_follow_the_engine_slot():
    # mod-host puts an instance's ports -- MIDI in (control) AND audio out --
    # under "effect_<instance>", so they move when the slot does.
    jack = FakeJack({
        ("effect_1", "audio", True): ["effect_1:out_l", "effect_1:out_r"],
        ("effect_1", "midi", False): ["effect_1:control"],
    })
    e = ModHostEngine(EPIANO, ctx_for(jack=jack, mod_host=_mh_ok()))
    e.load(JX10)   # lands on slot 0
    e.load(EPIANO)
    e._instance = 1
    assert e.audio_out_ports == ["effect_1:out_l", "effect_1:out_r"]
    assert e.midi_port == "effect_1:control"


def test_unallocated_engine_exposes_no_ports():
    # Must not fall back to matching some other instrument's ports.
    jack = FakeJack({("effect_0", "audio", True): ["effect_0:out_l"]})
    e = ModHostEngine(EPIANO, ctx_for(jack=jack, mod_host=_mh_ok()))
    assert e.audio_out_ports == []
    assert e.midi_port is None


def test_load_fails_when_mod_host_refuses_the_plugin():
    mh = MagicMock()
    mh.load_plugin.return_value = False
    e = ModHostEngine(EPIANO, ctx_for(mod_host=mh))
    assert e.load(EPIANO) is False


def test_voice_without_a_uri_fails_instead_of_loading_something_wrong():
    mh = _mh_ok()
    broken = Voice(name="Broken", engine="modhost", path="", category="")
    e = ModHostEngine(broken, ctx_for(mod_host=mh))
    assert e.load(broken) is False
    mh.load_plugin.assert_not_called()


def test_generic_modhost_voice_registers_under_the_shared_key():
    # Same JACK source as sfizz/dexed, so switching between any two LV2
    # instruments stays an in-place swap rather than a full re-patch.
    assert ENGINE_REGISTRY["modhost"] is ModHostEngine
    assert ENGINE_REGISTRY["modhost"].key == ENGINE_REGISTRY["sfizz"].key


# --- soundfont programs (one .sf2 is a library of voices) -------------------









def test_every_engine_validation_accepts_can_actually_be_loaded():
    # A hand-kept registry omitted `fluida`: soundfont voices validated as
    # usable, then failed with "unknown engine" at load time.
    from synth_ui.clients.voice import KNOWN_ENGINES

    assert KNOWN_ENGINES <= set(ENGINE_REGISTRY)


def test_soundfont_voices_are_played_by_mod_host():
    assert ENGINE_REGISTRY["fluida"] is ModHostEngine
