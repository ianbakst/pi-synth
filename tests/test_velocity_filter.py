"""Tests for the fixed-velocity MIDI filter. No hardware.

The arithmetic these pin down is midifilter's, not ours (see
clients/velocity_filter.py): the parameters are the whole feature, so a wrong
`onmin`/`onmax` pair is a rig that plays at the wrong dynamic with nothing
visibly broken.
"""

from unittest.mock import MagicMock

from synth_ui.clients.velocity_filter import VELOCITY_FILTER_URI, VelocityFilter


class FakeJack:
    """Ports keyed on (client, type, is_output), plus recorded patching."""

    def __init__(self, ports=None):
        self._q = dict(ports or {})
        self.connected: list[tuple[str, str]] = []
        self.disconnected: list[tuple[str, str]] = []

    def ports(self, *, client=None, type=None, is_output=None, **_):
        return list(self._q.get((client, type, is_output), []))

    def connect(self, src, dst):
        self.connected.append((src, dst))
        return True

    def disconnect(self, src, dst):
        self.disconnected.append((src, dst))
        return True


def midi_ports(instance=100):
    client = f"effect_{instance}"
    return {
        (client, "midi", False): [f"{client}:midiin"],
        (client, "midi", True): [f"{client}:midiout"],
    }


def world(*installed):
    w = MagicMock()
    w.has.side_effect = lambda uri: uri in installed
    return w


def make(ports=None, installed=(VELOCITY_FILTER_URI,), load_ok=True):
    mh = MagicMock()
    mh.load_plugin.return_value = load_ok
    jack = FakeJack(midi_ports() if ports is None else ports)
    return VelocityFilter(jack, mh, lv2=world(*installed)), jack, mh


def params(mh) -> dict[str, str]:
    """The last value set for each control symbol."""
    return {c.args[1]: c.args[2] for c in mh.set_param.call_args_list}


# --- availability -----------------------------------------------------------

def test_unavailable_when_the_plugin_is_not_installed():
    filt, _, mh = make(installed=())
    assert filt.available is False
    assert filt.ensure() is False
    mh.load_plugin.assert_not_called()


def test_output_port_is_none_until_loaded():
    # EngineManager reads this as "wire the keyboards straight to the
    # instrument", so it must not name a port before the plugin exists.
    filt, _, _ = make()
    assert filt.output_port() is None
    filt.ensure()
    assert filt.output_port() == "effect_100:midiout"


def test_ensure_is_idempotent():
    filt, _, mh = make()
    assert filt.ensure() is True
    assert filt.ensure() is True
    assert mh.load_plugin.call_count == 1


def test_a_refused_load_leaves_the_filter_out_of_the_path():
    filt, _, _ = make(load_ok=False)
    assert filt.ensure() is False
    assert filt.is_loaded is False
    assert filt.output_port() is None


# --- the parameters that are the feature ------------------------------------

def test_loading_starts_transparent():
    # onmin=1, onmax=127 is midifilter's exact identity: out == vel for every
    # input. A filter that coloured playing the moment it loaded would make
    # enabling it once change how every later rig sounds.
    filt, _, mh = make()
    filt.ensure()
    assert params(mh)["onmin"] == "1.0"
    assert params(mh)["onmax"] == "127.0"


def test_fixed_pins_both_ends_to_the_same_value():
    # onmin == onmax makes the velocity term drop out of midifilter's mapping,
    # so every note-on leaves at exactly this value.
    filt, _, mh = make()
    filt.ensure()
    filt.set_fixed(100)
    assert params(mh)["onmin"] == "100.0"
    assert params(mh)["onmax"] == "100.0"
    assert params(mh)["onoff"] == "0.0"


def test_zero_means_play_as_struck():
    filt, _, mh = make()
    filt.ensure()
    filt.set_fixed(100)
    filt.set_fixed(0)
    assert filt.velocity == 0
    assert (params(mh)["onmin"], params(mh)["onmax"]) == ("1.0", "127.0")


def test_velocity_is_clamped_into_the_ports_range():
    filt, _, mh = make()
    filt.ensure()
    filt.set_fixed(9999)
    assert filt.velocity == 127
    assert params(mh)["onmax"] == "127.0"


def test_a_setting_made_before_loading_survives_the_load():
    # load_rig can ask for fixed velocity before mod-host has the plugin; the
    # value has to still be there when it does.
    filt, _, mh = make()
    filt.set_fixed(64)
    filt.ensure()
    assert params(mh)["onmin"] == "64.0"


def test_channel_is_any():
    # Not channel 1: the setting is about what the keyboard sends, and a
    # keyboard transmitting on another channel would silently be unaffected.
    filt, _, mh = make()
    filt.ensure()
    assert params(mh)["channel"] == "0"


# --- patching ---------------------------------------------------------------

def test_attach_patches_keyboards_into_the_filter():
    filt, jack, _ = make()
    filt.ensure()
    filt.attach(["a2j:Keyboard", "ttymidi:MIDI_in"])
    assert jack.connected == [
        ("a2j:Keyboard", "effect_100:midiin"),
        ("ttymidi:MIDI_in", "effect_100:midiin"),
    ]


def test_attach_does_not_repatch_a_source_it_already_has():
    # _wire() calls this on every voice switch; re-issuing connects would be
    # noise on every change of instrument.
    filt, jack, _ = make()
    filt.ensure()
    filt.attach(["a2j:Keyboard"])
    filt.attach(["a2j:Keyboard", "a2j:Pedals"])
    assert jack.connected == [
        ("a2j:Keyboard", "effect_100:midiin"),
        ("a2j:Pedals", "effect_100:midiin"),
    ]


def test_attach_is_a_noop_when_the_filter_is_not_loaded():
    filt, jack, _ = make()
    filt.attach(["a2j:Keyboard"])
    assert jack.connected == []


def test_ports_are_matched_by_symbol_not_position():
    # Wiring these the wrong way round looks exactly like a dead keyboard, so
    # the in/out choice must not depend on the order jack_lsp happens to list.
    reversed_ports = {
        ("effect_100", "midi", False): ["effect_100:midiin"],
        ("effect_100", "midi", True): ["effect_100:zzz", "effect_100:midiout"],
    }
    filt, _, _ = make(ports=reversed_ports)
    filt.ensure()
    assert filt.output_port() == "effect_100:midiout"


def test_teardown_forgets_the_filter():
    # After mod-host restarts, claiming to be loaded would point the instrument
    # at a port that no longer exists.
    filt, _, mh = make()
    filt.ensure()
    filt.attach(["a2j:Keyboard"])
    filt.teardown()
    assert filt.is_loaded is False
    assert filt.output_port() is None
    mh.remove_plugin.assert_called_once()


def test_teardown_then_ensure_repatches_the_keyboards():
    filt, jack, _ = make()
    filt.ensure()
    filt.attach(["a2j:Keyboard"])
    filt.teardown()
    filt.ensure()
    filt.attach(["a2j:Keyboard"])
    assert jack.connected.count(("a2j:Keyboard", "effect_100:midiin")) == 2
