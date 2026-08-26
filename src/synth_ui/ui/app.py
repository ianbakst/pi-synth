import os
import threading

import pygame

from synth_ui.clients import EngineManager, Preset
from synth_ui.clients.effects_catalog import EffectCatalogEntry, read_effects_manifest
from synth_ui.clients.rig import Rig, RigEffect, RigLibrary
from synth_ui.clients.voice import Voice
from synth_ui.config import (
    BG,
    DEFAULT_GAIN,
    DEFAULT_VOICE,
    EFFECTS_MANIFEST,
    FLUIDSYNTH_HOST,
    FLUIDSYNTH_PORT,
    IS_PI,
    MAX_GAIN,
    MOD_HOST_PORT,
    RIGS_FILE,
    SCREEN_H,
    SCREEN_W,
    SOUNDFONT_DIR,
    STATE_FILE,
    VOICES_MANIFEST,
)
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.audio import AudioScreen
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.screens.effects import EffectsCatalogScreen, EffectsScreen
from synth_ui.ui.screens.preset import PresetScreen
from synth_ui.ui.screens.rigs import RigsScreen
from synth_ui.ui.screens.splash import SplashScreen
from synth_ui.ui.screens.usb import USBScreen
from synth_ui.ui.screens.voice_picker import VoicePickerScreen
from synth_ui.ui.utils import load_voices

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
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        self._engine = EngineManager(
            fluidsynth_host=FLUIDSYNTH_HOST,
            fluidsynth_port=FLUIDSYNTH_PORT,
            mod_host_port=MOD_HOST_PORT,
        )
        self._gain: float = DEFAULT_GAIN
        self._preset_screen: PresetScreen | None = None
        self._audio_screen: AudioScreen | None = None
        self._effects_screen: EffectsScreen | None = None
        self._catalog_screen: EffectsCatalogScreen | None = None
        self._catalog: list[EffectCatalogEntry] = read_effects_manifest(
            EFFECTS_MANIFEST
        )
        self._picker: VoicePickerScreen | None = None

        # Rigs are the unit of selection; the voice catalog is what you build
        # them from. A freshly flashed card has no rigs, so bootstrap one from
        # DEFAULT_VOICE — otherwise the instrument would boot to an empty list
        # and make no sound.
        self._rigs = RigLibrary.load(RIGS_FILE)
        self._rigs.bootstrap(DEFAULT_VOICE)

        self._home = RigsScreen(
            rigs=self._rigs.rigs,
            on_load_rig=self._load_rig,
            on_remove_rig=self._on_remove_rig,
            on_new=self._show_voice_picker,
            on_edit=self._show_effects_screen,
            on_audio=self._show_audio_screen,
            on_gain_change=self._on_gain_change,
            on_save=_save_state,
            effect_names={e.uri: e.name for e in self._catalog},
            unavailable=self._rig_unavailable,
            # Falls back to the first usable rig if this one is gone.
            initial_name=_load_state(),
            initial_gain=self._gain,
        )
        self.screen: Screen = SplashScreen()
        self._splash_start = pygame.time.get_ticks()
        self._splash_done = False

    # ------------------------------------------------------------------
    # Rigs
    # ------------------------------------------------------------------

    def _voice_for(self, name: str) -> Voice | None:
        return next(
            (v for v in load_voices(VOICES_MANIFEST, SOUNDFONT_DIR) if v.name == name),
            None,
        )

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

    def _show_voice_picker(self) -> None:
        self._picker = VoicePickerScreen(
            on_pick=self._on_voice_picked,
            on_back=self._show_home,
            on_usb=self._show_usb_screen,
        )
        self.screen = self._picker

    def _on_voice_picked(self, voice: Voice) -> None:
        """A picked voice becomes a new rig — bare instrument, no effects yet —
        which is then loaded and made active, ready for effects to be stacked."""
        rig = self._rigs.create_from_voice(voice.name)
        self._home.refresh(self._rigs.rigs)
        ok = self._engine.load_rig(rig, voice)
        self._home._active_rig = rig
        self._home.rig_list.selected_index = self._rigs.rigs.index(rig)
        self._home.header.name = rig.name
        self._home.header.error = not ok

        presets = self._engine.list_presets()
        if presets:
            self._show_preset_screen(voice, presets)
        else:
            self._show_home()

    def _on_remove_rig(self, rig: Rig) -> None:
        self._rigs.remove(rig.name)
        self._home.refresh(self._rigs.rigs)

    def _sync_active_rig_effects(self) -> None:
        """Persist the current effects chain into the active rig. Called when
        leaving the effects screen — editing effects *is* editing the rig now,
        so there's no separate save step to forget."""
        rig = self._home.active_rig
        if rig is None:
            return
        rig.effects = [
            RigEffect(uri=e.uri) for e in self._engine.effects()
        ]
        self._rigs.replace(rig)
        self._home.refresh(self._rigs.rigs)

    def _on_gain_change(self, gain: float) -> None:
        self._gain = gain
        self._engine.set_gain(gain)
        if self._preset_screen is not None:
            self._preset_screen.volume_slider.value = gain

    def _show_preset_screen(self, voice: Voice, presets: list[Preset]) -> None:
        self._preset_screen = PresetScreen(
            font_name=voice.name,
            presets=presets,
            on_select=self._on_preset_selected,
            on_back=self._show_home,
            on_gain_change=self._on_gain_change,
            initial_gain=self._gain,
            max_gain=MAX_GAIN,
        )
        self.screen = self._preset_screen

    def _on_preset_selected(self, preset: Preset) -> None:
        self._engine.select_preset(0, 1, preset.bank, preset.prog)
        self._home.header.name = preset.name
        if self._preset_screen is not None:
            self._preset_screen.set_selected(preset)

    def _show_home(self) -> None:
        self.screen = self._home

    def _show_usb_screen(self) -> None:
        self.screen = USBScreen(
            on_back=self._show_home,
            on_copy_complete=self._on_usb_copy_complete,
        )

    def _on_usb_copy_complete(self) -> None:
        # USB import adds soundfonts to the catalog, so it's the picker that
        # needs re-reading, not the rig list.
        if self._picker is not None:
            self._picker.refresh()

    def _show_audio_screen(self) -> None:
        self._audio_screen = AudioScreen(
            cards=self._engine.list_audio_cards(),
            current_id=self._engine.current_audio_device(),
            on_select=self._on_audio_selected,
            on_back=self._show_home,
        )
        self.screen = self._audio_screen

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
        self._effects_screen = EffectsScreen(
            effects=self._engine.effects(),
            catalog=self._catalog,
            on_remove=self._on_remove_effect,
            on_add=self._show_effects_catalog_screen,
            on_back=self._leave_effects_screen,
        )
        self.screen = self._effects_screen

    def _leave_effects_screen(self) -> None:
        self._sync_active_rig_effects()
        self._show_home()

    def _show_effects_catalog_screen(self) -> None:
        self._catalog_screen = EffectsCatalogScreen(
            catalog=self._catalog,
            on_select=self._on_add_effect,
            on_back=self._show_effects_screen,
        )
        self.screen = self._catalog_screen

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
        instance = self._engine.add_effect(entry.uri)
        if instance is not None:
            self._show_effects_screen()
        elif self._catalog_screen is not None:
            self._catalog_screen.set_loading(False)
            self._catalog_screen.header.error = True
            self._catalog_screen.header.name = "Failed to add effect"

    def _to_ui_event(self, event: pygame.event.Event) -> UIEvent | None:
        match event.type:
            case pygame.FINGERDOWN | pygame.FINGERUP:
                return UIEvent(event.type, pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)))
            case pygame.FINGERMOTION:
                return UIEvent(event.type, pos=(int(event.x * SCREEN_W), int(event.y * SCREEN_H)), dy=int(event.dy * SCREEN_H))
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
                    if pygame.time.get_ticks() - self._splash_start >= SPLASH_DURATION_MS:
                        self.screen = self._home
                        self._splash_done = True

                self.display.fill(BG)
                self.screen.draw(self.display)
                pygame.display.flip()
                clock.tick(30)
        finally:
            self._home.save()
            pygame.quit()
