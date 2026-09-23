"""Tests for EffectsRack — fake jack + mocked mod-host, no hardware."""

from unittest.mock import MagicMock

from synth_ui.clients.effects_rack import EffectsRack
from synth_ui.clients.lv2 import ControlPort, PortDefaults
from synth_ui.clients.rig import RigEffect, plan

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


# --- applying a rig's chain (diff, not rebuild) -----------------------------

def test_apply_reuses_shared_effects_and_loads_only_the_new_one():
    jack = FakeJack(ports=PORTS)
    mh = MagicMock()
    mh.load_plugin.return_value = True
    rack = EffectsRack(jack, mh)
    assert rack.add("urn:a") == 10
    mh.reset_mock()

    target = [RigEffect("urn:a"), RigEffect("urn:b", {"mix": 0.4})]
    assert rack.apply(plan(rack.snapshot(), target)) is True
    # urn:a kept at its instance; only urn:b instantiated
    mh.load_plugin.assert_called_once_with("urn:b", 11)
    mh.remove_plugin.assert_not_called()
    mh.set_param.assert_called_once_with(11, "mix", "0.4")
    assert rack.snapshot() == [(10, "urn:a"), (11, "urn:b")]


def test_apply_unloads_effects_the_rig_does_not_use():
    rack = EffectsRack(FakeJack(ports=PORTS), _mh())
    rack.add("urn:a")
    rack.apply(plan(rack.snapshot(), []))
    assert rack.is_empty()


def test_apply_reorders_without_reloading():
    mh = _mh()
    rack = EffectsRack(FakeJack(ports=PORTS), mh)
    rack.add("urn:a")
    rack.add("urn:b")
    mh.reset_mock()
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:b"), RigEffect("urn:a")]))
    mh.load_plugin.assert_not_called()
    mh.remove_plugin.assert_not_called()
    assert rack.snapshot() == [(11, "urn:b"), (10, "urn:a")]


def test_apply_reuses_freed_slots():
    rack = EffectsRack(FakeJack(ports=PORTS), _mh())
    rack.add("urn:a")
    rack.add("urn:b")
    # drop the first, add a third: the freed slot 10 is reused rather than
    # numbering climbing towards the master chain at 90
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:b"), RigEffect("urn:c")]))
    assert sorted(i for i, _ in rack.snapshot()) == [10, 11]


# --- a chain carries every control, so nothing hangs over -------------------

def _defaults(**controls):
    """A PortDefaults reporting these symbol=default controls, without running
    lv2info."""
    ports = [ControlPort(s, s, 0.0, 10.0, v) for s, v in controls.items()]
    return PortDefaults(read=lambda uri: ports)


def _params_written(mh):
    return {(c.args[1], c.args[2]) for c in mh.set_param.call_args_list}


def test_a_new_effect_starts_at_every_default():
    # Seeded rather than left empty: the values are what the plugin is actually
    # at, so the rig this is saved into describes the whole effect.
    rack = EffectsRack(
        FakeJack(PORTS), _mh(), defaults=_defaults(decay=1.5, mix=0.25)
    )
    rack.add("urn:reverb")
    assert rack.effects()[0].params == {"decay": 1.5, "mix": 0.25}


def test_switching_rigs_writes_the_controls_the_new_rig_never_mentions():
    """The leak: two rigs share a reverb instance, and the incoming rig's
    params were the only thing written. A decay dialled to 4s in one rig stayed
    at 4s under the next rig that never mentioned decay."""
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh, defaults=_defaults(decay=1.5, mix=0.25))
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb", {"decay": 4.0})]))
    mh.reset_mock()

    # Rig B: same reverb, never touched, so it carries no decay of its own.
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb")]))

    mh.load_plugin.assert_not_called()          # instance still reused
    assert ("decay", "1.5") in _params_written(mh)


def test_a_reused_instance_keeps_what_the_new_rig_does_set():
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh, defaults=_defaults(decay=1.5, mix=0.25))
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb", {"decay": 4.0})]))
    mh.reset_mock()

    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb", {"mix": 0.9})]))
    written = _params_written(mh)
    assert ("mix", "0.9") in written and ("decay", "1.5") in written


def test_a_rig_saved_before_effects_carried_every_control_is_completed():
    # Every rig already on a board predates this. They have to stop leaking on
    # load, not only once they're next saved.
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh, defaults=_defaults(decay=1.5, mix=0.25))
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb", {"decay": 4.0})]))
    assert rack.effects()[0].params == {"decay": 4.0, "mix": 0.25}


def test_an_unreadable_plugin_falls_back_to_what_the_rig_stored():
    # lv2info missing or the plugin uninstalled: fill in nothing rather than
    # invent values, and write what the rig actually asked for.
    jack, mh = FakeJack(PORTS), _mh()
    rack = EffectsRack(jack, mh, defaults=PortDefaults(read=lambda uri: []))
    rack.apply(plan(rack.snapshot(), [RigEffect("urn:reverb", {"decay": 4.0})]))
    assert rack.effects()[0].params == {"decay": 4.0}
