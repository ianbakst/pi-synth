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
from synth_ui.clients.set import SongSet
from synth_ui.ui.app import SynthUI
from synth_ui.ui.screens.effects import EffectsScreen
from synth_ui.ui.screens.rigs import RigsScreen


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
        # What the loaded instrument is set to, and every control written to it.
        self.live_voice_params: dict[str, float] = {}
        self.voice_writes: list[tuple[str, str]] = []
        # Voices the app asked to pre-load when a set was entered.
        self.warmed: list[list[str]] = []

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

    # --- the instrument's own controls ---

    def voice_controls(self, voice):
        return [
            ControlPort("cutoff", "Cutoff", 0.0, 1.0, 0.9),
            ControlPort("adsr_a", "Attack", 1.0, 20000.0, 1.0),
        ]

    def voice_params(self):
        return dict(self.live_voice_params)

    def set_voice_param(self, symbol, value):
        self.voice_writes.append((symbol, value))
        self.live_voice_params[symbol] = float(value)
        return True

    # --- enough for the app to build its own effects screen ---

    def fixed_velocity_available(self):
        return False

    def set_rig_trim(self, db):
        pass

    def warm(self, voices):
        self.warmed.append([v.name for v in voices])
        return len(voices)


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
    for attribute in ("chain", "trim_slider", "header", "fixed_velocity"):
        assert hasattr(screen, attribute), attribute


class StubSets:
    """Stands in for SetLibrary: the app only saves through it here."""

    def __init__(self, sets):
        self.sets = sets
        self.saves = 0

    def save(self):
        self.saves += 1
        return True


class StubHome:
    """Stands in for SetsScreen — home is the set list now."""

    def __init__(self):
        self.refreshed = 0
        self.active = None

    def refresh(self, sets):
        self.refreshed += 1

    def set_active(self, song_set):
        self.active = song_set


def _rig_screen(rigs, active=None, unavailable=None):
    """A real RigsScreen, so the app's calls onto it are the real ones."""
    screen = RigsScreen(
        rigs=rigs,
        on_load_rig=lambda r: True,
        on_edit_rig=lambda r: None,
        on_new=lambda: None,
        on_edit=lambda: None,
        on_back=lambda: None,
        on_gain_change=lambda g: None,
        unavailable=unavailable,
    )
    screen._active_rig = active
    return screen


def _set_ui(ui, rig, voice):
    """Wire a UI up as though `rig` is the active rig of the active set."""
    song_set = SongSet("Song", rigs=[rig])
    ui._sets = StubSets([song_set])
    ui._active_set = song_set
    ui._home = StubHome()
    ui._rig_screen = _rig_screen(song_set.rigs, active=rig)
    ui._library = {voice.name: voice}
    return ui


def test_swapping_an_instrument_keeps_the_effects_chain():
    """The chain is the point of a rig: changing the instrument must change the
    first block and nothing else."""
    from synth_ui.clients.rig import Rig, RigEffect
    from synth_ui.clients.voice import Voice

    rig = Rig("Lead", "Rhodes EP", effects=[RigEffect("urn:rev"), RigEffect("urn:dly")])
    ui = _ui([Effect(10, "urn:rev")])
    _set_ui(ui, rig, _voice())
    ui._show_effects_screen = lambda: None
    loaded = []
    ui._engine.load_voice = lambda v: loaded.append(v.name) or True

    ui._swap_instrument(Voice("Hammond B3", "modhost", "", "Organ"))

    assert rig.voice == "Hammond B3"
    assert [e.uri for e in rig.effects] == ["urn:rev", "urn:dly"]
    assert loaded == ["Hammond B3"]


# --- the instrument is a block you can edit ---------------------------------

def _voice(name="Calf Wavetable", params=None):
    from synth_ui.clients.voice import Voice

    return Voice(
        name, "modhost", "", "Synth", uri="urn:wt", params=params or {"cutoff": 0.4}
    )


def _rig_ui(rig, voice=None):
    """A UI whose effects screen was built by the app itself, so the callbacks
    under test are the ones the app actually wires up."""
    voice = voice or _voice()
    ui = _ui([Effect(10, "urn:rev")])
    _set_ui(ui, rig, voice)
    ui._show_effects_screen()
    return ui


def test_tapping_the_instrument_opens_its_parameters():
    from synth_ui.clients.rig import Rig

    rig = Rig("Vox Pad", "Calf Wavetable")
    ui = _rig_ui(rig)
    # Exactly what the chain component calls on a tap of the first block.
    ui._effects_screen.chain.on_edit_instrument()
    assert ui._params_screen is not None
    assert ui._params_screen.header.name == "Calf Wavetable"
    ui._params_screen.draw(pygame.Surface((800, 480)))


def test_the_instrument_screen_offers_the_swap_that_left_the_block():
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Vox Pad", "Calf Wavetable"))
    ui._show_voice_params_screen()
    assert "Change" in [label for label, _cb in ui._params_screen.header.actions]


def test_editing_a_control_reaches_the_live_instrument():
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Vox Pad", "Calf Wavetable"))
    ui._show_voice_params_screen()
    ui._params_screen.sliders.sliders[0].on_change(0.25)
    assert ui._engine.voice_writes == [("cutoff", "0.25")]


def test_the_patch_is_saved_into_the_rig_not_the_catalog():
    from synth_ui.clients.rig import Rig

    voice = _voice(params={"cutoff": 0.4})
    rig = Rig("Vox Pad", "Calf Wavetable")
    ui = _rig_ui(rig, voice)
    ui._engine.live_voice_params = {"cutoff": 0.25, "adsr_a": 90.0}

    ui._sync_active_rig()

    # Only what differs from the catalog entry: the rig says what this sound
    # does to the instrument, not what the instrument is.
    assert rig.voice_params == {"cutoff": 0.25, "adsr_a": 90.0}
    assert voice.params == {"cutoff": 0.4}


def test_a_patch_matching_the_catalog_is_not_stored():
    from synth_ui.clients.rig import Rig

    rig = Rig("Plain", "Calf Wavetable")
    ui = _rig_ui(rig, _voice(params={"cutoff": 0.4}))
    ui._engine.live_voice_params = {"cutoff": 0.4}
    ui._sync_active_rig()
    assert rig.voice_params == {}


def test_reset_returns_the_instrument_to_its_catalog_entry():
    # Not the plugin's raw defaults: "Calf Wavetable" is the plugin *plus* the
    # values that make it that voice. Controls the catalog doesn't set do fall
    # back to the port default.
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Vox Pad", "Calf Wavetable"), _voice(params={"cutoff": 0.4}))
    ui._engine.live_voice_params = {"cutoff": 0.1, "adsr_a": 90.0}
    ui._reset_voice_params()
    assert ui._engine.voice_writes == [("cutoff", "0.4"), ("adsr_a", "1.0")]


def test_swapping_the_instrument_drops_a_patch_meant_for_the_old_one():
    from synth_ui.clients.rig import Rig
    from synth_ui.clients.voice import Voice

    rig = Rig("Vox Pad", "Calf Wavetable", voice_params={"cutoff": 0.2})
    ui = _rig_ui(rig)
    ui._show_effects_screen = lambda: None
    ui._engine.load_voice = lambda v: True

    ui._swap_instrument(Voice("Hammond B3", "modhost", "", "Organ"))
    assert rig.voice_params == {}


def test_the_instrument_screen_needs_a_voice_in_the_catalog():
    """A rig naming a voice this unit doesn't have must not raise on a tap."""
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Gone", "Removed Voice"))
    ui._params_screen = None
    ui._show_voice_params_screen()
    assert ui._params_screen is None


def test_swapping_with_no_active_rig_does_nothing():
    from synth_ui.clients.voice import Voice

    ui = _ui([])
    ui._rig_screen = _rig_screen([], active=None)
    ui._swap_instrument(Voice("X", "modhost", "", "Piano"))


# --- fixed velocity toggle --------------------------------------------------

def _velocity_screen(initial=False, handler=None):
    return EffectsScreen(
        effects=[],
        catalog=CATALOG,
        on_remove=lambda i: None,
        on_add=lambda i=None: None,
        on_back=lambda: None,
        on_fixed_velocity=handler or (lambda enabled: None),
        initial_fixed_velocity=initial,
        source_name="Rhodes EP",
    )


def test_velocity_button_reads_out_its_state():
    screen = _velocity_screen()
    labels = [label for label, _cb in screen.header.actions]
    assert "Vel: Played" in labels
    screen._toggle_fixed_velocity()
    labels = [label for label, _cb in screen.header.actions]
    assert "Vel: Fixed" in labels and "Vel: Played" not in labels


def test_velocity_button_keeps_add_rightmost():
    # Header actions render right-to-left, first in the list is rightmost. The
    # button that was always under your thumb should stay there.
    screen = _velocity_screen()
    assert screen.header.actions[0][0] == "Add"


def test_toggling_reaches_the_engine():
    seen: list[bool] = []
    screen = _velocity_screen(handler=seen.append)
    screen._toggle_fixed_velocity()
    screen._toggle_fixed_velocity()
    assert seen == [True, False]


def test_the_button_is_absent_when_the_board_cannot_do_it():
    # app.py passes on_fixed_velocity=None when x42-plugins isn't installed;
    # a button that silently does nothing is worse than no button.
    screen = EffectsScreen(
        effects=[],
        catalog=CATALOG,
        on_remove=lambda i: None,
        on_add=lambda i=None: None,
        on_back=lambda: None,
        source_name="Rhodes EP",
    )
    assert [label for label, _cb in screen.header.actions] == ["Add"]
    # Still readable by _sync_active_rig, which doesn't know or care.
    assert screen.fixed_velocity is False


def test_an_enabled_rig_opens_the_editor_showing_fixed():
    screen = _velocity_screen(initial=True)
    assert screen.fixed_velocity is True
    assert [label for label, _cb in screen.header.actions][1] == "Vel: Fixed"


# --- sets: entering one is what scopes everything else ----------------------

def _sets_ui(*specs, active=None):
    """A UI holding real sets. `specs` are (set name, [rig names]); `active` is
    the set name that is already playing, if any."""
    from synth_ui.clients.rig import Rig

    ui = _ui([])
    sets = [
        SongSet(name, rigs=[Rig(r, "Calf Wavetable") for r in rigs])
        for name, rigs in specs
    ]
    ui._sets = StubSets(sets)
    ui._home = StubHome()
    ui._library = {"Calf Wavetable": _voice()}
    # The real availability check, so a rig whose voice is gone is skipped here
    # exactly as it would be on the board.
    ui._rig_screen = _rig_screen([], unavailable=ui._rig_unavailable)
    ui._in_background = lambda target: target()      # run in order, not racing
    ui._active_set = None
    if active is not None:
        chosen = next(s for s in sets if s.name == active)
        ui._activate_set(chosen)
        ui._rig_screen._active_rig = chosen.rigs[0] if chosen.rigs else None
    return ui, sets


def test_entering_a_new_set_makes_it_active_and_starts_its_first_rig():
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]))
    ui._enter_set(sets[0])
    assert ui._active_set is sets[0]
    assert ui._home.active is sets[0]
    assert ui._rig_screen.active_rig.name == "Pad"
    assert ui.screen is ui._rig_screen


def test_entering_a_new_set_shows_only_its_rigs():
    """The whole point: four rigs for a song means four pads, and the steppers
    and the footswitch walk those four."""
    ui, sets = _sets_ui(
        ("Levitating", ["Pad", "Strings"]), ("Other", ["A", "B", "C"])
    )
    ui._enter_set(sets[0])
    assert [t.title for t in ui._rig_screen.grid.tiles] == ["Pad", "Strings"]


def test_re_entering_the_active_set_changes_nothing():
    """Coming back from editing a chain must not yank you off the rig you were
    on and back to the top of the set."""
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]), active="Levitating")
    ui._rig_screen._active_rig = sets[0].rigs[1]          # you moved to Strings

    ui._enter_set(sets[0])

    assert ui._rig_screen.active_rig.name == "Strings"


def test_entering_a_set_skips_a_first_rig_that_cannot_play():
    # Landing on a rig whose voice is missing would leave silence and no clue.
    ui, sets = _sets_ui(("Levitating", ["Broken", "Pad"]))
    sets[0].rigs[0].voice = "Gone From The Catalog"
    ui._enter_set(sets[0])
    assert ui._rig_screen.active_rig.name == "Pad"


def test_an_empty_set_becomes_active_without_stopping_the_sound():
    ui, sets = _sets_ui(("Levitating", ["Pad"]), ("Empty", []), active="Levitating")
    playing = ui._rig_screen.active_rig

    ui._enter_set(sets[1])

    assert ui._active_set is sets[1]
    assert ui._rig_screen.active_rig is playing


def test_program_change_indexes_the_active_set():
    """A song's four rigs are PC 0-3 — which is what makes Program Change worth
    having at all."""
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]), active="Levitating")
    ui._select_rig_by_index(1)
    assert ui._rig_screen.active_rig.name == "Strings"


def test_program_change_past_the_end_of_the_set_is_ignored():
    ui, sets = _sets_ui(("Levitating", ["Pad"]), active="Levitating")
    ui._select_rig_by_index(9)
    assert ui._rig_screen.active_rig.name == "Pad"


def test_deleting_a_rig_removes_it_from_its_set():
    # A rig lives in exactly one set, so removing it there is deleting it.
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]), active="Levitating")
    doomed = sets[0].rigs[0]
    ui._on_remove_rig(doomed)
    assert [r.name for r in sets[0].rigs] == ["Strings"]
    assert ui._rig_screen.active_rig is None       # it was the one playing
    assert ui._sets.saves >= 1


def test_deleting_another_rig_leaves_the_active_one_alone():
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]), active="Levitating")
    ui._on_remove_rig(sets[0].rigs[1])
    assert ui._rig_screen.active_rig.name == "Pad"


def test_deleting_the_playing_set_leaves_no_active_set():
    # Jumping into another song's set would be worse than having none.
    ui, sets = _sets_ui(("Levitating", ["Pad"]), ("Other", ["X"]), active="Levitating")
    ui._sets.remove = lambda set_id: ui._sets.sets.remove(sets[0])

    ui._on_remove_set(sets[0])

    assert ui._active_set is None
    assert ui._rig_screen.grid.tiles == []


# --- where you were -----------------------------------------------------

def test_state_round_trips_set_and_rig(tmp_path, monkeypatch):
    from synth_ui.ui import app as app_module

    path = str(tmp_path / "state")
    monkeypatch.setattr(app_module, "STATE_FILE", path)
    app_module._save_state("set-id", "rig-id")
    assert app_module._load_state() == ("set-id", "rig-id")


def test_the_pre_sets_state_file_reads_as_a_rig_name(tmp_path, monkeypatch):
    """One line was the active rig's *name*, before sets existed. It has to
    resolve, or an upgrade dumps you at the top of the list."""
    from synth_ui.ui import app as app_module

    path = tmp_path / "state"
    path.write_text("Saw Wave\n")
    monkeypatch.setattr(app_module, "STATE_FILE", str(path))
    assert app_module._load_state() == ("", "Saw Wave")


def test_missing_state_is_not_an_error(tmp_path, monkeypatch):
    from synth_ui.ui import app as app_module

    monkeypatch.setattr(app_module, "STATE_FILE", str(tmp_path / "nope"))
    assert app_module._load_state() == ("", "")


# --- warming: why switching inside a song is immediate ----------------------

def test_entering_a_set_warms_its_voices():
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]))
    ui._enter_set(sets[0])
    assert ui._engine.warmed == [["Calf Wavetable", "Calf Wavetable"]]


def test_warming_happens_after_the_first_rig_is_playing():
    """Sound first, readiness second — and it leaves the rig you're about to
    play as the most recently used, so it can't be the one evicted."""
    order = []
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]))
    ui._rig_screen.load_first = lambda: order.append("load") or None
    ui._engine.warm = lambda voices: order.append("warm") or len(voices)

    ui._enter_set(sets[0])

    assert order == ["load", "warm"]


def test_re_entering_the_active_set_does_not_warm_again():
    ui, sets = _sets_ui(("Levitating", ["Pad"]), active="Levitating")
    ui._enter_set(sets[0])
    assert ui._engine.warmed == []


def test_warming_skips_a_rig_whose_voice_is_gone():
    ui, sets = _sets_ui(("Levitating", ["Pad", "Broken"]))
    sets[0].rigs[1].voice = "Gone From The Catalog"
    ui._enter_set(sets[0])
    assert ui._engine.warmed == [["Calf Wavetable"]]


def test_warming_carries_each_rig_s_own_patch():
    """Warmed with the values the rig will ask for, so selecting it finds the
    plugin loaded *and* set up, and the reconcile has nothing left to write."""
    sent = []
    ui, sets = _sets_ui(("Levitating", ["Pad", "Strings"]))
    sets[0].rigs[0].voice_params = {"cutoff": 0.2}
    sets[0].rigs[1].voice_params = {"cutoff": 0.8}
    ui._engine.warm = lambda voices: sent.extend(v.params["cutoff"] for v in voices)

    ui._enter_set(sets[0])

    assert sent == [0.2, 0.8]


def test_the_set_booted_into_is_warmed_too(tmp_path, monkeypatch):
    """Otherwise the first song of the night is the one set that isn't ready."""
    from synth_ui.clients.rig import Rig
    from synth_ui.ui import app as app_module

    monkeypatch.setattr(app_module, "STATE_FILE", str(tmp_path / "state"))
    ui = _ui([])
    song_set = SongSet("Levitating", rigs=[Rig("Pad", "Calf Wavetable")])
    ui._sets = StubSets([song_set])
    ui._home = StubHome()
    ui._library = {"Calf Wavetable": _voice()}
    ui._rig_screen = _rig_screen([], unavailable=ui._rig_unavailable)
    ui._in_background = lambda target: target()
    ui._active_set = None
    ui._sets.get = lambda set_id: None
    ui._sets.find_rig = lambda rig_id: None

    ui._restore_state()

    assert ui._active_set is song_set
    assert ui._rig_screen.active_rig.name == "Pad"
    assert ui._engine.warmed == [["Calf Wavetable"]]


# --- the cog reaches settings from every screen that has one ----------------

def test_the_cog_opens_settings_from_the_chain_screen():
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Pad", "Calf Wavetable"))
    ui._backlight = type("B", (), {"available": False})()
    ui._engine.midi_inputs = lambda: []
    ui._engine.reattach_midi = lambda: True

    ui._effects_screen.header.on_settings()

    assert ui._settings_screen is not None
    ui._settings_screen.draw(pygame.Surface((800, 480)))


def test_the_screens_the_app_builds_carry_the_cog():
    """The chain and the params screens are built by app methods, so this is
    the app's own wiring rather than the fixture's."""
    from synth_ui.clients.rig import Rig

    ui = _rig_ui(Rig("Pad", "Calf Wavetable"))
    assert ui._effects_screen.header.on_settings is not None

    ui._show_voice_params_screen()
    assert ui._params_screen.header.on_settings is not None

    ui._show_effect_params_screen(10)
    assert ui._params_screen.header.on_settings is not None
