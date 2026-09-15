"""The app's callbacks must reach attributes that actually exist on its screens.

This is the gap that let a crash ship: every component and every screen had
tests, and the *wiring between them* had none. `_on_bypass_effect` reached for
`EffectsScreen.rack_list` after that component was replaced by `.chain`, so
tapping bypass raised AttributeError on a board while 402 tests passed.

These construct real screens and drive the real handlers with stub engines, so a
renamed attribute fails here instead of on the instrument.
"""

import pygame
import pytest

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.clients.lv2 import ControlPort
from synth_ui.ui.app import SynthUI
from synth_ui.ui.screens.effects import EffectsScreen


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


class StubEngine:
    """Only what the handlers under test call."""

    def __init__(self, effects):
        self._effects = effects
        self.bypassed: list[tuple[int, bool]] = []
        self.moved: list[tuple[int, int]] = []
        self.params: list[tuple[int, str, str]] = []

    def effects(self):
        return list(self._effects)

    def set_effect_bypass(self, instance, bypassed):
        self.bypassed.append((instance, bypassed))
        for effect in self._effects:
            if effect.instance == instance:
                effect.bypassed = bypassed
        return True

    def move_effect(self, source, target):
        self.moved.append((source, target))
        return True

    def effect_controls(self, uri):
        return [ControlPort("gain", "Gain", 0.0, 1.0, 0.5)]

    def set_effect_param(self, instance, symbol, value):
        self.params.append((instance, symbol, value))
        return True


CATALOG = [EffectCatalogEntry("Reverb", "urn:rev", "Reverb")]


def _ui(effects):
    ui = SynthUI.__new__(SynthUI)
    ui._engine = StubEngine(effects)
    ui._catalog = CATALOG
    ui._insert_at = None
    ui._effects_screen = EffectsScreen(
        effects=effects,
        catalog=CATALOG,
        on_remove=lambda i: None,
        on_add=lambda i=None: None,
        on_back=lambda: None,
        source_name="Rhodes EP",
    )
    return ui


def test_bypass_reaches_the_screen_it_updates():
    """The exact crash: the handler wrote to an attribute that no longer
    existed."""
    effects = [Effect(10, "urn:rev")]
    ui = _ui(effects)
    ui._on_bypass_effect(10, True)
    assert ui._engine.bypassed == [(10, True)]
    assert ui._effects_screen.chain.effects[0].bypassed is True


def test_bypass_with_no_effects_screen_does_not_raise():
    ui = _ui([Effect(10, "urn:rev")])
    ui._effects_screen = None
    ui._on_bypass_effect(10, True)


def test_the_screen_redraws_after_a_bypass():
    effects = [Effect(10, "urn:rev")]
    ui = _ui(effects)
    ui._on_bypass_effect(10, True)
    ui._effects_screen.draw(pygame.Surface((800, 480)))


def test_params_screen_builds_for_a_loaded_effect():
    ui = _ui([Effect(10, "urn:rev")])
    ui._show_effect_params_screen(10)
    assert ui._params_screen is not None
    ui._params_screen.draw(pygame.Surface((800, 480)))


def test_params_screen_ignores_an_instance_that_is_gone():
    """Removing an effect while its params screen is opening must not raise."""
    ui = _ui([Effect(10, "urn:rev")])
    ui._params_screen = None
    ui._show_effect_params_screen(999)
    assert ui._params_screen is None


def test_reordering_effects_reaches_the_engine():
    ui = _ui([Effect(10, "urn:rev"), Effect(11, "urn:rev")])
    ui._show_effects_screen = lambda: None       # avoids needing the rig library
    ui._on_reorder_effects(0, 1)
    assert ui._engine.moved == [(0, 1)]


def test_every_attribute_the_app_touches_on_effects_screen_exists():
    """A cheap backstop for the whole class of rename bug: the screen is built
    the way the app builds it, and the names the app uses are asserted."""
    screen = _ui([Effect(10, "urn:rev")])._effects_screen
    for attribute in ("chain", "trim_slider", "header"):
        assert hasattr(screen, attribute), attribute


class StubRigs:
    def __init__(self, rigs):
        self.rigs = rigs

    def replace(self, rig):
        pass


class StubHome:
    def __init__(self, rig):
        self.active_rig = rig
        self.refreshed = 0

    def refresh(self, rigs):
        self.refreshed += 1


def test_swapping_an_instrument_keeps_the_effects_chain():
    """The chain is the point of a rig: changing the instrument must change the
    first block and nothing else."""
    from synth_ui.clients.rig import Rig, RigEffect
    from synth_ui.clients.voice import Voice

    rig = Rig("Lead", "Rhodes EP", [RigEffect("urn:rev"), RigEffect("urn:dly")])
    ui = _ui([Effect(10, "urn:rev")])
    ui._rigs = StubRigs([rig])
    ui._home = StubHome(rig)
    ui._show_effects_screen = lambda: None
    loaded = []
    ui._engine.load_voice = lambda v: loaded.append(v.name) or True

    ui._swap_instrument(Voice("Hammond B3", "modhost", "", "Organ"))

    assert rig.voice == "Hammond B3"
    assert [e.uri for e in rig.effects] == ["urn:rev", "urn:dly"]
    assert loaded == ["Hammond B3"]


def test_swapping_with_no_active_rig_does_nothing():
    from synth_ui.clients.voice import Voice

    ui = _ui([])
    ui._home = StubHome(None)
    ui._swap_instrument(Voice("X", "modhost", "", "Piano"))
