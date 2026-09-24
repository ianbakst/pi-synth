"""The settings screen, and the cog that reaches it.

The screen exists to answer "why are the keys dead" and "where is this box on
the network" without a console, so these assert what it reports rather than how
it looks — plus the one destructive control on it, which must not fire on a
single mis-aimed tap.
"""

import pygame
import pytest

from synth_ui.clients.network import Interface
from synth_ui.ui.components.header import Header
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.settings import POLL_MS, SettingsScreen

ROLAND = "a2j:Roland Digital Piano [24] (capture): Roland Digital Piano MIDI 1"


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def screen(midi=None, nets=None, reconnect=None, shutdown=None, brightness=True,
           wifi=True, set_wifi=None, restart=None):
    return SettingsScreen(
        on_back=lambda: None,
        midi_inputs=lambda: list(midi or []),
        on_reconnect_midi=reconnect or (lambda: True),
        interfaces=lambda: (None if nets is None else list(nets)),
        wifi_enabled=wifi if callable(wifi) else (lambda: wifi),
        on_set_wifi=set_wifi or (lambda on: True),
        on_shutdown=shutdown or (lambda: None),
        on_restart=restart or (lambda: None),
        on_brightness=(lambda v: None) if brightness else None,
        initial_brightness=0.8 if brightness else None,
    )


def text(section):
    return " | ".join(line for line, _color in section.lines)


def tap(component, rect):
    component.handle_event(UIEvent(pygame.FINGERUP, pos=rect.center))


def labels(section):
    return [label for _rect, label, _cb in section.button_rects()]


def press(section, prefix):
    """Tap the button whose label starts with `prefix`."""
    for rect, label, _cb in section.button_rects():
        if label.startswith(prefix):
            tap(section, rect)
            return label
    raise AssertionError(f"no button starting {prefix!r} in {labels(section)}")


# --- MIDI ---

def test_a_connected_keyboard_is_named():
    s = screen(midi=[ROLAND])
    assert "Roland Digital Piano" in text(s.midi)


def test_the_jack_port_boilerplate_is_stripped():
    """The full name is 70 characters of mostly nothing on an 800px panel."""
    s = screen(midi=[ROLAND])
    assert "a2j:" not in text(s.midi) and "[24]" not in text(s.midi)


def test_no_keyboard_says_so_and_names_the_usual_cause():
    s = screen(midi=[])
    assert "No keyboard connected" in text(s.midi)
    assert "Auto Off" in text(s.midi)


def test_the_alsa_loopback_is_not_reported_as_a_keyboard():
    """`Midi Through` is always present and carries nothing. Listing it would
    answer "is my piano connected" with yes, every time."""
    s = screen(midi=["a2j:Midi Through [14] (capture): Midi Through Port-0"])
    assert "Midi Through" not in text(s.midi)
    assert "No keyboard connected" in text(s.midi)


def test_the_din_jack_is_shown_but_is_not_a_keyboard():
    """It exists whether or not a cable is in it, so on its own it still means
    no keyboard."""
    s = screen(midi=["ttymidi:MIDI_in"])
    assert "DIN jack" in text(s.midi)
    assert "No keyboard connected" in text(s.midi)


def test_a_keyboard_alongside_the_din_reads_as_connected():
    s = screen(midi=["ttymidi:MIDI_in", ROLAND])
    assert "Roland Digital Piano" in text(s.midi)
    assert "DIN jack" in text(s.midi)
    assert "No keyboard connected" not in text(s.midi)


def test_reconnect_reattaches_and_re_reads():
    calls = []
    s = screen(midi=[], reconnect=lambda: calls.append(True) or True)
    # The piano is switched on between the screen opening and the tap.
    s._midi_inputs = lambda: [ROLAND]

    press(s.midi, "Reconnect")

    assert calls == [True]
    assert "Roland Digital Piano" in text(s.midi)


# --- network ---

def test_the_address_is_shown():
    s = screen(nets=[Interface("wlan0", "UP", "192.168.1.148")])
    assert "wlan0" in text(s.network) and "192.168.1.148" in text(s.network)


def test_up_without_an_address_is_distinguished_from_offline():
    s = screen(nets=[Interface("wlan0", "UP", "")])
    assert "no address" in text(s.network)


def test_unreadable_network_is_distinguished_from_none_present():
    """After toggling WiFi on, the interface is briefly absent — that is not a
    failure to read, and saying so made a recovering radio look broken."""
    assert "Could not read" in text(screen(nets=None).network)
    assert "still coming up" in text(screen(nets=[]).network)


def test_the_wifi_button_reads_out_its_state():
    assert labels(screen(wifi=True).network) == ["WiFi: On"]
    assert labels(screen(wifi=False).network) == ["WiFi: Off"]


def test_wifi_off_says_it_comes_back_on_its_own():
    """The toggle is for one session; a power cycle always restores a
    reachable box."""
    s = screen(wifi=False)
    assert "power cycle" in text(s.network)


def test_tapping_the_button_flips_the_radio():
    asked = []
    state = {"on": True}
    s = screen(wifi=lambda: state["on"],
               set_wifi=lambda on: (asked.append(on), state.__setitem__("on", on))[0])

    press(s.network, "WiFi")
    assert _wait_for(lambda: asked == [False])
    assert _wait_for(lambda: labels(s.network) == ["WiFi: Off"])

    press(s.network, "WiFi")
    assert _wait_for(lambda: asked == [False, True])
    assert _wait_for(lambda: labels(s.network) == ["WiFi: On"])


def test_an_unknown_radio_state_is_never_guessed_at():
    """Acting on a state we could not read risks switching off the only way in."""
    asked = []
    s = screen(wifi=None, set_wifi=lambda on: asked.append(on))
    press(s.network, "WiFi")
    assert asked == []
    assert labels(s.network) == ["WiFi"]


# --- power ---

def test_shutdown_needs_two_taps():
    """It is one tap away from every screen and it ends the show."""
    fired = []
    s = screen(shutdown=lambda: fired.append(True))

    press(s.power, "Shut down")
    assert fired == []
    assert "Really?" in text(s.power) or "Really?" in labels(s.power)

    press(s.power, "Really?")
    assert fired == [True]


# --- the cog ---

def test_the_cog_is_hit_where_it_is_drawn():
    opened = []
    header = Header(
        rect=pygame.Rect(0, 0, 800, 60),
        font=pygame.font.Font(None, 36),
        action_label="New",
        on_action=lambda: None,
        on_settings=lambda: opened.append(True),
    )
    header.handle_event(UIEvent(pygame.FINGERUP, pos=header._cog_rect().center))
    assert opened == [True]


def test_the_cog_takes_the_rightmost_slot_and_actions_move_left():
    font = pygame.font.Font(None, 36)
    header = Header(
        rect=pygame.Rect(0, 0, 800, 60), font=font,
        action_label="New", on_action=lambda: None,
        on_settings=lambda: None,
    )
    cog = header._cog_rect()
    new_rect = header._action_rects()[0][0]
    assert cog.right > new_rect.right
    assert new_rect.right <= cog.left


def test_a_header_without_settings_has_no_cog_and_keeps_its_slot():
    """The naming screen: Done stays rightmost, and a cog there would discard
    what you typed."""
    font = pygame.font.Font(None, 36)
    header = Header(
        rect=pygame.Rect(0, 0, 800, 60), font=font,
        action_label="Done", on_action=lambda: None,
    )
    assert header._cog_rect() is None
    assert header._action_rects()[0][0].right > 700


def test_the_screen_draws():
    screen(midi=[ROLAND], nets=[Interface("wlan0", "UP", "10.0.0.5")]).draw(
        pygame.Surface((800, 480))
    )


# --- it re-reads while you are looking at it --------------------------------

def _wait_for(predicate, timeout=2.0):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_the_screen_re_reads_while_it_is_open():
    """Everything it reports changes underneath it. Reading once on open meant
    that after toggling WiFi it showed the instant before the interface came
    back, and went on showing it."""
    reads = []
    s = screen()
    s.refresh = lambda: reads.append(True)
    s._last_poll -= POLL_MS + 1              # as if the interval had elapsed
    s.draw(pygame.Surface((800, 480)))
    assert _wait_for(lambda: reads)


def test_re_reading_is_rate_limited():
    """Each read is three subprocesses; one per frame would be 90 a second."""
    reads = []
    s = screen()
    s.refresh = lambda: reads.append(True)
    s._last_poll -= POLL_MS + 1
    s.draw(pygame.Surface((800, 480)))
    assert _wait_for(lambda: reads)
    for _ in range(10):
        s.draw(pygame.Surface((800, 480)))
    assert len(reads) == 1


def test_switching_wifi_on_shows_that_it_is_working_on_it():
    """It takes seconds and the frame loop must keep running; a screen that
    looks frozen during it is what sent us chasing the wrong fault."""
    import threading
    release = threading.Event()
    s = screen(wifi=False, set_wifi=lambda on: release.wait(2.0) or True)

    press(s.network, "WiFi")

    assert _wait_for(lambda: "Reconnecting" in text(s.network))
    release.set()


def test_wifi_that_does_not_come_back_says_so_and_how_to_recover():
    done = []
    s = screen(wifi=False, set_wifi=lambda on: done.append(on) or False)

    press(s.network, "WiFi")

    assert _wait_for(lambda: "did not come back" in text(s.network))
    assert "Power cycle" in text(s.network)


def test_a_second_tap_while_it_is_reconnecting_is_ignored():
    import threading
    release = threading.Event()
    calls = []
    s = screen(wifi=False,
               set_wifi=lambda on: (calls.append(on), release.wait(2.0))[0] or True)

    press(s.network, "WiFi")
    assert _wait_for(lambda: calls)
    press(s.network, "WiFi")
    release.set()

    assert calls == [True]


def test_restart_needs_two_taps_as_well():
    fired = []
    s = screen(restart=lambda: fired.append(True))

    press(s.power, "Restart")
    assert fired == []

    press(s.power, "Really?")
    assert fired == [True]


def test_arming_one_power_action_does_not_arm_the_other():
    """Reaching for Restart after half-pressing Shut down must not inherit the
    confirmation and power the box off instead."""
    off, boot = [], []
    s = screen(shutdown=lambda: off.append(True), restart=lambda: boot.append(True))

    press(s.power, "Shut down")          # arms shutdown
    press(s.power, "Restart")            # should re-arm, not fire
    assert off == [] and boot == []

    press(s.power, "Really?")
    assert boot == [True] and off == []


def test_both_power_buttons_are_present_and_only_one_confirms():
    s = screen()
    assert labels(s.power) == ["Restart", "Shut down"]
    press(s.power, "Shut down")
    assert labels(s.power) == ["Restart", "Really?"]


def test_a_poll_does_not_wipe_a_pending_confirmation():
    """The screen re-reads every 1.5s; that must not disarm a prompt you are
    looking at, nor silently un-confirm the next tap."""
    fired = []
    s = screen(shutdown=lambda: fired.append(True))
    press(s.power, "Shut down")

    s.refresh()

    assert labels(s.power) == ["Restart", "Really?"]
    press(s.power, "Really?")
    assert fired == [True]


def test_a_long_line_is_clipped_rather_than_drawn_under_the_buttons():
    from synth_ui.ui.screens.settings import _fit

    font = pygame.font.Font(None, 22)
    long = "Restart reloads everything and shut down stops it so the card " \
           "can be pulled out safely without corrupting anything at all"
    fitted = _fit(long, font, 300)
    assert font.size(fitted)[0] <= 300
    assert fitted.endswith("...")
    assert _fit("short", font, 300) == "short"
