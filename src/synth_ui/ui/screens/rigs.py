"""RigsScreen — the instrument's main screen.

Rigs, not voices, are what you pick: a rig is a whole sound (instrument +
effects + level), which is what you actually reach for when playing. The voice
catalog is now one level down, reached through "New" when building a rig.

Header actions: New (build a rig from a voice), Edit (this rig's effects),
Audio (output device). USB lives on the voice picker, since importing
soundfonts is about the catalog, not about rigs.
"""

import threading
from collections.abc import Callable

import pygame

from synth_ui.clients.rig import Rig
from synth_ui.config import (
    DEFAULT_GAIN,
    FOOTER_H,
    HEADER_H,
    MAX_GAIN,
    SCREEN_H,
    SCREEN_W,
)
from synth_ui.ui.components.header import Header
from synth_ui.ui.components.rig_list import RigList
from synth_ui.ui.components.slider.slider import Slider
from synth_ui.ui.screens.base import Screen


class RigsScreen(Screen):
    def __init__(
        self,
        rigs: list[Rig],
        on_load_rig: Callable[[Rig], bool],
        on_remove_rig: Callable[[Rig], None],
        on_edit_rig: Callable[[Rig], None],
        on_new: Callable,
        on_edit: Callable,
        on_audio: Callable,
        on_gain_change: Callable[[float], None],
        on_save: Callable[[str], None],
        effect_names: dict[str, str] | None = None,
        unavailable: Callable[[Rig], str] | None = None,
        initial_name: str | None = None,
        initial_gain: float = DEFAULT_GAIN,
    ):
        self._on_load_rig = on_load_rig
        self._on_save = on_save
        self._active_rig: Rig | None = None

        font_large = pygame.font.Font(None, 36)
        font_medium = pygame.font.Font(None, 28)
        font_small = pygame.font.Font(None, 22)

        self.header = Header(
            rect=pygame.Rect(0, 0, SCREEN_W, HEADER_H),
            font=font_large,
            action_label="New",
            on_action=on_new,
            action2_label="Edit",
            on_action2=on_edit,
            action3_label="Audio",
            on_action3=on_audio,
        )
        self.rig_list = RigList(
            rect=pygame.Rect(0, HEADER_H, SCREEN_W, SCREEN_H - HEADER_H - FOOTER_H),
            rigs=rigs,
            font_medium=font_medium,
            font_small=font_small,
            on_select=self._on_rig_select,
            on_remove=on_remove_rig,
            on_edit=on_edit_rig,
            effect_names=effect_names,
            unavailable=unavailable,
        )
        self.volume_slider = Slider(
            rect=pygame.Rect(0, SCREEN_H - FOOTER_H, SCREEN_W, FOOTER_H),
            initial_value=initial_gain,
            on_change=on_gain_change,
            min_value=0.0,
            max_value=MAX_GAIN,
            label="Volume",
            font=font_small,
        )
        self.components = (self.header, self.rig_list, self.volume_slider)

        if initial_name is not None:
            threading.Thread(
                target=self._restore, args=(initial_name,), daemon=True
            ).start()

    @property
    def active_rig(self) -> Rig | None:
        return self._active_rig

    def _on_rig_select(self, index: int, rig: Rig) -> None:
        self._active_rig = rig
        self.rig_list.selected_index = index
        self.header.name = rig.name
        self.set_loading(True)
        threading.Thread(target=self._do_load, args=(rig,), daemon=True).start()

    def _do_load(self, rig: Rig) -> None:
        try:
            self.header.error = not self._on_load_rig(rig)
        finally:
            self.set_loading(False)

    def _restore(self, name: str) -> None:
        """Load the rig from last time, or — if it's gone or unusable — the first
        one that works, so the unit always boots into something playable."""
        rigs = self.rig_list.rigs
        rig = next((r for r in rigs if r.name == name and not self._reason(r)), None)
        if rig is None:
            rig = next((r for r in rigs if not self._reason(r)), None)
        if rig is None:
            return
        self._active_rig = rig
        self.rig_list.selected_index = rigs.index(rig)
        self.header.name = rig.name
        self.set_loading(True)
        try:
            self._on_load_rig(rig)
        finally:
            self.set_loading(False)

    def _reason(self, rig: Rig) -> str:
        return self.rig_list._unavailable(rig)

    def refresh(self, rigs: list[Rig]) -> None:
        """Re-render after the library changed (rig added, removed, edited)."""
        self.rig_list.rigs = rigs
        if self._active_rig is not None:
            names = [r.name for r in rigs]
            self.rig_list.selected_index = (
                names.index(self._active_rig.name)
                if self._active_rig.name in names
                else -1
            )
            if self.rig_list.selected_index == -1:
                self._active_rig = None
                self.header.name = None

    def save(self) -> None:
        if self._active_rig is not None:
            self._on_save(self._active_rig.name)
