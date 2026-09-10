"""Tests for the permanent master chain (trim + limiter). No hardware."""

from unittest.mock import MagicMock

from synth_ui.clients.master_chain import MasterChain, MasterStage, sink_for

LIMITER = "http://calf.sourceforge.net/plugins/Limiter"
GAIN = "http://example.org/gain"

TRIM_STAGE = MasterStage(uri=GAIN, trim_symbol="gain", unit="db")
LIMIT_STAGE = MasterStage(uri=LIMITER, params={"limit": 0.89})


class FakeJack:
    """Ports keyed on (client, type, is_output), plus recorded patching."""

    def __init__(self, ports=None, dac=("system:playback_1", "system:playback_2")):
        self._q = dict(ports or {})
        self._dac = list(dac)
        self.connected: list[tuple[str, str]] = []
        self.disconnected: list[tuple[str, str]] = []

    def ports(self, *, client=None, type=None, is_output=None, **_):
        return list(self._q.get((client, type, is_output), []))

    def dac_sinks(self, snapshot=None):
        return list(self._dac)

    def connect(self, src, dst):
        self.connected.append((src, dst))
        return True

    def disconnect(self, src, dst):
        self.disconnected.append((src, dst))
        return True


def audio_ports(instance, ins=("in_l", "in_r"), outs=("out_l", "out_r")):
    client = f"effect_{instance}"
    return {
        (client, "audio", False): [f"{client}:{p}" for p in ins],
        (client, "audio", True): [f"{client}:{p}" for p in outs],
    }


def world(*installed):
    w = MagicMock()
    w.has.side_effect = lambda uri: uri in installed
    return w


# --- loading ----------------------------------------------------------------

def test_stages_load_at_instances_90_and_up():
    # Instruments own 0-9 and effects 10+, so the master must not collide.
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack({**audio_ports(90), **audio_ports(91)})
    chain = MasterChain(jack, mh, [TRIM_STAGE, LIMIT_STAGE], lv2=world(GAIN, LIMITER))
    assert chain.ensure() is True
    loads = [c.args for c in mh.load_plugin.call_args_list]
    assert loads == [(GAIN, 90), (LIMITER, 91)]


def test_stage_params_are_applied_at_load():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack(audio_ports(90))
    chain = MasterChain(jack, mh, [LIMIT_STAGE], lv2=world(LIMITER))
    chain.ensure()
    mh.set_param.assert_any_call(90, "limit", "0.89")


def test_chain_is_wired_stage_to_stage_then_to_the_dac():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack({**audio_ports(90), **audio_ports(91)})
    MasterChain(jack, mh, [TRIM_STAGE, LIMIT_STAGE], lv2=world(GAIN, LIMITER)).ensure()
    assert ("effect_90:out_l", "effect_91:in_l") in jack.connected
    assert ("effect_91:out_l", "system:playback_1") in jack.connected
    assert ("effect_91:out_r", "system:playback_2") in jack.connected


def test_ensure_is_idempotent():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack(audio_ports(90))
    chain = MasterChain(jack, mh, [LIMIT_STAGE], lv2=world(LIMITER))
    chain.ensure()
    chain.ensure()
    assert mh.load_plugin.call_count == 1


# --- degrading --------------------------------------------------------------

def test_a_missing_plugin_is_skipped_not_fatal():
    # A wrong/unbuilt URI must cost level control, never sound.
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack(audio_ports(91))
    chain = MasterChain(jack, mh, [TRIM_STAGE, LIMIT_STAGE], lv2=world(LIMITER))
    assert chain.ensure() is True
    mh.load_plugin.assert_called_once_with(LIMITER, 91)


def test_no_stages_loaded_means_no_chain():
    mh = MagicMock()
    chain = MasterChain(FakeJack(), mh, [TRIM_STAGE], lv2=world())
    assert chain.ensure() is False
    assert chain.is_ready() is False
    assert chain.input_ports() == []


def test_sink_falls_back_to_the_dac_when_the_chain_is_down():
    # Upstream stages route into sink_for() and never need to know whether a
    # master chain exists — silence is not an acceptable failure mode.
    jack = FakeJack()
    chain = MasterChain(jack, MagicMock(), [TRIM_STAGE], lv2=world())
    chain.ensure()
    assert sink_for(chain, jack)() == ["system:playback_1", "system:playback_2"]


def test_sink_prefers_the_master_chain_when_it_is_up():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack(audio_ports(90))
    chain = MasterChain(jack, mh, [TRIM_STAGE], lv2=world(GAIN))
    chain.ensure()
    assert sink_for(chain, jack)() == ["effect_90:in_l", "effect_90:in_r"]


# --- level ------------------------------------------------------------------

def make_chain(unit="db", **kw):
    mh = MagicMock()
    mh.load_plugin.return_value = True
    stage = MasterStage(uri=GAIN, trim_symbol="gain", unit=unit, **kw)
    chain = MasterChain(FakeJack(audio_ports(90)), mh, [stage], lv2=world(GAIN))
    chain.ensure()
    mh.reset_mock()
    return chain, mh


def test_volume_and_trim_collapse_when_only_one_gain_control_exists():
    chain, mh = make_chain()
    chain.set_volume_db(-6.0)
    chain.set_trim_db(-3.0)
    assert chain.level_db == -9.0
    assert mh.set_param.call_args.args == (90, "gain", "-9.0")


def test_trim_goes_pre_limiter_and_volume_post():
    # Separate ports so a hot voice is still limited (trim before) while the
    # volume control doesn't change how hard the limiter works (volume after).
    mh = MagicMock()
    mh.load_plugin.return_value = True
    stage = MasterStage(
        uri=GAIN, trim_symbol="level_in", volume_symbol="level_out", unit="db"
    )
    chain = MasterChain(FakeJack(audio_ports(90)), mh, [stage], lv2=world(GAIN))
    chain.ensure()
    mh.reset_mock()

    chain.set_trim_db(-3.0)
    chain.set_volume_db(-6.0)
    calls = dict((c.args[1], c.args[2]) for c in mh.set_param.call_args_list)
    assert calls["level_in"] == "-3.0"
    assert calls["level_out"] == "-6.0"


def test_values_are_clamped_into_the_port_range():
    # Calf's gains bottom out at 1/64; sending 0 for "silence" would be an
    # out-of-range value, not a quiet one.
    chain, mh = make_chain(unit="linear", minimum=0.015625, maximum=64.0)
    chain.set_volume_db(-90.0)
    assert float(mh.set_param.call_args.args[2]) == 0.015625
    chain.set_volume_db(48.0)
    assert float(mh.set_param.call_args.args[2]) == 64.0


def test_linear_control_gets_a_multiplier_not_decibels():
    # Calf's level_in is linear; sending dB there would be a huge level error.
    chain, mh = make_chain(unit="linear")
    chain.set_volume_db(-6.0)
    value = float(mh.set_param.call_args.args[2])
    assert abs(value - 0.501) < 0.001


def test_silence_floor_maps_to_zero_on_an_unbounded_linear_control():
    chain, mh = make_chain(unit="linear")
    chain.set_volume_db(-90.0)
    assert float(mh.set_param.call_args.args[2]) == 0.0


def test_teardown_unwires_and_removes_every_stage():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    jack = FakeJack(audio_ports(90))
    chain = MasterChain(jack, mh, [TRIM_STAGE], lv2=world(GAIN))
    chain.ensure()
    chain.teardown()
    assert ("effect_90:out_l", "system:playback_1") in jack.disconnected
    mh.remove_plugin.assert_called_once_with(90)
    assert chain.is_ready() is False
