"""Tests for the Engine layer — all dependencies mocked, no hardware."""

from unittest.mock import MagicMock

from synth_ui.clients.engine import (
    ENGINE_REGISTRY,
    EngineContext,
    FluidSynthEngine,
    ModHostEngine,
    SetBfreeEngine,
)
from synth_ui.clients.voice import Voice

GM = Voice(name="GM", engine="fluidsynth", path="/sf/default.sf2", category="GM")
ORGAN = Voice(name="B3", engine="setbfree", path="", category="Organ")
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


def ctx_for(*, jack=None, mod_host=None, fluidsynth=None, systemctl=None):
    return EngineContext(
        jack=jack or FakeJack(),
        mod_host=mod_host or MagicMock(),
        fluidsynth=fluidsynth or MagicMock(),
        systemctl=systemctl or (lambda argv: 0),
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


def test_setbfree_uses_its_unit_and_load_is_noop():
    calls: list[list[str]] = []
    e = SetBfreeEngine(ORGAN, ctx_for(systemctl=lambda argv: calls.append(argv) or 0))
    e.start()
    assert calls == [["sudo", "systemctl", "start", "setbfree.service"]]
    assert e.load(ORGAN) is True  # single-voice engine


def test_fluidsynth_load_and_panic_delegate_to_controller():
    fs = MagicMock()
    fs.load_soundfont.return_value = True
    e = FluidSynthEngine(GM, ctx_for(fluidsynth=fs))
    assert e.load(GM) is True
    fs.load_soundfont.assert_called_once_with("/sf/default.sf2")
    e.panic()
    fs.reset.assert_called_once()


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


# --- mod-host engine (sfizz / dexed share one slot) -------------------------

def test_modhost_start_clears_slot_then_loads_plugin():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    ModHostEngine(SFIZZ, ctx_for(mod_host=mh)).start()
    mh.remove_plugin.assert_called_once_with(0)          # clear slot first
    mh.load_plugin.assert_called_once_with(SFIZZ_URI, 0)


def test_modhost_start_stop_also_manage_the_systemd_unit():
    calls: list[list[str]] = []
    mh = MagicMock()
    mh.load_plugin.return_value = True
    ctx = ctx_for(mod_host=mh, systemctl=lambda argv: calls.append(argv) or 0)
    e = ModHostEngine(SFIZZ, ctx)
    e.start()
    e.stop()
    assert ["sudo", "systemctl", "start", "mod-host.service"] in calls
    assert ["sudo", "systemctl", "stop", "mod-host.service"] in calls


def test_modhost_start_waits_for_socket_before_loading_plugin():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.is_connected.side_effect = [False, False, True]
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e._wait_until_connected(timeout=1.0)
    assert mh.is_connected.call_count == 3


def test_modhost_swaps_plugin_on_engine_change():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.set_param.return_value = True
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()             # sfizz loaded
    mh.reset_mock()
    assert e.load(DEXED) is True   # in-place switch to dexed
    mh.remove_plugin.assert_called_once_with(0)
    mh.load_plugin.assert_called_once_with(DEXED_URI, 0)
    mh.set_param.assert_called_once_with(0, "sysex_file", "'/dx/ep.syx'")


def test_modhost_same_plugin_only_sets_param():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.set_param.return_value = True
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()
    mh.reset_mock()
    assert e.load(SFIZZ2) is True  # different sfizz voice, same plugin
    mh.load_plugin.assert_not_called()
    mh.remove_plugin.assert_not_called()
    mh.set_param.assert_called_once_with(0, "sfz_file", "'/sfz/p2.sfz'")


def test_modhost_stop_removes_plugin():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()
    mh.reset_mock()
    e.stop()
    mh.remove_plugin.assert_called_once_with(0)


def test_modhost_load_fails_when_plugin_add_fails():
    mh = MagicMock()
    mh.load_plugin.return_value = False
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    assert e.load(SFIZZ) is False
    mh.set_param.assert_not_called()
