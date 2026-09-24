"""Tests for EQ layouts, their computed response, and the graph that drags them."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from synth_ui.clients.controls import controls_for  # noqa: E402
from synth_ui.clients.eq import (  # noqa: E402
    DB_RANGE,
    EQ_LAYOUTS,
    HIGHPASS,
    LOWPASS,
    PEAK,
    eq_layout_for,
    log_freqs,
)
from synth_ui.clients.lv2 import ControlPort  # noqa: E402
from synth_ui.ui.event import UIEvent  # noqa: E402
from synth_ui.ui.screens.params import ParamsScreen  # noqa: E402

CALF_EQ5 = "http://calf.sourceforge.net/plugins/Equalizer5Band"
FIL4 = "http://gareus.org/oss/lv2/fil4#stereo"

# Input control symbols as `lv2info` lists them on the board (2026-09-24).
# A layout naming a symbol not here would drive a control the plugin doesn't
# have — accepted by mod-host, and silently ignored.
BOARD_SYMBOLS = {
    CALF_EQ5: {
        "level_in", "level_out", "individuals", "zoom",
        *(f"{band}_{field}"
          for band in ("ls", "hs", "p1", "p2", "p3")
          for field in ("active", "level", "freq", "q")),
    },
    FIL4: {
        "enable", "gain", "peakreset", "HighPass", "HPfreq", "HPQ", "LowPass",
        "LPfreq", "LPQ", "LSsec", "LSfreq", "LSq", "LSgain", "HSsec", "HSfreq",
        "HSq", "HSgain",
        *(f"{f}{i}" for i in range(1, 5) for f in ("sec", "freq", "q", "gain")),
    },
}


def _values(layout, **overrides):
    """Every band off, at a middling setting, then whatever the test says."""
    values = {}
    for band in layout.bands:
        values[band.enable] = 0.0
        values[band.freq] = 1000.0
        if band.gain:
            values[band.gain] = layout.gain_value(0.0)
        if band.width:
            values[band.width] = 1.0
    values.update(overrides)
    return values


class TestLayouts:
    @pytest.mark.parametrize("uri", [CALF_EQ5, FIL4])
    def test_every_symbol_exists_on_the_plugin(self, uri):
        layout = EQ_LAYOUTS[uri]
        assert layout.band_symbols <= BOARD_SYMBOLS[uri]
        assert set(layout.hidden) <= BOARD_SYMBOLS[uri]

    def test_a_plugin_missing_a_band_control_gets_no_graph(self):
        assert eq_layout_for(CALF_EQ5, BOARD_SYMBOLS[CALF_EQ5] - {"p2_q"}) is None

    def test_an_unknown_plugin_gets_no_graph(self):
        assert eq_layout_for("urn:reverb") is None

    def test_mono_and_stereo_fil4_share_a_layout(self):
        assert eq_layout_for(FIL4) is eq_layout_for(FIL4.replace("stereo", "mono"))


class TestUnits:
    def test_calf_levels_are_linear(self):
        layout = EQ_LAYOUTS[CALF_EQ5]
        band = layout.bands[1]
        assert layout.gain_db(band, {band.gain: 2.0}) == pytest.approx(6.02, abs=0.01)
        assert layout.gain_value(-6.0206) == pytest.approx(0.5, abs=1e-4)

    def test_fil4_gains_are_db(self):
        layout = EQ_LAYOUTS[FIL4]
        assert layout.gain_value(-4.0) == -4.0


class TestResponse:
    @pytest.mark.parametrize("uri,peak", [(CALF_EQ5, 1), (FIL4, 2)])
    def test_a_peak_reaches_its_gain_at_its_frequency(self, uri, peak):
        layout = EQ_LAYOUTS[uri]
        band = layout.bands[peak]
        assert band.kind == PEAK
        values = _values(layout, **{
            band.enable: 1.0, band.freq: 1000.0, band.gain: layout.gain_value(6.0),
        })
        (db,) = layout.response(values, [1000.0])
        assert db == pytest.approx(6.0, abs=0.01)

    def test_a_band_that_is_off_contributes_nothing(self):
        layout = EQ_LAYOUTS[FIL4]
        band = layout.bands[2]
        values = _values(layout, **{band.gain: 12.0})
        assert layout.response(values, [1000.0]) == [0.0]

    def test_calf_off_is_zero_and_left_right_mid_side_are_on(self):
        layout = EQ_LAYOUTS[CALF_EQ5]
        band = layout.bands[1]
        for setting, heard in ((0.0, False), (1.0, True), (4.0, True)):
            values = _values(layout, **{
                band.enable: setting, band.gain: layout.gain_value(6.0),
            })
            assert (layout.response(values, [1000.0])[0] > 1) is heard

    def test_bands_in_series_add(self):
        layout = EQ_LAYOUTS[FIL4]
        a, b = layout.bands[2], layout.bands[3]
        values = _values(layout, **{
            a.enable: 1.0, a.gain: 3.0, b.enable: 1.0, b.gain: 3.0,
        })
        (db,) = layout.response(values, [1000.0])
        assert db == pytest.approx(6.0, abs=0.01)

    def test_fil4_highpass_matches_its_documented_corner(self):
        """fil4's source: resonance 0 is -6 dB at the corner."""
        layout = EQ_LAYOUTS[FIL4]
        hp = layout.bands[0]
        assert hp.kind == HIGHPASS and not hp.has_gain
        values = _values(layout, **{hp.enable: 1.0, hp.freq: 100.0, hp.width: 0.0})
        at, below, above = layout.response(values, [100.0, 25.0, 1000.0])
        assert at == pytest.approx(-6.0, abs=0.1)
        assert below < -20 and abs(above) < 0.2

    def test_fil4_lowpass_cuts_above(self):
        layout = EQ_LAYOUTS[FIL4]
        lp = layout.bands[-1]
        assert lp.kind == LOWPASS
        values = _values(layout, **{lp.enable: 1.0, lp.freq: 5000.0, lp.width: 0.0})
        below, above = layout.response(values, [500.0, 20000.0])
        assert abs(below) < 0.2 and above < -20

    def test_log_freqs_span_the_axis(self):
        freqs = log_freqs(5, 20.0, 20000.0)
        assert freqs[0] == pytest.approx(20.0)
        assert freqs[-1] == pytest.approx(20000.0)
        assert freqs[2] == pytest.approx(632.46, abs=0.01)   # the geometric middle


# --- the graph ---------------------------------------------------------------

def _ports(uri):
    """ControlPorts for a layout's plugin: ranges from the board, trimmed."""
    layout = EQ_LAYOUTS[uri]
    ports = [ControlPort("level_in", "Input", 0.015625, 64.0, 1.0)]
    # An octave apart, as a plugin's own defaults spread its bands; stacked on
    # one frequency, only the selected handle could ever be grabbed.
    for i, band in enumerate(layout.bands):
        ports.append(ControlPort(band.enable, "On", 0.0, 1.0, 0.0, toggled=True))
        ports.append(ControlPort(band.freq, "Freq", 20.0, 20000.0, 60.0 * 2 ** i,
                                 logarithmic=True))
        if band.gain:
            low, high = ((0.015625, 64.0) if layout.gain_unit == "linear"
                         else (-18.0, 18.0))
            ports.append(ControlPort(band.gain, "Gain", low, high,
                                     layout.gain_value(0.0)))
        if band.width:
            ports.append(ControlPort(band.width, "Q", 0.1, 4.0, 1.0))
    ports += [ControlPort(s, s, 0.0, 1.0, 0.0) for s in layout.hidden]
    return ports


@pytest.fixture
def screen():
    pygame.init()
    changes = []
    s = ParamsScreen(
        "EQ", _ports(FIL4), {}, lambda sym, v: changes.append((sym, v)),
        on_back=lambda: None, eq=EQ_LAYOUTS[FIL4],
    )
    s.draw(pygame.Surface((800, 480)))
    s.changes = changes
    return s


def _drag(target, start, end):
    target.handle_event(UIEvent(pygame.FINGERDOWN, pos=start))
    target.handle_event(UIEvent(pygame.FINGERMOTION, pos=end))
    target.handle_event(UIEvent(pygame.FINGERUP, pos=end))


class TestPanel:
    def test_the_eq_is_the_first_page(self, screen):
        assert screen.controls.on_eq_page
        assert screen.controls.pages == 2

    def test_the_grid_holds_only_what_is_not_a_band(self, screen):
        """And not the hidden meter reset, which does nothing to the sound."""
        symbols = [w.control.symbol for w in screen.controls.widgets]
        assert symbols == ["level_in"]

    def test_the_selected_bands_controls_sit_beneath(self, screen):
        page = screen.controls.eq_page
        band = page.layout.bands[page.graph.selected]
        assert [w.control.symbol for w in page.band_widgets] == list(band.symbols)


class TestDragging:
    def test_a_handle_moves_its_band_in_frequency_and_gain(self, screen):
        graph = screen.controls.eq_page.graph
        band = graph.layout.bands[2]                         # peak 1
        start = graph.handle_pos(band)
        end = (round(graph.x_for(2000.0)), round(graph.y_for(6.0)))
        _drag(screen, start, end)
        assert graph.selected == 2
        assert graph.values[band.freq] == pytest.approx(2000.0, rel=0.02)
        assert graph.values[band.gain] == pytest.approx(6.0, abs=0.2)
        sent = dict(screen.changes)
        assert band.freq in sent and band.gain in sent

    def test_dragging_a_band_that_is_off_turns_it_on(self, screen):
        graph = screen.controls.eq_page.graph
        band = graph.layout.bands[2]
        start = graph.handle_pos(band)
        _drag(screen, start, (start[0] + 30, start[1]))
        assert (band.enable, 1.0) in screen.changes

    def test_a_highpass_only_moves_across(self, screen):
        graph = screen.controls.eq_page.graph
        hp = graph.layout.bands[0]
        graph.values[hp.freq] = 100.0                        # clear of the others
        start = graph.handle_pos(hp)
        _drag(screen, start, (start[0] + 40, start[1] - 80))
        assert graph.values[hp.freq] > 100.0
        assert not any(sym == hp.width for sym, _ in screen.changes)

    def test_gain_stops_at_the_graphs_range(self, screen):
        graph = screen.controls.eq_page.graph
        band = graph.layout.bands[2]
        start = graph.handle_pos(band)
        _drag(screen, start, (start[0], -1000))
        assert graph.values[band.gain] == pytest.approx(DB_RANGE)

    def test_a_touch_away_from_every_handle_is_not_a_drag(self, screen):
        graph = screen.controls.eq_page.graph
        corner = (graph.plot.x + 2, graph.plot.bottom - 2)
        _drag(screen, corner, (corner[0] + 50, corner[1] - 50))
        assert screen.changes == []

    def test_a_knob_beneath_moves_the_curve(self, screen):
        page = screen.controls.eq_page
        graph = page.graph
        before = list(graph._curve)
        band = graph.layout.bands[graph.selected]
        # The selected band's on/off, then its frequency knob.
        page.band_widgets[0].on_change(1.0)
        page.band_widgets[1].on_change(300.0)
        screen.draw(pygame.Surface((800, 480)))
        assert graph.values[band.freq] == 300.0
        assert graph._curve != before

    def test_selecting_another_band_rebuilds_the_row(self, screen):
        page = screen.controls.eq_page
        lp = page.layout.bands[-1]
        pos = page.graph.handle_pos(lp)
        _drag(screen, pos, pos)
        assert page.band_widgets[1].control.symbol == lp.freq

    def test_every_band_selected_draws(self, screen):
        page = screen.controls.eq_page
        for i in range(len(page.layout.bands)):
            page.graph.select(i)
            screen.draw(pygame.Surface((800, 480)))


def test_the_calf_eq_hides_its_gui_only_controls():
    pygame.init()
    ports = _ports(CALF_EQ5)
    assert {c.symbol for c in controls_for(ports)} >= {"individuals", "zoom"}
    s = ParamsScreen("EQ", ports, {}, lambda *a: None, lambda: None,
                     eq=EQ_LAYOUTS[CALF_EQ5])
    assert [w.control.symbol for w in s.controls.widgets] == ["level_in"]
