"""Tests for effect parameter discovery and the value formatting around it."""

from synth_ui.clients.lv2 import ControlPort, parse_control_ports
from synth_ui.ui.screens.params import format_value

# Trimmed from real `lv2info` output: two usable controls, one meter that isn't.
# Indented with spaces here; lv2info itself uses tabs, which the parser doesn't
# care about (see test_tab_indentation_parses_identically).
LV2INFO = """
    Port 0:
        Type:       http://lv2plug.in/ns/lv2core#InputPort
                    http://lv2plug.in/ns/lv2core#ControlPort
        Symbol:     level_in
        Name:       Input gain
        Minimum:    0.015625
        Maximum:    64.000000
        Default:    1.000000

    Port 1:
        Type:       http://lv2plug.in/ns/lv2core#InputPort
                    http://lv2plug.in/ns/lv2core#ControlPort
        Properties: http://lv2plug.in/ns/lv2core#toggled
        Symbol:     bypass
        Name:       Bypass
        Minimum:    0.000000
        Maximum:    1.000000
        Default:    0.000000

    Port 2:
        Type:       http://lv2plug.in/ns/lv2core#InputPort
                    http://lv2plug.in/ns/lv2core#ControlPort
        Symbol:     meter_in
        Name:       Input meter
        Minimum:    0.000000
        Maximum:    1.000000
        Default:    0.000000

    Port 3:
        Type:       http://lv2plug.in/ns/lv2core#OutputPort
                    http://lv2plug.in/ns/lv2core#AudioPort
        Symbol:     out_l
        Name:       Out L

    Port 4:
        Type:       http://lv2plug.in/ns/lv2core#InputPort
                    http://lv2plug.in/ns/lv2core#ControlPort
        Symbol:     no_range
        Name:       Undeclared range

    Port 5:
        Type:       http://lv2plug.in/ns/lv2core#InputPort
                    http://lv2plug.in/ns/lv2core#ControlPort
        Scale Points:
            0 = "Shiny1"
            5 = "Blah"
            23 = "Reed"

        Symbol:     o1wave
        Name:       Osc1 Wave
        Minimum:    0.000000
        Maximum:    28.000000
        Default:    28.000000
        Properties: http://lv2plug.in/ns/lv2core#integer
                    http://lv2plug.in/ns/lv2core#enumeration
"""


class TestParseControlPorts:
    def test_finds_input_control_ports(self):
        symbols = [p.symbol for p in parse_control_ports(LV2INFO)]
        assert "level_in" in symbols

    def test_reads_the_range_and_default(self):
        port = next(p for p in parse_control_ports(LV2INFO) if p.symbol == "level_in")
        assert (port.minimum, port.maximum, port.default) == (0.015625, 64.0, 1.0)
        assert port.name == "Input gain"

    def test_audio_and_output_ports_are_ignored(self):
        assert "out_l" not in [p.symbol for p in parse_control_ports(LV2INFO)]

    def test_a_port_without_a_range_is_skipped(self):
        """No range means no slider; inventing 0..1 would send values the plugin
        rejects."""
        assert "no_range" not in [p.symbol for p in parse_control_ports(LV2INFO)]

    def test_toggles_are_marked(self):
        port = next(p for p in parse_control_ports(LV2INFO) if p.symbol == "bypass")
        assert port.toggled

    def test_an_enumerated_port_survives_the_blank_line_inside_it(self):
        """lv2info puts a blank line between a port's Scale Points and its
        Symbol. Splitting records on blank lines tore those ports in half and
        dropped every one — which hid every wavetable, filter mode and delay
        timing on the board behind a screen that claimed the plugin had none."""
        port = next(p for p in parse_control_ports(LV2INFO) if p.symbol == "o1wave")
        assert (port.minimum, port.maximum) == (0.0, 28.0)
        assert port.integer

    def test_enumerated_settings_keep_their_names(self):
        port = next(p for p in parse_control_ports(LV2INFO) if p.symbol == "o1wave")
        assert port.scale_points == {0.0: "Shiny1", 5.0: "Blah", 23.0: "Reed"}

    def test_a_plain_port_has_no_scale_points(self):
        port = next(p for p in parse_control_ports(LV2INFO) if p.symbol == "level_in")
        assert port.scale_points == {}

    def test_meters_and_bypass_are_hidden(self):
        """Calf exposes meters as control inputs. They aren't knobs, and showing
        them buries the controls that matter."""
        hidden = {p.symbol for p in parse_control_ports(LV2INFO) if p.hidden}
        assert {"meter_in", "bypass"} <= hidden


class TestClamp:
    def test_clamps_to_the_declared_range(self):
        port = ControlPort("x", "X", minimum=0.0, maximum=1.0, default=0.5)
        assert port.clamp(2.0) == 1.0
        assert port.clamp(-1.0) == 0.0


class TestFormatValue:
    def test_toggle_reads_as_on_off(self):
        port = ControlPort("x", "X", 0.0, 1.0, 0.0, toggled=True)
        assert format_value(port)(1.0) == "on"
        assert format_value(port)(0.0) == "off"

    def test_integer_drops_the_decimal(self):
        port = ControlPort("x", "X", 0.0, 8.0, 1.0, integer=True)
        assert format_value(port)(4.0) == "4"

    def test_narrow_range_gets_more_precision(self):
        """0.1 steps are meaningless on a 0..1 control."""
        port = ControlPort("x", "X", 0.0, 1.0, 0.5)
        assert format_value(port)(0.25) == "0.25"

    def test_wide_range_gets_one_decimal(self):
        port = ControlPort("x", "X", 0.0, 64.0, 1.0)
        assert format_value(port)(12.34) == "12.3"

    def test_an_enumeration_reads_out_its_setting(self):
        # "Blah" is choosable by ear; 5 is not.
        port = ControlPort("o1wave", "Osc1 Wave", 0.0, 28.0, 0.0, integer=True,
                           scale_points={0.0: "Shiny1", 5.0: "Blah"})
        assert format_value(port)(5.0) == "Blah"
        assert format_value(port)(5.4) == "Blah"      # mid-drag, between detents

    def test_an_enumeration_falls_back_to_the_number(self):
        """A plugin needn't label every value in its range."""
        port = ControlPort("mode", "Mode", 0.0, 9.0, 0.0, integer=True,
                           scale_points={0.0: "Off"})
        assert format_value(port)(7.0) == "7"


class TestWhitespace:
    def test_tab_indentation_parses_identically(self):
        """lv2info indents with tabs. The fixture above uses spaces for the
        linter's sake, so prove the parser doesn't distinguish them."""
        tabbed = LV2INFO.replace("    ", "\t")
        assert [p.symbol for p in parse_control_ports(tabbed)] == [
            p.symbol for p in parse_control_ports(LV2INFO)
        ]
