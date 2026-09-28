"""The banner on the home screen: shown until tapped, and the tap that
dismisses it must not also land on the pad underneath."""

import pygame
import pytest

from synth_ui.clients.set import SongSet
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.sets import SetsScreen


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def home(entered):
    return SetsScreen(
        sets=[SongSet(f"Set {n}") for n in range(6)],
        on_enter_set=entered.append, on_edit_set=lambda s: None,
        on_new=lambda: None, on_settings=lambda: None, on_gain_change=lambda g: None,
    )


def test_a_tap_on_the_banner_dismisses_it_and_nothing_else():
    entered = []
    s = home(entered)
    s.notify("Sets file was damaged.")
    s.draw(pygame.display.get_surface())
    pos = (100, 470)
    for kind in (pygame.FINGERDOWN, pygame.FINGERUP):
        s.handle_event(UIEvent(kind, pos=pos))
    assert s.banner.text is None
    assert entered == []


def test_no_banner_means_touches_pass_through():
    s = home([])
    s.notify(None)
    assert s.banner.handle_event(UIEvent(pygame.FINGERUP, pos=(100, 470))) is False
