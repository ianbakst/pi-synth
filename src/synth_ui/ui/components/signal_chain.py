"""The rig's signal chain, drawn as blocks wired together.

A chain is a path, not a list. As rows it read top-to-bottom with nothing saying
what fed what, and each row spent most of its width on nothing. As blocks on a
wire it reads the way you'd sketch it on paper.

**The instrument is the first block.** Not a caption on the header — a block on
the wire, because that is what it is: the stage everything downstream is fed by.
It differs from the effects in three ways, all structural rather than cosmetic:

  - tapping it swaps the instrument, not its parameters;
  - it cannot be removed, and nothing can be inserted before it;
  - it cannot be dragged, and no effect can be dropped in front of it.

A chain without an instrument makes no sound, so "starts with an instrument" is
an invariant of the data, not a rule the user is trusted to follow. The geometry
below enforces it by construction: insertion points start *after* slot 0 and
drop targets are clamped to slot 1 and beyond.

Laid out boustrophedon — left to right, then right to left, alternating. That
buys one nice property for free: the last block of a row and the first of the
next are always in the **same column**, so every connector is a plain horizontal
or vertical segment and there are no elbows to draw or hit-test.

Interactions:
  - tap the instrument   -> choose a different instrument
  - tap an effect        -> edit its parameters
  - tap its corner dot   -> bypass (stays in the chain, passes audio through)
  - tap a `+`            -> add an effect at that point in the chain
  - hold an effect, drag -> move it. The hold is the guard: without it, a scroll
                            or a mis-aimed tap would rearrange the chain.
"""

from collections.abc import Callable

import pygame

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.config import (
    BG,
    BTN_ACTIVE,
    BTN_NORMAL,
    DIVIDER,
    SLIDER_BG,
    STATUS_OK,
    TEXT_ACTIVE,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from synth_ui.ui.components.base import Component
from synth_ui.ui.event import UIEvent

COLUMNS = 3
BLOCK_W = 190
BLOCK_H = 84
# Room for a connector and the `+` sitting on it.
GAP_X = 47
GAP_Y = 56
MARGIN = 20
PLUS = 28
DOT_R = 9
WIRE = 3
# Slot 0 is always the instrument; effect i occupies slot i + 1.
INSTRUMENT_SLOT = 0


class SignalChain(Component):
    LONG_PRESS_MS = 400

    def __init__(
        self,
        rect: pygame.Rect,
        effects: list[Effect],
        catalog: list[EffectCatalogEntry],
        font_medium: pygame.font.Font,
        font_small: pygame.font.Font,
        on_edit: Callable[[int], None],
        on_bypass: Callable[[int, bool], None],
        on_add: Callable[[int], None],
        on_reorder: Callable[[int, int], None],
        on_change_instrument: Callable[[], None] | None = None,
        source_name: str = "",
    ):
        super().__init__(rect)
        self.effects = effects
        self._names = {e.uri: e.name for e in catalog}
        self._categories = {e.uri: e.category for e in catalog}
        self.font_medium = font_medium
        self.font_small = font_small
        self.on_edit = on_edit
        self.on_bypass = on_bypass
        self.on_add = on_add
        self.on_reorder = on_reorder
        self.on_change_instrument = on_change_instrument
        self.source_name = source_name

        self.scroll_offset = 0
        self._press_slot: int | None = None
        self._press_start_ms = 0
        self._drag_slot: int | None = None
        self._drag_pos = (0, 0)
        self._tracking = False

    # ------------------------------------------------------------------
    # Geometry. Everything is in slots; effect i is slot i + 1.
    # ------------------------------------------------------------------

    @property
    def _slots(self) -> int:
        return 1 + len(self.effects)

    def _origin_x(self) -> int:
        span = COLUMNS * BLOCK_W + (COLUMNS - 1) * GAP_X
        return self.rect.x + max(MARGIN, (self.rect.width - span) // 2)

    def slot_rect(self, slot: int) -> pygame.Rect:
        """Where a slot sits. Odd rows run right-to-left, which is what makes
        the row-to-row connector a straight vertical line."""
        row, position = divmod(slot, COLUMNS)
        column = position if row % 2 == 0 else COLUMNS - 1 - position
        return pygame.Rect(
            self._origin_x() + column * (BLOCK_W + GAP_X),
            self.rect.y + GAP_Y + row * (BLOCK_H + GAP_Y) - self.scroll_offset,
            BLOCK_W,
            BLOCK_H,
        )

    def instrument_rect(self) -> pygame.Rect:
        return self.slot_rect(INSTRUMENT_SLOT)

    def block_rect(self, effect_index: int) -> pygame.Rect:
        return self.slot_rect(effect_index + 1)

    def plus_rect(self, index: int) -> pygame.Rect:
        """The `+` that inserts an effect at position `index`, centred on the
        wire between slot `index` and the next.

        `index` 0 is immediately *after* the instrument — there is deliberately
        no `+` before slot 0, because nothing may precede the instrument.
        """
        before = self.slot_rect(index)
        if index + 1 >= self._slots:                    # on the exit wire
            centre = (before.centerx, before.bottom + GAP_Y // 2)
        else:
            after = self.slot_rect(index + 1)
            if before.y == after.y:                     # same row: horizontal
                centre = ((before.right + after.x) // 2, before.centery)
            else:                                       # row change: vertical
                centre = (before.centerx, (before.bottom + after.y) // 2)
        return pygame.Rect(centre[0] - PLUS // 2, centre[1] - PLUS // 2, PLUS, PLUS)

    def _dot_rect(self, block: pygame.Rect) -> pygame.Rect:
        """The bypass dot, top-right of an effect block."""
        return pygame.Rect(block.right - 34, block.y + 8, 26, 26)

    @property
    def _rows(self) -> int:
        return max(1, (self._slots + COLUMNS - 1) // COLUMNS)

    @property
    def _content_h(self) -> int:
        return self._rows * (BLOCK_H + GAP_Y) + GAP_Y

    @property
    def _max_scroll(self) -> int:
        return max(0, self._content_h - self.rect.height)

    def _slot_at(self, pos: tuple[int, int]) -> int | None:
        return next(
            (s for s in range(self._slots) if self.slot_rect(s).collidepoint(pos)),
            None,
        )

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, BG, self.rect)
        self.scroll_offset = max(0, min(self.scroll_offset, self._max_scroll))

        previous_clip = surface.get_clip()
        surface.set_clip(self.rect)

        self._draw_wires(surface)
        for slot in range(self._slots):
            if slot == self._drag_slot:
                continue
            if slot == INSTRUMENT_SLOT:
                self._draw_instrument(surface, self.slot_rect(slot))
            else:
                self._draw_effect(
                    surface, self.slot_rect(slot), self.effects[slot - 1]
                )
        for index in range(len(self.effects) + 1):
            self._draw_plus(surface, self.plus_rect(index))

        if self._drag_slot is not None:
            target = self.slot_rect(self._drop_slot())
            pygame.draw.rect(surface, TEXT_SECONDARY, target, width=2, border_radius=8)
            floating = self.slot_rect(self._drag_slot)
            floating.center = self._drag_pos
            self._draw_effect(surface, floating, self.effects[self._drag_slot - 1])

        surface.set_clip(previous_clip)

    def _draw_wires(self, surface: pygame.Surface) -> None:
        """The signal path: down into the instrument, through each effect, out
        bottom right."""
        first = self.slot_rect(0)
        pygame.draw.line(
            surface, DIVIDER,
            (first.centerx, self.rect.y), (first.centerx, first.y), WIRE,
        )

        for slot in range(1, self._slots):
            before, after = self.slot_rect(slot - 1), self.slot_rect(slot)
            if before.y == after.y:
                start, end = (before.right, before.centery), (after.x, after.centery)
            else:
                start, end = (before.centerx, before.bottom), (after.centerx, after.y)
            pygame.draw.line(surface, DIVIDER, start, end, WIRE)

        last = self.slot_rect(self._slots - 1)
        exit_y = last.bottom + GAP_Y // 2
        pygame.draw.line(
            surface, DIVIDER, (last.centerx, last.bottom), (last.centerx, exit_y), WIRE
        )
        pygame.draw.line(
            surface, DIVIDER,
            (last.centerx, exit_y), (self.rect.right - MARGIN, exit_y), WIRE,
        )
        self._draw_terminal(surface, (self.rect.right - MARGIN, exit_y), "OUT")

    def _draw_terminal(
        self, surface: pygame.Surface, pos: tuple[int, int], text: str
    ) -> None:
        pygame.draw.circle(surface, STATUS_OK, pos, 6)
        label = self.font_small.render(text, True, TEXT_SECONDARY)
        surface.blit(label, (pos[0] - label.get_width(), pos[1] + 10))

    def _fit(self, text: str, width: int, color: tuple) -> pygame.Surface:
        surface = self.font_medium.render(text, True, color)
        while surface.get_width() > width and len(text) > 3:
            text = text[:-4] + "..."
            surface = self.font_medium.render(text, True, color)
        return surface

    def _draw_instrument(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Visually distinct from the effects, because it behaves differently:
        it can be changed but never removed or reordered."""
        pygame.draw.rect(surface, BTN_ACTIVE, rect, border_radius=8)
        name = self.source_name or "No instrument"
        surface.blit(
            self._fit(name, rect.width - 24, TEXT_ACTIVE), (rect.x + 12, rect.y + 12)
        )
        surface.blit(
            # Parallel with the effects, whose subtitle is their category. "tap
            # to change" was the honest label but overran the block, and the
            # colour and position already say this block is not like the others.
            self.font_small.render("instrument", True, TEXT_ACTIVE),
            (rect.x + 12, rect.y + 46),
        )

    def _draw_effect(
        self, surface: pygame.Surface, rect: pygame.Rect, effect: Effect
    ) -> None:
        pygame.draw.rect(surface, BTN_NORMAL, rect, border_radius=8)
        color = TEXT_DISABLED if effect.bypassed else TEXT_PRIMARY
        name = self._names.get(effect.uri, effect.uri)
        surface.blit(
            self._fit(name, rect.width - 52, color), (rect.x + 12, rect.y + 12)
        )
        surface.blit(
            self.font_small.render(
                "bypassed" if effect.bypassed else self._categories.get(effect.uri, ""),
                True,
                TEXT_SECONDARY,
            ),
            (rect.x + 12, rect.y + 46),
        )

        # Bypass lives on the block because it is a performance control — the
        # one thing you reach for mid-song — while remove lives one level in, on
        # the parameters screen, where you have already committed to editing it.
        dot = self._dot_rect(rect)
        pygame.draw.circle(
            surface, SLIDER_BG if effect.bypassed else BTN_ACTIVE, dot.center, DOT_R
        )
        if not effect.bypassed:
            pygame.draw.circle(surface, TEXT_ACTIVE, dot.center, DOT_R - 4)

    def _draw_plus(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, SLIDER_BG, rect, border_radius=6)
        cx, cy = rect.center
        pygame.draw.line(surface, TEXT_SECONDARY, (cx - 6, cy), (cx + 6, cy), 2)
        pygame.draw.line(surface, TEXT_SECONDARY, (cx, cy - 6), (cx, cy + 6), 2)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _drop_slot(self) -> int:
        """Nearest slot to the finger, never slot 0: an effect dropped onto the
        instrument would have to displace it, and nothing precedes it."""
        nearest, best = self._drag_slot or 1, None
        for slot in range(1, self._slots):
            rect = self.slot_rect(slot)
            dx, dy = rect.centerx - self._drag_pos[0], rect.centery - self._drag_pos[1]
            distance = dx * dx + dy * dy
            if best is None or distance < best:
                nearest, best = slot, distance
        return max(1, nearest)

    def _held(self) -> bool:
        return pygame.time.get_ticks() - self._press_start_ms >= self.LONG_PRESS_MS

    def handle_event(self, event: UIEvent) -> bool:
        if self.loading:
            return False

        if event.type == pygame.MOUSEWHEEL:
            self.scroll_offset -= event.dy * 40
            return True

        if event.type in (pygame.FINGERDOWN, pygame.MOUSEBUTTONDOWN):
            if not self.rect.collidepoint(event.pos):
                return False
            self._tracking = True
            self._press_start_ms = pygame.time.get_ticks()
            self._drag_pos = event.pos
            self._press_slot = self._slot_at(event.pos)
            return True

        if event.type in (pygame.FINGERMOTION, pygame.MOUSEMOTION) and self._tracking:
            self._drag_pos = event.pos
            if self._drag_slot is None:
                # The instrument is never picked up: it has no position to move
                # to, since it must stay first.
                draggable = (
                    self._press_slot is not None
                    and self._press_slot != INSTRUMENT_SLOT
                )
                if draggable and self._held():
                    self._drag_slot = self._press_slot
                elif abs(event.dy) > 2:
                    self.scroll_offset -= int(event.dy)
            return True

        if event.type in (pygame.FINGERUP, pygame.MOUSEBUTTONUP) and self._tracking:
            self._tracking = False
            slot, self._press_slot = self._press_slot, None

            if self._drag_slot is not None:
                source, self._drag_slot = self._drag_slot, None
                target = self._drop_slot()
                if target != source:
                    self.on_reorder(source - 1, target - 1)
                return True

            if slot == INSTRUMENT_SLOT:
                if self.on_change_instrument:
                    self.on_change_instrument()
                return True

            if slot is not None:
                effect = self.effects[slot - 1]
                if self._dot_rect(self.slot_rect(slot)).collidepoint(event.pos):
                    self.on_bypass(effect.instance, not effect.bypassed)
                else:
                    self.on_edit(effect.instance)
                return True

            for index in range(len(self.effects) + 1):
                if self.plus_rect(index).collidepoint(event.pos):
                    self.on_add(index)
                    return True
            return True

        return False
