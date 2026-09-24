import os
import subprocess
import threading
from dataclasses import replace

import pygame

from synth_ui.clients import EngineManager
from synth_ui.clients.backlight import Backlight
from synth_ui.clients.display_device import select_kmsdrm_device
from synth_ui.clients.effects_catalog import (
    EffectCatalogEntry,
    annotate_effects,
    read_effects_manifest,
)
from synth_ui.clients.midi_control import MidiControlListener
from synth_ui.clients.network import (
    enable_wifi,
    ensure_wifi_on,
    set_wifi,
    wifi_enabled,
)
from synth_ui.clients.network import (
    interfaces as network_interfaces,
)
from synth_ui.clients.rig import Rig, RigEffect
from synth_ui.clients.set import SetLibrary, SongSet
from synth_ui.clients.voice import Voice
from synth_ui.config import (
    BG,
    BRIGHTNESS_FILE,
    DEFAULT_BRIGHTNESS,
    DEFAULT_GAIN,
    DEFAULT_VOICE,
    EFFECTS_MANIFEST,
    IS_PI,
    MIDI_NEXT_RIG_CC,
    MIDI_PREV_RIG_CC,
    MIDI_PROGRAM_SELECTS_RIG,
    MOD_HOST_PORT,
    RIGS_FILE,
    SCREEN_H,
    SCREEN_W,
    SETS_FILE,
    SOUNDFONT_DIR,
    STATE_FILE,
    TRIMS_FILE,
    VOICES_MANIFEST,
)
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.screens.effects import EffectsCatalogScreen, EffectsScreen
from synth_ui.ui.screens.params import ParamsScreen
from synth_ui.ui.screens.rigs import RigsScreen
from synth_ui.ui.screens.sets import SetsScreen
from synth_ui.ui.screens.settings import SettingsScreen
from synth_ui.ui.screens.splash import SplashScreen
from synth_ui.ui.screens.text_entry import TextEntryScreen
from synth_ui.ui.screens.usb import USBScreen
from synth_ui.ui.screens.voice_picker import VoicePickerScreen
from synth_ui.ui.utils import load_voices, lv2_world

SPLASH_DURATION_MS = 5000


def _load_state() -> tuple[str, str]:
    """(set id, rig id) from last time — where you were, not what it was called.

    Two lines now that a rig lives inside a set. A one-line file is the format
    written before sets existed, where the single value was the active rig's
    *name*; it is returned as the rig field and resolved by name as a fallback,
    so an upgrade lands you back on the rig you left rather than at the top of
    the list.
    """
    try:
        with open(STATE_FILE) as f:
            lines = [line.strip() for line in f.read().splitlines()]
    except OSError:
        return "", ""
    if len(lines) >= 2:
        return lines[0], lines[1]
    return "", (lines[0] if lines else "")


def _save_state(set_id: str, rig_id: str) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            f.write(f"{set_id}\n{rig_id}\n")
    except Exception:
        pass


def _set_wifi(enabled: bool) -> bool:
    """Switching off is one command; switching on has to be waited for.

    `nmcli radio wifi on` returns before the interface is back, so reporting its
    success as "WiFi is on" told the player the box was reachable when it was
    not — on a box whose only way in is that radio.
    """
    return enable_wifi() if enabled else set_wifi(False)


def _shutdown() -> None:
    """Power off cleanly.

    The box has no power switch — it is switched off by pulling the plug, which
    is also how an SD card gets corrupted. This is the only way to stop the
    writes first.
    """
    _power_off(["sudo", "systemctl", "poweroff"])


def _restart() -> None:
    """Reboot. Distinct from pulling the plug and back in for the same reason
    shutdown is, and it is the honest way to recover a box whose audio stack has
    got itself into a state — the touchscreen is the only console it has."""
    _power_off(["sudo", "systemctl", "reboot"])


def _power_off(command: list[str]) -> None:
    try:
        subprocess.run(command, timeout=10)
    except Exception:
        pass


def _load_brightness() -> float:
    """Saved screen brightness as a 0..1 fraction, defaulting to full.

    Anything unreadable or out of range reads as the default rather than as an
    error: a corrupt file must not leave the instrument booting to a black
    screen it has no way to recover from.
    """
    try:
        with open(BRIGHTNESS_FILE) as f:
            value = float(f.read().strip())
    except (OSError, ValueError):
        return DEFAULT_BRIGHTNESS
    return value if 0.0 <= value <= 1.0 else DEFAULT_BRIGHTNESS


def _save_brightness(fraction: float) -> None:
    try:
        with open(BRIGHTNESS_FILE, "w") as f:
            f.write(f"{fraction:.3f}")
    except OSError:
        pass


class SynthUI:
    def __init__(self):
        # This app never plays audio through pygame — every sound path goes
        # through JACK (fluidsynth/mod-host) to the DAC. Without this, SDL's
        # audio mixer opens its own ALSA PCM stream on init and, since the
        # HiFiBerry is effectively the only playback device once onboard audio
        # is disabled, fights jackd for it: continuous ALSA underruns and no
        # audio actually reaching the DAC. Must be set before pygame.init().
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        # Pick the DRM card the panel is actually on. On CM5 the DSI panel and
        # the GPU are separate DRM devices and SDL selects between them by a
        # probe-order index, so this has to be resolved by name. Must be set
        # before pygame.init(). See clients/display_device.py.
        if IS_PI:
            select_kmsdrm_device()
        # Touch needs no SDL env setup: SDL2 auto-scans /dev/input/event* and
        # works as long as this process's user is in the 'input' group. The old
        # SDL 1.2 vars (SDL_FBDEV/SDL_MOUSEDEV/SDL_MOUSEDRV) are ignored by SDL2.
        pygame.init()

        if IS_PI:
            self.display = pygame.display.set_mode(
                (SCREEN_W, SCREEN_H), pygame.FULLSCREEN
            )
            pygame.mouse.set_visible(False)
        else:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        self._engine = EngineManager(
            mod_host_port=MOD_HOST_PORT,
        )
        self._gain: float = DEFAULT_GAIN
        self._settings_screen: SettingsScreen | None = None
        self._effects_screen: EffectsScreen | None = None
        self._catalog_screen: EffectsCatalogScreen | None = None
        self._params_screen: ParamsScreen | None = None
        self._insert_at: int | None = None
        # Set while the voice picker is open to swap an existing rig's
        # instrument rather than to build a new rig from the chosen voice.
        self._picking_replaces_instrument = False
        # Annotated so the browser can grey out effects this board can't load,
        # rather than letting a tap fail silently.
        self._catalog: list[EffectCatalogEntry] = annotate_effects(
            read_effects_manifest(EFFECTS_MANIFEST), lv2_world.has
        )
        self._picker: VoicePickerScreen | None = None

        # Sets are what you pick between songs; the rigs inside one are what you
        # pick during a song. A freshly flashed card has neither, so bootstrap a
        # set holding one DEFAULT_VOICE rig — otherwise the instrument boots to
        # an empty list and makes no sound. RIGS_FILE is read only to migrate a
        # board that predates sets.
        self._sets = SetLibrary.load(SETS_FILE, legacy_rigs_path=RIGS_FILE)
        self._sets.bootstrap(DEFAULT_VOICE)
        self._active_set: SongSet | None = None
        self._library: dict[str, Voice] = {}
        self._reload_library()

        self._home = SetsScreen(
            sets=self._sets.sets,
            on_enter_set=self._enter_set,
            on_edit_set=self._show_set_rename_screen,
            on_new=self._on_new_set,
            on_settings=self._show_settings_screen,
            on_gain_change=self._on_gain_change,
            on_reorder=self._on_reorder_sets,
            initial_gain=self._gain,
        )
        self._rig_screen = RigsScreen(
            rigs=[],
            on_load_rig=self._load_rig,
            on_edit_rig=self._show_rename_screen,
            on_new=lambda: self._show_voice_picker(replaces_instrument=False),
            on_edit=self._show_effects_screen,
            on_back=self._show_home,
            on_gain_change=self._on_gain_change,
            on_settings=self._show_settings_screen,
            on_reorder=self._on_reorder_rigs,
            effect_names={e.uri: e.name for e in self._catalog},
            unavailable=self._rig_unavailable,
            initial_gain=self._gain,
        )
        self._restore_state()
        # Hands-free rig switching. Runs whether or not a pedal is attached —
        # aseqdump subscribes to whatever appears, so plugging one in later
        # works with no restart. Failure is non-fatal: the touchscreen is the
        # primary control and must keep working regardless.
        self._midi_control = MidiControlListener(
            on_next=self._rig_screen.select_next,
            on_previous=self._rig_screen.select_previous,
            next_cc=MIDI_NEXT_RIG_CC,
            prev_cc=MIDI_PREV_RIG_CC,
            on_program=self._select_rig_by_index if MIDI_PROGRAM_SELECTS_RIG else None,
        )
        self._midi_control.start()
        # A WiFi toggle is for one session only: this box has no Ethernet, and a
        # remembered "off" would leave no way in when it is needed most. See
        # clients/network.set_wifi.
        threading.Thread(target=ensure_wifi_on, daemon=True).start()

        # Restore the panel brightness before anything is drawn, so the first
        # thing on screen is already at the level the player left it.
        self._backlight = Backlight()
        self._brightness = _load_brightness()
        if self._backlight.available:
            self._backlight.set_fraction(self._brightness)

        self.screen: Screen = SplashScreen()
        self._splash_start = pygame.time.get_ticks()
        self._splash_done = False

    # ------------------------------------------------------------------
    # Rigs
    # ------------------------------------------------------------------

    def _reload_library(self) -> None:
        """Rebuild the voice library from the manifest and the soundfont folder.

        Deliberately NOT done on lookup. `_voice_for` is reached from
        the rig tiles — rebuilt whenever the library changes — and building the
        library scans every soundfont on the SD card and reads its header. Doing
        that per lookup meant re-reading the whole library over a hundred times
        a second: 66% CPU from a 30 fps touchscreen loop, and heavy SD I/O on
        core 0, which also carries the audio interrupt.

        So it's built once, and rebuilt only when the library can actually have
        changed: opening the voice picker, and after a USB import.
        """
        self._library = {
            v.name: v for v in load_voices(VOICES_MANIFEST, SOUNDFONT_DIR, TRIMS_FILE)
        }

    def _voice_for(self, name: str) -> Voice | None:
        return self._library.get(name)

    @property
    def _rigs(self) -> list[Rig]:
        """The active set's rigs. Empty when no set is active, which is only
        the case after the playing set was deleted."""
        return self._active_set.rigs if self._active_set is not None else []

    def _rig_unavailable(self, rig: Rig) -> str:
        """Why this rig can't be loaded here — surfaced on the row rather than
        discovered by tapping it. A rig outlives the catalog: its voice can be
        renamed away or its sample library never installed."""
        voice = self._voice_for(rig.voice)
        if voice is None:
            return f"voice '{rig.voice}' not in the library"
        return voice.unavailable_reason

    def _load_rig(self, rig: Rig) -> bool:
        voice = self._voice_for(rig.voice)
        if voice is None:
            return False
        return self._engine.load_rig(rig, voice)

    # ------------------------------------------------------------------
    # Sets
    # ------------------------------------------------------------------

    def _restore_state(self) -> None:
        """Pick up where the instrument was left, in a background thread so the
        splash is drawn while a sample library loads.

        Resuming, not entering: the saved rig is loaded even if it isn't the
        first in its set. Only if it can't be found does this fall back to the
        set's first usable rig, so a fresh card — or one whose saved rig was
        deleted — still boots into something playable rather than silence.
        """
        set_id, rig_id = _load_state()
        song_set = self._sets.get(set_id)
        rig = song_set.get(rig_id) if song_set else None
        if rig is None:
            # Either the pre-sets state file, whose single value was a rig
            # *name*, or a rig that has since moved or gone.
            found = self._sets.find_rig(rig_id)
            if found is None and rig_id:
                found = next(
                    (
                        (s, r)
                        for s in self._sets.sets
                        for r in s.rigs
                        if r.name == rig_id
                    ),
                    None,
                )
            if found is not None:
                song_set, rig = found
        if song_set is None:
            song_set = self._sets.sets[0] if self._sets.sets else None
        if song_set is None:
            return

        self._activate_set(song_set)
        target = rig if rig is not None and not self._rig_unavailable(rig) else None

        def resume() -> None:
            if target is not None:
                self._rig_screen.select(target)
            else:
                self._rig_screen.load_first()
            # Warmed here as well as on entry, or the set you boot into would be
            # the one set that isn't ready — you'd have to leave it and come
            # back to get instant switching in the first song of the night.
            self._warm_set(song_set)

        self._in_background(resume)

    def _in_background(self, target) -> None:
        """Run something off the UI thread.

        Loading a rig can take seconds when its voice is a sample library, and
        the frame loop must keep drawing through it. A named seam rather than an
        inline Thread so tests can run these steps in order instead of racing
        them.
        """
        threading.Thread(target=target, daemon=True).start()

    def _activate_set(self, song_set: SongSet) -> None:
        """Make a set the one being played: its rigs on the rigs screen, its pad
        highlighted on the sets screen. Loads nothing by itself."""
        self._active_set = song_set
        self._home.set_active(song_set)
        self._rig_screen.set_rigs(song_set.rigs, title=song_set.name)

    def _enter_set(self, song_set: SongSet) -> None:
        """Tapping a set opens it — and, if it wasn't already the active one,
        makes it active and starts its first usable rig.

        Re-entering the set you're already in changes nothing, which is what
        makes coming back from editing a chain safe. Entering a different one is
        a deliberate move to another song, so it loads: walking into a set
        should leave you ready to play without a second tap.
        """
        already_active = (
            self._active_set is not None and self._active_set.id == song_set.id
        )
        self._activate_set(song_set)
        self.screen = self._rig_screen
        if not already_active:
            self._in_background(lambda: self._start_set(song_set))

    def _start_set(self, song_set: SongSet) -> None:
        """Make a freshly entered set playable, then ready.

        The first rig loads first so there's sound as soon as possible; the rest
        of the set is warmed behind it, which is the part that makes every
        switch *within* the song immediate. Warming after rather than before
        also leaves the rig you're about to play as the most recently used, so
        a set with more rigs than there are resident slots can't evict the one
        under your hands.
        """
        self._rig_screen.load_first()
        self._warm_set(song_set)

    def _warm_set(self, song_set: SongSet) -> None:
        """Pre-load the set's instruments, each with the patch its rig will ask
        for — so selecting a rig finds the plugin loaded *and* already set up,
        and the reconcile on switch has nothing left to write."""
        voices = []
        for rig in song_set.rigs:
            voice = self._voice_for(rig.voice)
            if voice is None or self._rig_unavailable(rig):
                continue
            voices.append(
                replace(voice, params={**voice.params, **rig.voice_params})
            )
        if voices:
            self._engine.warm(voices)

    def _on_new_set(self) -> None:
        song_set = self._sets.create()
        self._home.refresh(self._sets.sets)
        self._show_set_rename_screen(song_set, title="Name set")

    def _show_set_rename_screen(
        self, song_set: SongSet, title: str = "Rename set"
    ) -> None:
        def done(name: str) -> None:
            song_set.name = name.strip() or song_set.name
            self._sets.save()
            self._home.refresh(self._sets.sets)
            if self._active_set is not None and self._active_set.id == song_set.id:
                self._rig_screen.set_rigs(song_set.rigs, title=song_set.name)
            self._show_home()

        self.screen = TextEntryScreen(
            title=title,
            initial=song_set.name,
            on_done=done,
            on_cancel=self._show_home,
            on_delete=lambda: self._on_remove_set(song_set),
            delete_label="Delete set",
        )

    def _on_remove_set(self, song_set: SongSet) -> None:
        """Deleting a set deletes the rigs inside it — they live nowhere else.

        The set that is playing can be deleted like any other; what keeps
        sounding is whatever the engine already has, since nothing here unloads
        it. The instrument is left with no active set rather than silently
        jumping into another song's.
        """
        self._sets.remove(song_set.id)
        if self._active_set is not None and self._active_set.id == song_set.id:
            self._active_set = None
            self._home.set_active(None)
            self._rig_screen.set_rigs([], title="No set")
        self._home.refresh(self._sets.sets)
        self._show_home()

    def _on_reorder_sets(self, source: int, target: int) -> None:
        if self._sets.move(source, target):
            self._home.refresh(self._sets.sets)

    def _save_where_we_were(self) -> None:
        """Record the active set and rig on the way out, by id, so the next
        boot resumes here rather than at the top of the list."""
        rig = self._rig_screen.active_rig
        if self._active_set is not None and rig is not None:
            _save_state(self._active_set.id, rig.id)

    def _show_voice_picker(self, replaces_instrument: bool = False) -> None:
        """The voice catalogue, opened for one of two jobs.

        The flag is set here rather than by the caller so that "New" always
        clears it: a swap abandoned with Back would otherwise leave it set, and
        the next new rig would silently replace the current one's instrument
        instead of creating anything.
        """
        self._picking_replaces_instrument = replaces_instrument
        self._reload_library()   # pick up fonts added since startup
        self._picker = VoicePickerScreen(
            on_pick=self._on_voice_picked,
            # Back returns where you came from: the chain you were editing, or
            # home if you were starting a new rig.
            on_back=(
                self._show_effects_screen if replaces_instrument else self._show_rigs
            ),
            on_usb=self._show_usb_screen,
        )
        self.screen = self._picker

    def _on_voice_picked(self, voice: Voice) -> None:
        """A picked voice becomes a new rig — bare instrument, no effects yet —
        which is then loaded and made active, ready for effects to be stacked.

        Unless the picker was opened from the chain's instrument block, in which
        case it replaces the active rig's instrument and leaves its chain."""
        if self._picking_replaces_instrument:
            self._picking_replaces_instrument = False
            self._swap_instrument(voice)
            return
        if self._active_set is None:
            return
        rig = self._active_set.create_from_voice(voice.name)
        self._sets.save()
        self._rig_screen.set_rigs(self._active_set.rigs)
        self._home.refresh(self._sets.sets)
        ok = self._engine.load_rig(rig, voice)
        self._rig_screen._active_rig = rig
        self._rig_screen.grid.tiles = self._rig_screen._tiles()
        self._rig_screen.header.name = rig.name
        self._rig_screen.header.error = not ok

        # Name it while you have the context for what it is. The rig is already
        # created and loaded, so the instrument is playable during naming and
        # cancelling just keeps the auto-name.
        self._show_rename_screen(rig, title="Name rig")

    def _show_rename_screen(self, rig: Rig, title: str = "Rename rig") -> None:
        def done(name: str) -> None:
            # A plain assignment: the name is metadata now, so two rigs may
            # share one and renaming can't turn this into a different rig.
            rig.name = name.strip() or rig.name
            self._sets.save()
            self._rig_screen.header.name = rig.name
            self._rig_screen.set_rigs(self._rigs)
            self._show_rigs()

        self.screen = TextEntryScreen(
            title=title,
            initial=rig.name,
            on_done=done,
            on_cancel=self._show_rigs,
            on_delete=lambda: self._on_remove_rig(rig),
            delete_label="Delete rig",
        )

    def _on_remove_rig(self, rig: Rig) -> None:
        """A rig lives in exactly one set, so removing it from that set is
        deleting it. What's playing keeps playing — nothing unloads the engine —
        but the screen stops claiming to be on a rig that no longer exists."""
        if self._active_set is None:
            return
        self._active_set.remove(rig.id)
        self._sets.save()
        self._rig_screen.rig_removed(rig.id)
        self._rig_screen.set_rigs(self._active_set.rigs)
        self._home.refresh(self._sets.sets)
        self._show_rigs()

    def _select_rig_by_index(self, index: int) -> None:
        """Program Change selects a rig by position *within the active set*,
        which is what makes it usable: a song's four rigs are PC 0-3.

        Out-of-range is ignored rather than clamped: a Program Change past the
        end of the set is someone else's message, not a request for the last
        rig.
        """
        rigs = self._rigs
        if 0 <= index < len(rigs):
            self._rig_screen.select(rigs[index])

    def _on_reorder_rigs(self, source: int, target: int) -> None:
        if self._active_set is not None and self._active_set.move(source, target):
            self._sets.save()
            self._rig_screen.set_rigs(self._active_set.rigs)

    def _sync_active_rig(self) -> None:
        """Persist what's been dialled in — chain, instrument patch, level —
        into the active rig. Called when leaving the effects screen: editing a
        rig's blocks *is* editing the rig, so there's no separate save step to
        forget."""
        rig = self._rig_screen.active_rig
        if rig is None:
            return
        if self._effects_screen is not None:
            rig.trim_db = self._effects_screen.trim_slider.value
            rig.fixed_velocity = self._effects_screen.fixed_velocity
        # Carry params and bypass, not just the URI. Saving the chain without
        # what was dialled into it is the same as not saving it.
        rig.effects = [
            RigEffect(uri=e.uri, params=dict(e.params), bypassed=e.bypassed)
            for e in self._engine.effects()
        ]
        # The instrument's controls, for the same reason — but only where they
        # differ from the catalog entry. A rig stores what *this sound* does to
        # the instrument, not a copy of the instrument, so a corrected value in
        # a shipped voice still reaches every rig built on it.
        catalog = self._library.get(rig.voice)
        if catalog is not None:
            rig.voice_params = {
                symbol: value
                for symbol, value in self._engine.voice_params().items()
                if catalog.params.get(symbol) != value
            }
        if self._active_set is not None:
            self._active_set.replace(rig)
            self._sets.save()
        self._rig_screen.set_rigs(self._rigs)

    def _on_gain_change(self, gain: float) -> None:
        self._gain = gain
        self._engine.set_gain(gain)

    def _show_home(self) -> None:
        """Home is the sets screen. Leaving a set never changes what is
        playing — browsing is not switching."""
        self.screen = self._home

    def _show_rigs(self) -> None:
        """Back to the active set's rigs — where you were before naming or
        editing something."""
        self.screen = self._rig_screen

    def _show_usb_screen(self) -> None:
        self.screen = USBScreen(
            on_back=self._show_home,
            on_copy_complete=self._on_usb_copy_complete,
        )

    def _on_usb_copy_complete(self) -> None:
        # USB import adds soundfonts to the catalog.
        self._reload_library()
        if self._picker is not None:
            self._picker.refresh()

    def _show_settings_screen(self) -> None:
        """The cog, from any screen that has one. Back always returns home
        rather than to wherever you came from: settings is a detour, and a Back
        that lands somewhere different each time is worse than one that always
        lands in the same place."""
        self._settings_screen = SettingsScreen(
            on_back=self._show_home,
            midi_inputs=self._engine.midi_inputs,
            on_reconnect_midi=self._engine.reattach_midi,
            interfaces=network_interfaces,
            wifi_enabled=wifi_enabled,
            on_set_wifi=_set_wifi,
            on_shutdown=_shutdown,
            on_restart=_restart,
            # Both None on a board with no backlight, which hides the slider
            # rather than showing one that does nothing.
            on_brightness=(
                self._on_brightness if self._backlight.available else None
            ),
            initial_brightness=(
                self._brightness if self._backlight.available else None
            ),
        )
        self.screen = self._settings_screen

    def _on_brightness(self, fraction: float) -> None:
        """Applied live while dragging; saved on each change.

        Saving every event is cheap (one short file) and worth it: the
        instrument is powered down by pulling the plug, so there is no shutdown
        hook to write it in.
        """
        self._brightness = fraction
        self._backlight.set_fraction(fraction)
        _save_brightness(fraction)

    def _show_effects_screen(self) -> None:
        rig = self._rig_screen.active_rig
        self._effects_screen = EffectsScreen(
            effects=self._engine.effects(),
            catalog=self._catalog,
            on_remove=self._on_remove_effect,
            on_bypass=self._on_bypass_effect,
            on_edit=self._show_effect_params_screen,
            on_add=self._show_effects_catalog_screen,
            on_back=self._leave_effects_screen,
            on_trim_change=self._engine.set_rig_trim,
            on_reorder=self._on_reorder_effects,
            on_edit_instrument=self._show_voice_params_screen,
            # None hides the control on a board that can't do it, rather than
            # offering a button that silently fails.
            on_fixed_velocity=(
                self._engine.set_fixed_velocity
                if self._engine.fixed_velocity_available()
                else None
            ),
            on_settings=self._show_settings_screen,
            initial_trim=rig.trim_db if rig is not None else 0.0,
            initial_fixed_velocity=rig.fixed_velocity if rig is not None else False,
            source_name=rig.voice if rig is not None else "",
            rig_name=rig.name if rig is not None else "",
        )
        self.screen = self._effects_screen

    def _leave_effects_screen(self) -> None:
        self._sync_active_rig()
        self._show_home()

    def _show_effects_catalog_screen(self, index: int | None = None) -> None:
        # Which `+` on the wire was tapped, so the effect lands where the chain
        # wants it rather than always on the end.
        self._insert_at = index
        self._catalog_screen = EffectsCatalogScreen(
            catalog=self._catalog,
            on_select=self._on_add_effect,
            on_back=self._show_effects_screen,
        )
        self.screen = self._catalog_screen

    def _show_instrument_swap(self) -> None:
        """Pick a different instrument for the rig being edited.

        Same picker as building a new rig; what differs is what happens on the
        way back, so the mode is recorded here rather than duplicating the
        screen."""
        self._show_voice_picker(replaces_instrument=True)

    def _swap_instrument(self, voice: Voice) -> None:
        """Change the active rig's instrument, keeping its effects chain.

        The chain is the point of a rig, so it survives: the same reverb and
        delay, now fed by a different instrument. Only the first block changes.
        """
        rig = self._rig_screen.active_rig
        if rig is None:
            return
        rig.voice = voice.name
        # The patch described a different instrument's controls. Carrying it
        # over would push this rig's old cutoff onto whatever symbol happens to
        # share that name on the new plugin, or silently do nothing.
        rig.voice_params = {}
        if self._active_set is not None:
            self._active_set.replace(rig)
            self._sets.save()
        self._engine.load_voice(voice)
        self._rig_screen.set_rigs(self._rigs)
        self._show_effects_screen()

    def _on_reorder_effects(self, source: int, target: int) -> None:
        if self._engine.move_effect(source, target):
            self._show_effects_screen()

    def _on_bypass_effect(self, instance: int, bypassed: bool) -> None:
        """Toggling is a single mod-host command, so it happens inline rather
        than on a worker: putting it behind a spinner would make an A/B
        comparison feel slower than it is."""
        self._engine.set_effect_bypass(instance, bypassed)
        if self._effects_screen is not None:
            self._effects_screen.chain.effects = self._engine.effects()

    def _show_effect_params_screen(self, instance: int) -> None:
        effect = next(
            (e for e in self._engine.effects() if e.instance == instance), None
        )
        if effect is None:
            return
        name = next(
            (c.name for c in self._catalog if c.uri == effect.uri), effect.uri
        )
        # lv2info is a subprocess; reading it on the UI thread would stall the
        # frame loop for the length of a plugin scan.
        self._params_screen = ParamsScreen(
            name=name,
            ports=self._engine.effect_controls(effect.uri),
            values=dict(effect.params),
            on_change=(
                lambda symbol, value: self._engine.set_effect_param(
                    instance, symbol, str(value)
                )
            ),
            on_back=self._show_effects_screen,
            on_reset=lambda: self._reset_effect_params(instance),
            on_settings=self._show_settings_screen,
        )
        self.screen = self._params_screen

    def _reset_effect_params(self, instance: int) -> None:
        """Back to the plugin's own defaults — the escape hatch from a chain
        that has been dialled into uselessness."""
        for port in self._engine.effect_controls(
            next(e.uri for e in self._engine.effects() if e.instance == instance)
        ):
            self._engine.set_effect_param(instance, port.symbol, str(port.default))
        self._show_effect_params_screen(instance)

    def _show_voice_params_screen(self) -> None:
        """The instrument's own controls, on the same screen its effects get.

        Reached by tapping the first block on the wire. Swapping the instrument
        lives in this screen's header now: one rule — tap a block, edit a block
        — beats the first block behaving differently from the rest because of
        what it is.
        """
        rig = self._rig_screen.active_rig
        voice = self._library.get(rig.voice) if rig is not None else None
        if voice is None:
            return
        self._params_screen = ParamsScreen(
            name=voice.name,
            ports=self._engine.voice_controls(voice),
            values=self._engine.voice_params(),
            on_change=(
                lambda symbol, value: self._engine.set_voice_param(symbol, str(value))
            ),
            on_back=self._show_effects_screen,
            on_reset=self._reset_voice_params,
            on_swap=self._show_instrument_swap,
            on_settings=self._show_settings_screen,
        )
        self.screen = self._params_screen

    def _reset_voice_params(self) -> None:
        """Back to the instrument as the catalog describes it.

        Not the plugin's raw defaults, which is what Reset means for an effect.
        A voice is a catalog entry — "Rhodes EP" is mda EPiano *plus* the values
        that make it that voice — so resetting past them would hand back a
        different instrument than the one named at the top of the screen.
        """
        rig = self._rig_screen.active_rig
        voice = self._library.get(rig.voice) if rig is not None else None
        if voice is None:
            return
        for port in self._engine.voice_controls(voice):
            baseline = voice.params.get(port.symbol, port.default)
            self._engine.set_voice_param(port.symbol, str(baseline))
        self._show_voice_params_screen()

    def _on_remove_effect(self, instance: int) -> None:
        if self._effects_screen is None:
            return
        self._effects_screen.set_loading(True)
        threading.Thread(
            target=self._remove_effect_worker, args=(instance,), daemon=True
        ).start()

    def _remove_effect_worker(self, instance: int) -> None:
        self._engine.remove_effect(instance)
        self._show_effects_screen()

    def _on_add_effect(self, entry: EffectCatalogEntry) -> None:
        if self._catalog_screen is None:
            return
        self._catalog_screen.set_loading(True)
        threading.Thread(
            target=self._add_effect_worker, args=(entry,), daemon=True
        ).start()

    def _add_effect_worker(self, entry: EffectCatalogEntry) -> None:
        instance = self._engine.add_effect(entry.uri, self._insert_at)
        if instance is not None:
            self._show_effects_screen()
        elif self._catalog_screen is not None:
            self._catalog_screen.set_loading(False)
            self._catalog_screen.header.error = True
            self._catalog_screen.header.name = "Failed to add effect"

    def _to_ui_event(self, event: pygame.event.Event) -> UIEvent | None:
        match event.type:
            case pygame.FINGERDOWN | pygame.FINGERUP:
                return UIEvent(
                    event.type,
                    pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)),
                )
            case pygame.FINGERMOTION:
                return UIEvent(
                    event.type,
                    pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)),
                    dy=int(event.dy * SCREEN_H),
                )
            case pygame.MOUSEBUTTONDOWN | pygame.MOUSEBUTTONUP | pygame.MOUSEMOTION:
                return UIEvent(event.type, pos=event.pos)
            case pygame.MOUSEWHEEL:
                return UIEvent(event.type, dy=event.y * 40)
            case _:
                return None

    def run(self) -> None:
        clock = pygame.time.Clock()
        try:
            while True:
                for raw in pygame.event.get():
                    if raw.type == pygame.QUIT:
                        return
                    if raw.type == pygame.KEYDOWN and raw.key == pygame.K_ESCAPE:
                        return
                    if event := self._to_ui_event(raw):
                        self.screen.handle_event(event)

                if not self._splash_done:
                    elapsed = pygame.time.get_ticks() - self._splash_start
                    if elapsed >= SPLASH_DURATION_MS:
                        self.screen = self._home
                        self._splash_done = True

                self.display.fill(BG)
                self.screen.draw(self.display)
                pygame.display.flip()
                clock.tick(30)
        finally:
            self._save_where_we_were()
            pygame.quit()
