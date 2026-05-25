from pygame.surface import Surface

from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent


class Screen:
    components: tuple[Component, ...]

    def draw(self, surface: Surface) -> None:
        for component in self.components:
            component.draw(surface)

    def handle_event(self, event: UIEvent) -> None:
        for component in self.components:
            component.handle_event(event)
