"""Tests for EffectsRack — fake jack + mocked mod-host, no hardware."""

from unittest.mock import MagicMock

from synth_ui.clients.effects_rack import EffectsRack

# Canned audio ports for effect instances 10 and 11 (per-instance JACK clients).
PORTS = {
    ("effect_10", "audio", False): ["effect_10:in_l", "effect_10:in_r"],
    ("effect_10", "audio", True): ["effect_10:out_l", "effect_10:out_r"],
    ("effect_11", "audio", False): ["effect_11:in_l", "effect_11:in_r"],
    ("effect_11", "audio", True): ["effect_11:out_l", "effect_11:out_r"],
}


class FakeJack:
    def __init__(self, ports=None):
        self._ports = ports or {}
        self.connects: list = []
        self.disconnects: list = []

    def ports(
        self, *, client=None, type=None, is_output=None, contains=None, snapshot=None
    ):
        return list(self._ports.get((client, type, is_output), []))

    def dac_sinks(self, snapshot=None):
        return ["system:playback_1", "system:playback_2"]

    def connect(self, src, dst):
        self.connects.append((src, dst))
        return True

    def disconnect(self, src, dst):
        self.disconnects.append((src, dst))
        return True


def _mh(load=True, param=True):
    mh = MagicMock()
    mh.load_plugin.return_value = load
    mh.set_param.return_value = param
    return mh


def test_add_first_effect_loads_and_wires_to_dac():
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh)
    assert rack.add("urn:reverb") == 10
    mh.load_plugin.assert_called_once_with("urn:reverb", 10)
    assert rack.input_ports() == ["effect_10:in_l", "effect_10:in_r"]
    assert rack.output_ports() == ["effect_10:out_l", "effect_10:out_r"]
    assert ("effect_10:out_l", "system:playback_1") in jack.connects
    assert ("effect_10:out_r", "system:playback_2") in jack.connects


def test_add_second_effect_chains_and_reroutes_dac():
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh)
    rack.add("urn:a")  # effect_10 -> DAC
    jack.connects.clear()
    jack.disconnects.clear()

    assert rack.add("urn:b") == 11
    # stale 'effect_10 -> DAC' torn down before rewiring
    assert ("effect_10:out_l", "system:playback_1") in jack.disconnects
    # chain: effect_10.out -> effect_11.in
    assert ("effect_10:out_l", "effect_11:in_l") in jack.connects
    # new last effect_11 -> DAC
    assert ("effect_11:out_l", "system:playback_1") in jack.connects
    # first stays the instrument's entry point; last is now effect_11
    assert rack.input_ports() == ["effect_10:in_l", "effect_10:in_r"]
    assert rack.output_ports() == ["effect_11:out_l", "effect_11:out_r"]


def test_remove_effect_removes_plugin_and_rechains():
    rack = EffectsRack(FakeJack(PORTS), _mh())
    rack.add("urn:a")
    rack.add("urn:b")
    rack.remove(10)
    rack._mh.remove_plugin.assert_any_call(10)
    assert [e.instance for e in rack.effects()] == [11]


def test_clear_removes_all_effects():
    rack = EffectsRack(FakeJack(PORTS), _mh())
    rack.add("urn:a")
    rack.add("urn:b")
    rack.clear()
    assert rack.is_empty()
    assert rack._mh.remove_plugin.call_count == 2


def test_set_param_delegates_to_mod_host():
    mh = _mh()
    rack = EffectsRack(FakeJack(), mh)
    assert rack.set_param(10, "dry_wet", "0.3") is True
    mh.set_param.assert_called_once_with(10, "dry_wet", "0.3")


def test_empty_rack_exposes_no_ports():
    rack = EffectsRack(FakeJack(), _mh())
    assert rack.is_empty()
    assert rack.input_ports() == []
    assert rack.output_ports() == []


def test_add_returns_none_and_stays_empty_on_load_failure():
    rack = EffectsRack(FakeJack(), _mh(load=False))
    assert rack.add("urn:x") is None
    assert rack.is_empty()
