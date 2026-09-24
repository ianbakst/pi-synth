"""Tests for turning plugin settings into controls, and the widgets that draw
them on the touchscreen."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from synth_ui.clients.controls import (  # noqa: E402
    CHOICE,
    KNOB,
    TOGGLE,
    control_for,
    controls_for,
)
from synth_ui.clients.lv2 import ControlPort, parse_control_ports  # noqa: E402
from synth_ui.ui.components.controls import (  # noqa: E402
    KNOB_DRAG_PX,
    Choice,
    Knob,
    Toggle,
    fit_text,
    widget_for,
)
from synth_ui.ui.event import UIEvent  # noqa: E402
from synth_ui.ui.screens.params import ParamsScreen  # noqa: E402


def _port(**kw) -> ControlPort:
    base = dict(symbol="x", name="X", minimum=0.0, maximum=1.0, default=0.5)
    return ControlPort(**{**base, **kw})


class TestParsing:
    # Trimmed from the board's `lv2info` for Calf's EQ.
    LV2INFO = """
    Port 17:
        Type:        http://lv2plug.in/ns/lv2core#ControlPort
                     http://lv2plug.in/ns/lv2core#InputPort
        Symbol:      ls_freq
        Name:        Freq L
        Minimum:     10.000000
        Maximum:     20000.000000
        Default:     100.000000
        Properties:  http://lv2plug.in/ns/ext/port-props#logarithmic
                     http://lv2plug.in/ns/ext/port-props#hasStrictBounds
    """

    def test_logarithmic_is_read(self):
        (port,) = parse_control_ports(self.LV2INFO)
        assert port.logarithmic
        assert not port.enumeration


class TestKind:
    def test_a_toggle(self):
        assert control_for(_port(toggled=True)).kind == TOGGLE

    def test_an_enumeration_is_a_choice_of_its_named_values_only(self):
        c = control_for(_port(maximum=28.0, integer=True, enumeration=True,
                              scale_points={23.0: "Reed", 0.0: "Shiny1"}))
        assert c.kind == CHOICE
        assert c.options == [(0.0, "Shiny1"), (23.0, "Reed")]

    def test_a_small_integer_range_is_a_choice_of_every_value(self):
        c = control_for(_port(minimum=1.0, maximum=4.0, integer=True,
                              scale_points={1.0: "Off"}))
        assert c.kind == CHOICE
        assert c.options == [(1.0, "Off"), (2.0, "2"), (3.0, "3"), (4.0, "4")]

    def test_a_blank_label_reads_as_off(self):
        """Calf's EQ names each band's off state " "."""
        c = control_for(_port(maximum=2.0, integer=True, enumeration=True,
                              scale_points={0.0: " ", 1.0: "ON", 2.0: "  "}))
        assert c.options == [(0.0, "Off"), (1.0, "ON"), (2.0, "2")]

    def test_a_wide_integer_range_is_a_knob(self):
        """Choosing among 128 values one tap at a time is not a control."""
        assert control_for(_port(maximum=127.0, integer=True)).kind == KNOB

    def test_labels_without_enumeration_stay_a_knob_that_names_them(self):
        c = control_for(_port(maximum=9.0, integer=True, scale_points={0.0: "Off"}))
        assert c.kind == KNOB
        assert c.format(0.0) == "Off"

    def test_everything_else_is_a_knob(self):
        assert control_for(_port()).kind == KNOB

    def test_hidden_ports_are_left_out(self):
        assert controls_for([_port(symbol="meter", hidden=True), _port()]) == [
            control_for(_port())
        ]


class TestTravel:
    def test_linear(self):
        c = control_for(_port(minimum=-18.0, maximum=18.0))
        assert c.to_ratio(0.0) == 0.5
        assert c.from_ratio(0.75) == 9.0

    def test_logarithmic_puts_equal_ratios_at_equal_distances(self):
        c = control_for(_port(minimum=20.0, maximum=20000.0, logarithmic=True))
        # 20 -> 200 -> 2000 -> 20000: three decades, a third of the travel each.
        assert c.to_ratio(200.0) == pytest.approx(1 / 3)
        assert c.from_ratio(2 / 3) == pytest.approx(2000.0)

    def test_a_log_claim_over_zero_falls_back_to_linear(self):
        """log(0) is undefined; a plugin getting this wrong mustn't crash the
        screen."""
        c = control_for(_port(minimum=0.0, maximum=10.0, logarithmic=True))
        assert not c.logarithmic
        assert c.to_ratio(5.0) == 0.5

    def test_a_calf_gain_is_logarithmic_without_saying_so(self):
        """1/64..64, no log flag: linear would put unity at 1.5% of the travel."""
        c = control_for(_port(minimum=0.015625, maximum=64.0, default=1.0))
        assert c.logarithmic
        assert c.to_ratio(1.0) == pytest.approx(0.5)

    def test_a_narrow_positive_range_stays_linear(self):
        assert not control_for(_port(minimum=0.4, maximum=15.0)).logarithmic

    def test_ratios_outside_the_travel_clamp(self):
        c = control_for(_port())
        assert c.from_ratio(1.7) == 1.0
        assert c.from_ratio(-0.2) == 0.0

    def test_an_integer_knob_lands_on_integers(self):
        c = control_for(_port(maximum=100.0, integer=True))
        assert c.from_ratio(0.333) == 33.0

    def test_a_zero_width_range_does_not_divide_by_zero(self):
        assert control_for(_port(minimum=1.0, maximum=1.0)).to_ratio(1.0) == 0.0


class TestFormat:
    def test_thousands_read_as_k(self):
        c = control_for(_port(minimum=20.0, maximum=20000.0))
        assert c.format(1250.0) == "1.25k"
        assert c.format(12500.0) == "12.5k"

    def test_a_choice_reads_its_nearest_option(self):
        c = control_for(_port(maximum=2.0, integer=True))
        assert c.format(1.0000001) == "1"

    def test_to_dict_is_plain_data(self):
        """What a browser will be sent: nothing that isn't JSON."""
        import json

        c = control_for(_port(maximum=2.0, integer=True, scale_points={0.0: "A"}))
        assert json.loads(json.dumps(c.to_dict()))["options"][0] == [0.0, "A"]


# --- widgets ---------------------------------------------------------------

@pytest.fixture(scope="module")
def font():
    pygame.init()
    return pygame.font.Font(None, 22)


def _down(x, y):
    return UIEvent(pygame.FINGERDOWN, pos=(x, y))


def _move(x, y):
    return UIEvent(pygame.FINGERMOTION, pos=(x, y))


def _up(x, y):
    return UIEvent(pygame.FINGERUP, pos=(x, y))


def _widget(port, font, value=None):
    changes = []
    control = control_for(port)
    w = widget_for(
        pygame.Rect(0, 0, 190, 140), control,
        control.default if value is None else value, changes.append, font,
    )
    return w, changes


class TestKnob:
    def test_dragging_up_turns_it_up_by_distance_not_position(self, font):
        knob, changes = _widget(_port(default=0.25), font)
        assert isinstance(knob, Knob)
        knob.handle_event(_down(95, 100))
        knob.handle_event(_move(95, 100 - KNOB_DRAG_PX // 2))
        assert changes[-1] == pytest.approx(0.75)

    def test_touching_it_without_moving_changes_nothing(self, font):
        knob, changes = _widget(_port(), font)
        knob.handle_event(_down(95, 60))
        knob.handle_event(_up(95, 60))
        assert changes == []

    def test_the_drag_keeps_going_outside_the_cell(self, font):
        knob, changes = _widget(_port(default=0.0), font)
        knob.handle_event(_down(95, 130))
        knob.handle_event(_move(400, -500))
        assert changes[-1] == 1.0

    def test_an_integer_knob_only_reports_whole_steps(self, font):
        knob, changes = _widget(_port(maximum=100.0, integer=True, default=0.0), font)
        knob.handle_event(_down(95, 100))
        for y in range(100, 90, -1):
            knob.handle_event(_move(95, y))
        assert changes == sorted(set(changes))
        assert all(v == int(v) for v in changes)

    def test_it_draws_at_either_end_and_either_side_of_zero(self, font):
        surface = pygame.Surface((800, 480))
        for value in (-18.0, -3.0, 0.0, 6.0, 18.0):
            knob, _ = _widget(_port(minimum=-18.0, maximum=18.0), font, value)
            knob.draw(surface)


class TestToggle:
    def test_a_tap_flips_it(self, font):
        toggle, changes = _widget(_port(toggled=True, default=0.0), font)
        assert isinstance(toggle, Toggle)
        toggle.handle_event(_down(95, 55))
        toggle.handle_event(_up(95, 55))
        assert changes == [1.0]

    def test_sliding_off_before_lifting_cancels(self, font):
        toggle, changes = _widget(_port(toggled=True, default=0.0), font)
        toggle.handle_event(_down(95, 55))
        toggle.handle_event(_up(95, 300))
        assert changes == []


class TestChoice:
    def test_few_options_are_buttons(self, font):
        choice, changes = _widget(_port(maximum=2.0, integer=True, default=0.0), font)
        assert isinstance(choice, Choice)
        last = choice._segments()[-1]
        choice.handle_event(_down(*last.center))
        choice.handle_event(_up(*last.center))
        assert changes == [2.0]

    def test_many_options_step_and_wrap(self, font):
        port = _port(maximum=5.0, integer=True, default=5.0)
        choice, changes = _widget(port, font)
        right = choice._stepper.midright
        choice.handle_event(_down(right[0] - 5, right[1]))
        choice.handle_event(_up(right[0] - 5, right[1]))
        assert changes == [0.0]

    def test_both_forms_draw(self, font):
        surface = pygame.Surface((800, 480))
        for maximum in (2.0, 6.0):
            choice, _ = _widget(_port(maximum=maximum, integer=True), font)
            choice.draw(surface)


def test_long_names_are_cut_to_fit(font):
    text = fit_text(font, "Highshelf Frequency Bandwidth", 100)
    assert text.endswith("...")
    assert font.size(text)[0] <= 100


class TestPanel:
    def _screen(self, count, font, changes=None):
        ports = [_port(symbol=f"p{i}", name=f"P{i}") for i in range(count)]
        return ParamsScreen(
            name="X", ports=ports, values={},
            on_change=lambda s, v: (changes if changes is not None else []).append(
                (s, v)
            ),
            on_back=lambda: None,
        )

    def _tap(self, panel, rect):
        panel.handle_event(_down(*rect.center))
        panel.handle_event(_up(*rect.center))

    def test_twelve_controls_are_one_page(self, font):
        assert self._screen(12, font).controls.pages == 1

    def test_thirteen_are_two(self, font):
        panel = self._screen(13, font).controls
        assert panel.pages == 2
        assert len(panel.visible) == 12

    def test_the_next_button_turns_the_page(self, font):
        panel = self._screen(20, font).controls
        self._tap(panel, panel._next_button)
        assert panel.page == 1
        assert [w.control.symbol for w in panel.visible] == [
            f"p{i}" for i in range(12, 20)
        ]

    def test_pages_stop_at_the_ends(self, font):
        panel = self._screen(20, font).controls
        self._tap(panel, panel._prev_button)
        assert panel.page == 0
        for _ in range(3):
            self._tap(panel, panel._next_button)
        assert panel.page == 1

    def test_sliding_off_a_page_button_cancels(self, font):
        panel = self._screen(20, font).controls
        panel.handle_event(_down(*panel._next_button.center))
        panel.handle_event(_up(100, 100))
        assert panel.page == 0

    def test_a_control_on_a_later_page_takes_the_touch(self, font):
        changes = []
        screen = self._screen(14, font, changes)
        self._tap(screen.controls, screen.controls._next_button)
        x, y = screen.controls.visible[0].rect.center   # p12, top-left again
        screen.handle_event(_down(x, y))
        screen.handle_event(_move(x, y - 24))
        screen.handle_event(_up(x, y - 24))
        assert changes and changes[-1][0] == "p12"

    def test_every_page_draws(self, font):
        screen = self._screen(30, font)
        for _ in range(screen.controls.pages):
            screen.draw(pygame.Surface((800, 480)))
            screen.controls.turn(1)

    def test_a_knob_in_the_grid_reaches_the_plugin(self, font):
        changes = []
        screen = self._screen(5, font, changes)
        screen.draw(pygame.Surface((800, 480)))
        knob = screen.controls.widgets[4]   # second row, first column
        x, y = knob.rect.center
        screen.handle_event(_down(x, y))
        screen.handle_event(_move(x, y - 24))
        screen.handle_event(_up(x, y - 24))
        assert changes and changes[-1][0] == "p4"
