"""Tests for the Engine layer — all dependencies mocked, no hardware."""

from unittest.mock import MagicMock, call

from synth_ui.clients.engine import (
    ENGINE_REGISTRY,
    EngineContext,
    FluidSynthEngine,
    ModHostEngine,
)
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.voice import Voice

GM = Voice(name="GM", engine="fluidsynth", path="/sf/default.sf2", category="GM")
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
    fluidsynth=None,
    systemctl=None,
    mod_host_needed=None,
    slots=None,
):
    mh = mod_host or MagicMock()
    return EngineContext(
        jack=jack or FakeJack(),
        mod_host=mh,
        fluidsynth=fluidsynth or MagicMock(),
        # A real InstrumentSlots over the mocked mod-host: slot allocation is
        # the behaviour under test here, not something worth faking.
        slots=slots if slots is not None else InstrumentSlots(mh),
        systemctl=systemctl or (lambda argv: 0),
        mod_host_needed=mod_host_needed or (lambda: False),
    )


# --- registry ---------------------------------------------------------------

def test_registry_maps_engines_and_shares_modhost_key():
    assert ENGINE_REGISTRY["fluidsynth"] is FluidSynthEngine
    assert ENGINE_REGISTRY["sfizz"] is ModHostEngine
    assert ENGINE_REGISTRY["dexed"] is ModHostEngine
    # sfizz and dexed resolve to the same JACK source, so a swap is in-place
    assert ENGINE_REGISTRY["sfizz"].key == ENGINE_REGISTRY["dexed"].key == "modhost"
    assert FluidSynthEngine.key == "fluidsynth"


# --- process engines (systemd) ----------------------------------------------

def test_process_engine_start_stop_shell_out_to_systemctl():
    calls: list[list[str]] = []
    ctx = ctx_for(systemctl=lambda argv: calls.append(argv) or 0)
    e = FluidSynthEngine(GM, ctx)
    e.start()
    e.stop()
    assert ["sudo", "systemctl", "start", "fluidsynth-engine.service"] in calls
    assert ["sudo", "systemctl", "stop", "fluidsynth-engine.service"] in calls


def test_fluidsynth_load_and_panic_delegate_to_controller():
    fs = MagicMock()
    fs.load_soundfont.return_value = True
    e = FluidSynthEngine(GM, ctx_for(fluidsynth=fs))
    assert e.load(GM) is True
    fs.load_soundfont.assert_called_once_with("/sf/default.sf2")
    e.panic()
    fs.reset.assert_called_once()


def test_fluidsynth_default_font_selects_instead_of_reloading(tmp_path, monkeypatch):
    # The default soundfont is already resident (loaded at process start), so
    # switching to it must be a preset select, NOT a second multi-hundred-MB load.
    sf = tmp_path / "default.sf2"
    sf.write_bytes(b"sf2")
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(sf))
    fs = MagicMock()
    voice = Voice(name="General MIDI", engine="fluidsynth", path=str(sf), category="GM")
    e = FluidSynthEngine(voice, ctx_for(fluidsynth=fs))
    assert e.load(voice) is True
    fs.load_soundfont.assert_not_called()
    fs.select_preset.assert_called_once_with(0, 1, 0, 0)


def test_fluidsynth_default_font_matches_through_symlink(tmp_path, monkeypatch):
    # default.sf2 is a symlink to the real font on the Pi; samefile must see them
    # as one file so the symlinked voice path still hits the fast select path.
    real = tmp_path / "FluidR3_GM.sf2"
    real.write_bytes(b"sf2")
    link = tmp_path / "default.sf2"
    link.symlink_to(real)
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(link))
    fs = MagicMock()
    voice = Voice(name="GM", engine="fluidsynth", path=str(real), category="GM")
    e = FluidSynthEngine(voice, ctx_for(fluidsynth=fs))
    assert e.load(voice) is True
    fs.load_soundfont.assert_not_called()
    fs.select_preset.assert_called_once_with(0, 1, 0, 0)


# --- port discovery / readiness ---------------------------------------------

def test_ports_discovered_via_jackgraph_and_ready():
    jack = FakeJack(
        {
            ("fluidsynth", "midi", False): ["fluidsynth:midi"],
            ("fluidsynth", "audio", True): ["fluidsynth:l", "fluidsynth:r"],
        }
    )
    e = FluidSynthEngine(GM, ctx_for(jack=jack))
    assert e.midi_port == "fluidsynth:midi"
    assert e.audio_out_ports == ["fluidsynth:l", "fluidsynth:r"]
    assert e.is_ready() is True


def test_not_ready_and_no_midi_when_ports_absent():
    e = FluidSynthEngine(GM, ctx_for(jack=FakeJack()))
    assert e.is_ready() is False
    assert e.midi_port is None
    assert e.audio_out_ports == []


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
    assert e.load(JX10) is True
    assert e.instance == 1
    mh.remove_plugin.assert_not_called()
    assert ctx.slots.loaded_instances() == [0, 1]


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

def test_program_selects_the_instrument_inside_the_default_font(tmp_path, monkeypatch):
    # Without this a whole GM font collapses to one voice and its Rhodes,
    # Wurlitzer, organ and synth brass are unreachable.
    sf = tmp_path / "default.sf2"
    sf.write_text("")
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(sf))
    fs = MagicMock()
    voice = Voice("Wurlitzer", "fluidsynth", str(sf), "EP", program=5)

    assert FluidSynthEngine(voice, ctx_for(fluidsynth=fs)).load(voice) is True
    fs.select_preset.assert_called_once_with(0, 1, 0, 5)
    fs.load_soundfont.assert_not_called()   # font already resident


def test_bank_is_honoured_for_variation_banks(tmp_path, monkeypatch):
    sf = tmp_path / "default.sf2"
    sf.write_text("")
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(sf))
    fs = MagicMock()
    voice = Voice("FM EP", "fluidsynth", str(sf), "EP", bank=8, program=5)
    FluidSynthEngine(voice, ctx_for(fluidsynth=fs)).load(voice)
    fs.select_preset.assert_called_once_with(0, 1, 8, 5)


def test_a_voice_with_no_program_keeps_the_engines_current_instrument(
    tmp_path, monkeypatch
):
    sf = tmp_path / "default.sf2"
    sf.write_text("")
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(sf))
    fs = MagicMock()
    voice = Voice("GM", "fluidsynth", str(sf), "GM")   # program defaults to -1
    FluidSynthEngine(voice, ctx_for(fluidsynth=fs)).load(voice)
    fs.select_preset.assert_called_once_with(0, 1, 0, 0)


def test_a_non_default_font_selects_on_its_own_sfont_id(tmp_path, monkeypatch):
    # The engine boots with the default font as sfont 1, so a font loaded later
    # is not 1 — selecting on 1 would pick from the wrong font entirely.
    default = tmp_path / "default.sf2"
    default.write_text("")
    other = tmp_path / "other.sf2"
    other.write_text("")
    monkeypatch.setattr("synth_ui.clients.engine._DEFAULT_SOUNDFONT", str(default))
    fs = MagicMock()
    fs.load_soundfont.return_value = True
    fs.current_sfont_id.return_value = 2
    voice = Voice("Other", "fluidsynth", str(other), "Piano", program=4)

    FluidSynthEngine(voice, ctx_for(fluidsynth=fs)).load(voice)
    fs.load_soundfont.assert_called_once_with(str(other))
    fs.select_preset.assert_called_once_with(0, 2, 0, 4)
