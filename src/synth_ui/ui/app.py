import os
import threading

import pygame

from synth_ui.clients import EngineManager
from synth_ui.clients.backlight import Backlight
from synth_ui.clients.effects_catalog import (
    EffectCatalogEntry,
    annotate_effects,
    read_effects_manifest,
)
from synth_ui.clients.midi_control import MidiControlListener
from synth_ui.clients.rig import Rig, RigEffect, RigLibrary
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
    SOUNDFONT_DIR,
    STATE_FILE,
    TRIMS_FILE,
    VOICES_MANIFEST,
)
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.audio import AudioScreen
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.screens.effect_params import EffectParamsScreen
from synth_ui.ui.screens.effects import EffectsCatalogScreen, EffectsScreen
from synth_ui.ui.screens.rigs import RigsScreen
from synth_ui.ui.screens.splash import SplashScreen
from synth_ui.ui.screens.text_entry import TextEntryScreen
from synth_ui.ui.screens.usb import USBScreen
from synth_ui.ui.screens.voice_picker import VoicePickerScreen
from synth_ui.ui.utils import load_voices, lv2_world

SPLASH_DURATION_MS = 5000


def _load_state() -> str | None:
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def _save_state(name: str) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            f.write(name)
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
        # No other SDL env setup needed: pygame 2 is SDL2, which uses KMSDRM for
        # video and auto-scans /dev/input/event* for touch. Touch works as long as
        # this process's user is in the 'input' group (see setup.sh). The old SDL
        # 1.2 vars (SDL_FBDEV/SDL_MOUSEDEV/SDL_MOUSEDRV) are ignored by SDL2.
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
        self._audio_screen: AudioScreen | None = None
        self._effects_screen: EffectsScreen | None = None
        self._catalog_screen: EffectsCatalogScreen | None = None
        self._params_screen: EffectParamsScreen | None = None
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

        # Rigs are the unit of selection; the voice catalog is what you build
        # them from. A freshly flashed card has no rigs, so bootstrap one from
        # DEFAULT_VOICE — otherwise the instrument would boot to an empty list
        # and make no sound.
        self._rigs = RigLibrary.load(RIGS_FILE)
        self._rigs.bootstrap(DEFAULT_VOICE)
        self._library: dict[str, Voice] = {}
        self._reload_library()

        self._home = RigsScreen(
            rigs=self._rigs.rigs,
            on_load_rig=self._load_rig,
            on_remove_rig=self._on_remove_rig,
            on_edit_rig=self._show_rename_screen,
            on_new=lambda: self._show_voice_picker(replaces_instrument=False),
            on_edit=self._show_effects_screen,
            on_audio=self._show_audio_screen,
            on_gain_change=self._on_gain_change,
            on_save=_save_state,
            on_reorder=self._on_reorder_rigs,
            effect_names={e.uri: e.name for e in self._catalog},
            unavailable=self._rig_unavailable,
            # Falls back to the first usable rig if this one is gone.
            initial_name=_load_state(),
            initial_gain=self._gain,
        )
        # Hands-free rig switching. Runs whether or not a pedal is attached —
        # aseqdump subscribes to whatever appears, so plugging one in later
        # works with no restart. Failure is non-fatal: the touchscreen is the
        # primary control and must keep working regardless.
        self._midi_control = MidiControlListener(
            on_next=self._home.select_next,
            on_previous=self._home.select_previous,
            next_cc=MIDI_NEXT_RIG_CC,
            prev_cc=MIDI_PREV_RIG_CC,
            on_program=self._select_rig_by_index if MIDI_PROGRAM_SELECTS_RIG else None,
        )
        self._midi_control.start()

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
                self._show_effects_screen if replaces_instrument else self._show_home
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
        rig = self._rigs.create_from_voice(voice.name)
        self._home.refresh(self._rigs.rigs)
        ok = self._engine.load_rig(rig, voice)
        self._home._active_rig = rig
        self._home.grid.tiles = self._home._tiles()
        self._home.header.name = rig.name
        self._home.header.error = not ok

        # Name it while you have the context for what it is. The rig is already
        # created and loaded, so the instrument is playable during naming and
        # cancelling just keeps the auto-name.
        self._show_rename_screen(rig, title="Name rig")

    def _show_rename_screen(self, rig: Rig, title: str = "Rename rig") -> None:
        def done(name: str) -> None:
            self._rigs.rename(rig, name)
            self._home.header.name = rig.name
            self._home.refresh(self._rigs.rigs)
            self._show_home()

        self.screen = TextEntryScreen(
            title=title,
            initial=rig.name,
            on_done=done,
            on_cancel=self._show_home,
        )

    def _on_remove_rig(self, rig: Rig) -> None:
        self._rigs.remove(rig.name)
        self._home.refresh(self._rigs.rigs)

    def _select_rig_by_index(self, index: int) -> None:
        """Program Change selects a rig by position. Out-of-range is ignored
        rather than clamped: a Program Change past the end of the set list is
        someone else's message, not a request for the last rig."""
        if 0 <= index < len(self._rigs.rigs):
            self._home._select_rig(self._rigs.rigs[index])

    def _on_reorder_rigs(self, source: int, target: int) -> None:
        if self._rigs.move(source, target):
            self._home.refresh(self._rigs.rigs)

    def _sync_active_rig_effects(self) -> None:
        """Persist the current effects chain into the active rig. Called when
        leaving the effects screen — editing effects *is* editing the rig now,
        so there's no separate save step to forget."""
        rig = self._home.active_rig
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
        self._rigs.replace(rig)
        self._home.refresh(self._rigs.rigs)

    def _on_gain_change(self, gain: float) -> None:
        self._gain = gain
        self._engine.set_gain(gain)

    def _show_home(self) -> None:
        self.screen = self._home

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

    def _show_audio_screen(self) -> None:
        self._audio_screen = AudioScreen(
            cards=self._engine.list_audio_cards(),
            current_id=self._engine.current_audio_device(),
            on_select=self._on_audio_selected,
            on_back=self._show_home,
            # Both None on a board with no backlight, which hides the slider
            # rather than showing one that does nothing.
            on_brightness=(
                self._on_brightness if self._backlight.available else None
            ),
            initial_brightness=(
                self._brightness if self._backlight.available else None
            ),
        )
        self.screen = self._audio_screen

    def _on_brightness(self, fraction: float) -> None:
        """Applied live while dragging; saved on each change.

        Saving every event is cheap (one short file) and worth it: the
        instrument is powered down by pulling the plug, so there is no shutdown
        hook to write it in.
        """
        self._brightness = fraction
        self._backlight.set_fraction(fraction)
        _save_brightness(fraction)

    def _on_audio_selected(self, card_id: str) -> None:
        # Restarting jack + rebuilding the voice takes seconds — do it off the UI
        # thread and show a switching state meanwhile.
        if self._audio_screen is None:
            return
        self._audio_screen.set_loading(True)
        self._audio_screen.header.name = "Switching audio..."
        threading.Thread(
            target=self._apply_audio, args=(card_id,), daemon=True
        ).start()

    def _apply_audio(self, card_id: str) -> None:
        ok = self._engine.set_audio_device(card_id)
        if ok:
            self._show_home()
        elif self._audio_screen is not None:
            self._audio_screen.set_loading(False)
            self._audio_screen.header.error = True
            self._audio_screen.header.name = "Audio switch failed"

    def _show_effects_screen(self) -> None:
        rig = self._home.active_rig
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
            on_change_instrument=self._show_instrument_swap,
            # None hides the control on a board that can't do it, rather than
            # offering a button that silently fails.
            on_fixed_velocity=(
                self._engine.set_fixed_velocity
                if self._engine.fixed_velocity_available()
                else None
            ),
            initial_trim=rig.trim_db if rig is not None else 0.0,
            initial_fixed_velocity=rig.fixed_velocity if rig is not None else False,
            source_name=rig.voice if rig is not None else "",
            rig_name=rig.name if rig is not None else "",
        )
        self.screen = self._effects_screen

    def _leave_effects_screen(self) -> None:
        self._sync_active_rig_effects()
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
        rig = self._home.active_rig
        if rig is None:
            return
        rig.voice = voice.name
        self._rigs.replace(rig)
        self._engine.load_voice(voice)
        self._home.refresh(self._rigs.rigs)
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
        self._params_screen = EffectParamsScreen(
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
            self._home.save()
            pygame.quit()
