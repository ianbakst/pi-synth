import pygame

from synth_ui.clients import EngineManager, Preset
from synth_ui.clients.voice import Voice
from synth_ui.config import (
    BG,
    DEFAULT_GAIN,
    ENGINE_MANAGER_SCRIPT,
    FLUIDSYNTH_HOST,
    FLUIDSYNTH_PORT,
    IS_PI,
    MAX_GAIN,
    MOD_HOST_PORT,
    SCREEN_H,
    SCREEN_W,
    STATE_FILE,
)
from synth_ui.ui.event import UIEvent
from synth_ui.ui.screens.base import Screen
from synth_ui.ui.screens.home import HomeScreen
from synth_ui.ui.screens.preset import PresetScreen
from synth_ui.ui.screens.splash import SplashScreen
from synth_ui.ui.screens.usb import USBScreen

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
        # No SDL env setup needed: pygame 2 is SDL2, which uses KMSDRM for video
        # and auto-scans /dev/input/event* for touch. Touch works as long as this
        # process's user is in the 'input' group (see setup.sh). The old SDL 1.2
        # vars (SDL_FBDEV/SDL_MOUSEDEV/SDL_MOUSEDRV) are ignored by SDL2.
        pygame.init()

        if IS_PI:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        else:
            self.display = pygame.display.set_mode((SCREEN_W, SCREEN_H))

        pygame.display.set_caption("MIDI Instrument")

        self._engine = EngineManager(
            engine_manager_script=ENGINE_MANAGER_SCRIPT,
            fluidsynth_host=FLUIDSYNTH_HOST,
            fluidsynth_port=FLUIDSYNTH_PORT,
            mod_host_port=MOD_HOST_PORT,
        )
        self._gain: float = DEFAULT_GAIN
        self._preset_screen: PresetScreen | None = None

        self._home = HomeScreen(
            on_load_voice=self._engine.load_voice,
            on_list_presets=self._engine.list_presets,
            on_navigate=self._on_navigate,
            on_gain_change=self._on_gain_change,
            on_save=_save_state,
            on_usb=self._show_usb_screen,
            initial_name=_load_state(),
            initial_gain=self._gain,
        )
        self.screen: Screen = SplashScreen()
        self._splash_start = pygame.time.get_ticks()
        self._splash_done = False

    def _on_gain_change(self, gain: float) -> None:
        self._gain = gain
        self._engine.set_gain(gain)
        if self._preset_screen is not None:
            self._preset_screen.volume_slider.value = gain

    def _on_navigate(self, voice: Voice, presets: list[Preset]) -> None:
        """Called after a voice loads. Navigate to presets only for FluidSynth."""
        if presets:
            self._show_preset_screen(voice, presets)
        # For other engines, stay on the home screen (no preset drill-down)

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
        self._home.refresh()

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
