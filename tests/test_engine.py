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


def test_modhost_start_retries_plugin_load_until_mod_host_is_ready():
    # A freshly-started mod-host accepts TCP connections before it's actually
    # ready to host a plugin, so the first add(s) can fail even though the
    # socket is up. start() must retry the load itself, not just the connect.
    mh = MagicMock()
    mh.load_plugin.side_effect = [False, False, True]
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()
    assert mh.load_plugin.call_count == 3
    assert e._loaded_uri == SFIZZ_URI


def test_modhost_start_gives_up_after_timeout():
    mh = MagicMock()
    mh.load_plugin.return_value = False
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    assert e._load_plugin_with_retry(SFIZZ, timeout=0.3) is False
    assert mh.load_plugin.call_count > 1


def test_modhost_swaps_plugin_on_engine_change():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.patch_set.return_value = True
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()             # sfizz loaded
    mh.reset_mock()
    assert e.load(DEXED) is True   # in-place switch to dexed
    mh.remove_plugin.assert_called_once_with(0)
    mh.load_plugin.assert_called_once_with(DEXED_URI, 0)
    # dexed's file-load property URI is unverified (empty) -> no patch_set yet
    mh.patch_set.assert_not_called()


def test_modhost_same_plugin_only_sets_the_instrument_file():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.patch_set.return_value = True
    e = ModHostEngine(SFIZZ, ctx_for(mod_host=mh))
    e.start()
    mh.reset_mock()
    assert e.load(SFIZZ2) is True  # different sfizz voice, same plugin
    mh.load_plugin.assert_not_called()
    mh.remove_plugin.assert_not_called()
    # SFZ file is a patch property loaded via patch_set (not param_set), unquoted
    mh.patch_set.assert_called_once_with(
        0, "http://sfztools.github.io/sfizz:sfzfile", "/sfz/p2.sfz"
    )


def test_modhost_ports_discovered_under_effect_instance_client():
    # mod-host puts a plugin instance's ports — MIDI in (control) AND audio out —
    # under "effect_<instance>", not "mod-host". mod-host:midi_in is a dead end
    # that never reaches the plugin. Confirmed on hardware.
    jack = FakeJack(
        {
            ("effect_0", "midi", False): ["effect_0:control"],
            ("effect_0", "audio", True): ["effect_0:out_left", "effect_0:out_right"],
        }
    )
    e = ModHostEngine(SFIZZ, ctx_for(jack=jack))
    assert e.audio_client == "effect_0"
    assert e.midi_port == "effect_0:control"
    assert e.audio_out_ports == ["effect_0:out_left", "effect_0:out_right"]
    assert e.is_ready() is True


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
    mh.patch_set.assert_not_called()
