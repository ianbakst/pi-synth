"""SettingsScreen — the things about the box, rather than about the sound.

Reached by the cog, which sits in the same top-right slot on every screen that
has one.

**There is no audio device picker.** There was, and it earned its removal: this
board has exactly one card worth playing through, and `scripts/start-jack.sh`
already prefers the HiFiBerry *by name*, falling back to the first available
card and then to jackd's dummy backend. Selecting a card by hand could only ever
move audio somewhere worse — the onboard USB codec on the carrier board, whose
jack is sealed inside the enclosure. A saved choice in `~/.synth-audio-device` is
still honoured if one is written by hand.

What replaced it is diagnosis. The instrument has a screen and no console: when
the keys go dead or the box vanishes from the network, this is the only place it
can say why.
"""

import threading
from collections.abc import Callable

import pygame

from synth_ui.clients.network import Interface
from synth_ui.config import (
    BTN_NORMAL,
    DIVIDER,
    HEADER_H,
    PANEL_BG,
    SCREEN_H,
    SCREEN_W,
    STATUS_ERR,
    STATUS_OK,
    TEXT_ACTIVE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen

ROW_H = 96
PAD = 16
BUTTON_H = 56
BUTTON_MIN_W = 130
BUTTON_PAD_X = 18
BUTTON_GAP = 8
# How often the screen re-reads what it is reporting, while it is on screen.
# Everything here changes underneath it — a piano is switched on, the radio
# comes back and DHCP hands out an address — and a screen that answers "what is
# going on" with a snapshot from when it opened is worse than useless: after
# toggling WiFi it showed the half-second where the interface had not returned
# yet, and went on showing it.
POLL_MS = 1500


class _Section(Component):
    """A titled block: some lines of status, and buttons along the right.

    Buttons lay out right-to-left from the edge and are sized to their label,
    the way the header's do — so Power can carry both Restart and Shut down
    without either being a different size from the Reconnect above it.
    """

    def __init__(
        self,
        rect: pygame.Rect,
        title: str,
        font_title: pygame.font.Font,
        font_body: pygame.font.Font,
        buttons: list[tuple[str, Callable]] | None = None,
    ):
        super().__init__(rect)
        self.title = title
        self.lines: list[tuple[str, tuple[int, int, int]]] = []
        self._font_title = font_title
        self._font_body = font_body
        self.buttons: list[tuple[str, Callable]] = list(buttons or [])

    def button_rects(self) -> list[tuple[pygame.Rect, str, Callable]]:
        rects: list[tuple[pygame.Rect, str, Callable]] = []
        x_right = self.rect.right - PAD
        y = self.rect.y + (self.rect.height - BUTTON_H) // 2
        for label, callback in self.buttons:
            width = max(
                BUTTON_MIN_W,
                self._font_title.render(label, True, TEXT_ACTIVE).get_width()
                + BUTTON_PAD_X * 2,
            )
            rect = pygame.Rect(x_right - width, y, width, BUTTON_H)
            rects.append((rect, label, callback))
            x_right = rect.x - BUTTON_GAP
        return rects

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.line(
            surface, DIVIDER,
            (self.rect.x + PAD, self.rect.bottom - 1),
            (self.rect.right - PAD, self.rect.bottom - 1),
        )
        title = self._font_title.render(self.title, True, TEXT_PRIMARY)
        surface.blit(title, (self.rect.x + PAD, self.rect.y + 14))
        y = self.rect.y + 14 + title.get_height() + 4
        # Text stops where the buttons start. Without this a long line renders
        # straight under them and reads as gibberish, which is easy to write by
        # accident because nothing about `lines` suggests a width limit.
        rects = self.button_rects()
        limit = (rects[-1][0].x - BUTTON_GAP) if rects else (self.rect.right - PAD)
        available = limit - (self.rect.x + PAD)
        for text, color in self.lines:
            line = self._font_body.render(_fit(text, self._font_body, available),
                                          True, color)
            surface.blit(line, (self.rect.x + PAD, y))
            y += line.get_height() + 2

        for rect, label, _callback in self.button_rects():
            pygame.draw.rect(surface, BTN_NORMAL, rect, border_radius=6)
            surf = self._font_title.render(label, True, TEXT_ACTIVE)
            surface.blit(
                surf,
                (rect.centerx - surf.get_width() // 2,
                 rect.centery - surf.get_height() // 2),
            )

    def handle_event(self, event: UIEvent) -> bool:
        if event.type not in (pygame.FINGERUP, pygame.MOUSEBUTTONDOWN):
            return False
        for rect, _label, callback in self.button_rects():
            if rect.collidepoint(event.pos):
                callback()
                return True
        return False


class SettingsScreen(Screen):
    def __init__(
        self,
        on_back: Callable,
        midi_inputs: Callable[[], list[str]],
        on_reconnect_midi: Callable[[], bool],
        interfaces: Callable[[], list[Interface]],
        wifi_enabled: Callable[[], bool | None],
        on_set_wifi: Callable[[bool], bool],
        on_shutdown: Callable,
        on_restart: Callable,
        on_brightness: Callable[[float], None] | None = None,
        initial_brightness: float | None = None,
    ):
        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self._midi_inputs = midi_inputs
        self._interfaces = interfaces
        self._wifi_enabled = wifi_enabled
        self._on_set_wifi = on_set_wifi
        # Turning it back on takes seconds and can fail; the screen has to say
        # which of those is happening rather than look frozen.
        self._wifi_busy = False
        self._wifi_failed = False
        self._on_reconnect = on_reconnect_midi
        self._on_shutdown = on_shutdown
        self._on_restart = on_restart
        # Both power actions ask twice. Either is one tap away from every
        # screen and either ends the show; a mis-aimed finger must not do that.
        # Arming is per-action, so reaching for Restart cancels a half-pressed
        # Shut down rather than inheriting its confirmation.
        self._armed: str | None = None

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            on_back=on_back,
        )
        self.header.name = "Settings"

        y = HEADER_H
        self.midi = _Section(
            pygame.Rect(0, y, SCREEN_W, ROW_H), "MIDI in",
            font_medium, font_small,
            buttons=[("Reconnect", self._reconnect)],
        )
        y += ROW_H
        self.network = _Section(
            pygame.Rect(0, y, SCREEN_W, ROW_H), "Network",
            font_medium, font_small,
            buttons=[("WiFi", self._toggle_wifi)],
        )
        y += ROW_H
        self.power = _Section(
            pygame.Rect(0, y, SCREEN_W, ROW_H), "Power",
            font_medium, font_small,
            buttons=[("Restart", self._restart), ("Shut down", self._shutdown)],
        )
        y += ROW_H

        self.brightness_slider: Slider | None = None
        if on_brightness is not None and initial_brightness is not None:
            self.brightness_slider = Slider(
                rect=pygame.Rect(0, y, SCREEN_W, SCREEN_H - y),
                initial_value=initial_brightness,
                on_change=on_brightness,
                min_value=0.0,
                max_value=1.0,
                label="Brightness",
                font=font_small,
                format_value=lambda v: f"{int(v * 100)}%",
                live=True,
            )

        # Set to now, not zero: __init__ reads once below, and zero would mean
        # "last polled at pygame startup", which early in a session reads as
        # recent enough to skip.
        self._last_poll = pygame.time.get_ticks()
        self._polling = False
        self.refresh()
        self.components = tuple(
            c for c in (self.header, self.midi, self.network, self.power,
                        self.brightness_slider)
            if c is not None
        )

    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read what's connected: on open, on a timer, and after a button."""
        named = [d for d in (_describe(p) for p in self._midi_inputs()) if d]
        keyboards = [d for d in named if d != "DIN jack"]
        if keyboards:
            self.midi.lines = [(d, STATUS_OK) for d in named]
        else:
            # The DIN jack is always there, so it is not an answer to "is my
            # keyboard connected".
            self.midi.lines = [("No keyboard connected.", STATUS_ERR)] + [
                (d, TEXT_SECONDARY) for d in named
            ] + [("A piano asleep on Auto Off looks like this.", TEXT_SECONDARY)]

        radio = self._wifi_enabled()
        self.network.buttons = [
            ({True: "WiFi: On", False: "WiFi: Off"}.get(radio, "WiFi"),
             self._toggle_wifi)
        ]
        if self._wifi_busy:
            self.network.lines = [("Reconnecting...", TEXT_SECONDARY)]
            return
        if self._wifi_failed:
            self.network.lines = [
                ("WiFi did not come back.", STATUS_ERR),
                ("Power cycle to recover — it always comes up on.",
                 TEXT_SECONDARY),
            ]
            return
        if radio is False:
            self.network.lines = [
                ("WiFi off. Back on at the next power cycle.", TEXT_SECONDARY),
            ]
            return

        if self._armed is None:
            self._set_power_buttons()
            self.power.lines = [
                ("Restart reloads everything.", TEXT_SECONDARY),
                ("Shut down before pulling the plug.", TEXT_SECONDARY),
            ]

        found = self._interfaces()
        if found is None:
            self.network.lines = [("Could not read interfaces.", TEXT_SECONDARY)]
        elif not found:
            # The radio comes back before the interface does; this is what the
            # seconds after switching WiFi on look like.
            self.network.lines = [("No interface yet — still coming up.",
                                   TEXT_SECONDARY)]
        else:
            self.network.lines = [
                (
                    f"{i.name}  {i.address}" if i.address
                    else f"{i.name}  up, but no address",
                    STATUS_OK if i.usable else STATUS_ERR,
                )
                for i in found
            ]

    def _power(self, action: str, label: str, warning: str, run: Callable) -> None:
        """Arm on the first tap, act on the second."""
        if self._armed != action:
            self._armed = action
            self._set_power_buttons(confirming=action)
            self.power.lines = [(warning, STATUS_ERR)]
            return
        run()

    def _set_power_buttons(self, confirming: str | None = None) -> None:
        self.power.buttons = [
            ("Really?" if confirming == "restart" else "Restart", self._restart),
            ("Really?" if confirming == "shutdown" else "Shut down", self._shutdown),
        ]

    def _restart(self) -> None:
        self._power("restart", "Restart",
                    "Tap Really? again to restart.", self._on_restart)

    def _toggle_wifi(self) -> None:
        """Switch the radio.

        No confirmation: unlike shutdown it is undone by tapping again, and by a
        power cycle regardless. But switching it *on* waits for an address
        rather than for the radio, so it runs on a worker — the frame loop has
        to keep drawing, and the polling read is what shows it coming back.
        """
        current = self._wifi_enabled()
        if current is None or self._wifi_busy:
            return          # unknown state; don't guess at what to set it to
        self._wifi_busy = True

        def apply() -> None:
            try:
                ok = self._on_set_wifi(not current)
                self._wifi_failed = not ok and not current
            finally:
                self._wifi_busy = False
                self.refresh()

        threading.Thread(target=apply, daemon=True).start()
        self.refresh()

    def _reconnect(self) -> None:
        self._on_reconnect()
        self.refresh()

    def _shutdown(self) -> None:
        self._power("shutdown", "Shut down",
                    "Tap Really? again to power off.", self._on_shutdown)

    def _poll(self) -> None:
        """Re-read on a timer while this screen is drawn.

        On a worker, because every reading here is a subprocess — `nmcli`, `ip`,
        `jack_lsp` — and running three of them on the UI thread would hitch the
        frame loop every time. One in flight at a time, and none at all once the
        screen stops being drawn, so navigating away ends it with no teardown
        hook to forget to call.
        """
        now = pygame.time.get_ticks()
        if self._polling or now - self._last_poll < POLL_MS:
            return
        self._last_poll = now
        self._polling = True

        def read() -> None:
            try:
                self.refresh()
            finally:
                self._polling = False

        threading.Thread(target=read, daemon=True).start()

    def draw(self, surface: pygame.Surface) -> None:
        self._poll()
        surface.fill(PANEL_BG, pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H))
        super().draw(surface)


def _fit(text: str, font: pygame.font.Font, width: int) -> str:
    """`text`, shortened with an ellipsis until it fits `width`."""
    if width <= 0 or font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text + "..."


def _describe(port: str) -> str | None:
    """A MIDI source as a player would name it, or None to leave it out.

    Two ports are always present and neither is a keyboard being plugged in:
    ALSA's `Midi Through` loopback, which carries nothing, and ttymidi's port
    for the DIN jack, which exists whether or not a cable is in it. Listing the
    loopback would make this screen answer "is my piano connected" with yes,
    every time — which is exactly the question it exists to answer.

    JACK names are otherwise long and mostly boilerplate:
    `a2j:Roland Digital Piano [24] (capture): Roland Digital Piano MIDI 1`.
    """
    if "midi through" in port.lower():
        return None
    if port.startswith("ttymidi:"):
        return "DIN jack"
    name = port.split(":", 1)[1] if ":" in port else port
    name = name.split("(capture)")[0].split(" [")[0]
    return name.strip(" :") or port
