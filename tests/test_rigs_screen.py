"""RigsScreen holds the active rig — what is loaded and making sound.

The rest of the app reads it, so these pin down the one rule that is easy to get
wrong: showing a different set's rigs is navigation and must not stop the
instrument. Only a rig actually going away clears it.
"""

import pygame
import pytest

from synth_ui.clients.rig import Rig
from synth_ui.ui.screens.rigs import RigsScreen


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def screen(rigs, loads=None, unavailable=None):
    return RigsScreen(
        rigs=rigs,
        on_load_rig=(lambda r: (loads.append(r.name) if loads is not None else None)
                     or True),
        on_edit_rig=lambda r: None,
        on_new=lambda: None,
        on_edit=lambda: None,
        on_back=lambda: None,
        on_gain_change=lambda g: None,
        unavailable=unavailable,
    )


def rigs(*names):
    return [Rig(n, "Calf Wavetable") for n in names]


def test_selecting_a_rig_loads_it_and_marks_it_active():
    loaded = []
    songs = rigs("Pad", "Strings")
    s = screen(songs, loads=loaded)
    s.select(songs[1])
    assert s.active_rig is songs[1]
    assert loaded == ["Strings"]


def test_showing_another_set_does_not_stop_what_is_playing():
    """Browsing is not switching. The engine goes on playing until something is
    actually selected, so walking through the set list between songs is safe."""
    loaded = []
    first = rigs("Pad")
    s = screen(first, loads=loaded)
    s.select(first[0])
    loaded.clear()

    s.set_rigs(rigs("A", "B"), title="Another Song")

    assert s.active_rig is first[0]
    assert loaded == []
    assert s.header.name == "Another Song"


def test_the_active_rig_is_only_highlighted_in_its_own_set():
    first = rigs("Pad")
    s = screen(first)
    s.select(first[0])
    s.set_rigs(rigs("A", "B"))
    assert [t.active for t in s.grid.tiles] == [False, False]


def test_deleting_the_playing_rig_clears_it():
    songs = rigs("Pad", "Strings")
    s = screen(songs)
    s.select(songs[0])
    s.rig_removed(songs[0].id)
    assert s.active_rig is None


def test_deleting_another_rig_leaves_the_active_one_alone():
    songs = rigs("Pad", "Strings")
    s = screen(songs)
    s.select(songs[0])
    s.rig_removed(songs[1].id)
    assert s.active_rig is songs[0]


def test_load_first_skips_rigs_that_cannot_play():
    songs = rigs("Broken", "Pad")
    s = screen(songs, unavailable=lambda r: "voice missing" if r.name == "Broken"
               else "")
    assert s.load_first().name == "Pad"


def test_load_first_on_an_unplayable_set_selects_nothing():
    songs = rigs("Broken")
    s = screen(songs, unavailable=lambda r: "voice missing")
    assert s.load_first() is None
    assert s.active_rig is None


class TestStepping:
    """What the steppers and a footswitch walk — scoped to the shown set."""

    def test_step_moves_through_the_set(self):
        songs = rigs("A", "B", "C")
        s = screen(songs)
        s.select(songs[0])
        s.select_next()
        assert s.active_rig.name == "B"
        s.select_previous()
        assert s.active_rig.name == "A"

    def test_step_wraps(self):
        songs = rigs("A", "B")
        s = screen(songs)
        s.select(songs[1])
        s.select_next()
        assert s.active_rig.name == "A"

    def test_step_skips_a_rig_that_cannot_load(self):
        """A footswitch landing on a broken rig leaves silence and no way to
        tell why."""
        songs = rigs("A", "Broken", "C")
        s = screen(songs, unavailable=lambda r: "gone" if r.name == "Broken" else "")
        s.select(songs[0])
        s.select_next()
        assert s.active_rig.name == "C"

    def test_stepping_after_browsing_elsewhere_enters_this_set(self):
        """The active rig belongs to another set — the pedal should pick this
        set up rather than refuse to move."""
        other = rigs("Elsewhere")
        s = screen(other)
        s.select(other[0])
        s.set_rigs(rigs("A", "B"))
        s.select_next()
        assert s.active_rig.name == "A"

    def test_stepping_an_empty_set_does_nothing(self):
        s = screen([])
        s.select_next()
        assert s.active_rig is None
