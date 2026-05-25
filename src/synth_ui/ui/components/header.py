import pygame

from synth_ui.config import DIVIDER, PANEL_BG, TEXT_ACTIVE, TEXT_SECONDARY

from .base import Component


class Header(Component):
    def __init__(self, rect: pygame.Rect, font: pygame.font.Font):
        super().__init__(rect)
        self.font = font
        self.name: str | None = None
        self.loading: bool = False

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, PANEL_BG, self.rect)
        pygame.draw.line(
            surface,
            DIVIDER,
            (self.rect.left, self.rect.bottom - 1),
            (self.rect.right, self.rect.bottom - 1),
        )

        name = self.name or "No voice selected"
        color = TEXT_SECONDARY if (not self.name or self.loading) else TEXT_ACTIVE

        text = self.font.render(name, True, color)
        max_w = self.rect.width - 50
        if text.get_width() > max_w:
            while text.get_width() > max_w and len(name) > 3:
                name = name[:-4] + "..."
                text = self.font.render(name, True, color)
        surface.blit(
            text, (36, self.rect.y + (self.rect.height - text.get_height()) // 2)
        )
