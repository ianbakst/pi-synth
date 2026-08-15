"""
AudioDevices: discover the ALSA playback cards JACK could open.

This is a *different* layer from JackGraph. JackGraph patches JACK ports of
running clients; this enumerates the physical sound cards, so the UI can show
them and let the user pick which one JACK opens. JACK binds one device at
startup, so changing the card means restarting jackd (see scripts/start-jack.sh)
— not a live patch.

Source of truth is `aplay -l` (playback devices only, which is what a synth
wants), parsed into stable card *ids* (`hw:<id>`, never the index — indices shift
across boots). The reader is injectable so tests need no ALSA. Off the Pi (no
`aplay`), discovery is simply empty.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The appliance's built-in DAC — preferred whenever it's present.
PREFERRED_CARD = "sndrpihifiberry"

# `card 0: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: ...`
_CARD_RE = re.compile(r"^card (\d+): (\S+) \[([^\]]*)\], device ")

# A reader returns the raw `aplay -l` stdout.
Reader = Callable[[], str]


def _aplay_reader() -> str:
    try:
        p = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=5)
        return p.stdout
    except FileNotFoundError:
        return ""  # no alsa-utils (dev machine) -> no cards
    except subprocess.TimeoutExpired:
        logger.warning("aplay -l timed out")
        return ""


@dataclass(frozen=True)
class Card:
    """An ALSA playback card. `id` is stable across boots; use it as hw:<id>."""

    id: str
    name: str
    index: int

    @property
    def device(self) -> str:
        return f"hw:{self.id}"


class AudioDevices:
    def __init__(self, reader: Reader | None = None):
        self._read: Reader = reader or _aplay_reader

    def list_cards(self) -> list[Card]:
        """Playback cards, de-duplicated by id (a card lists once per device)."""
        cards: list[Card] = []
        seen: set[str] = set()
        for line in self._read().splitlines():
            m = _CARD_RE.match(line)
            if not m:
                continue
            index, card_id, name = int(m.group(1)), m.group(2), m.group(3)
            if card_id in seen:
                continue
            seen.add(card_id)
            cards.append(Card(id=card_id, name=name, index=index))
        return cards

    def resolve(self, saved: str | None = None) -> str | None:
        """The card id JACK should open, by precedence: a valid saved choice ->
        the HiFiBerry -> the first available card -> None (no hardware).

        Mirrors scripts/start-jack.sh (which is authoritative at boot); used by
        the UI to show/validate the effective device. `None` here maps to jackd's
        dummy backend in the wrapper.
        """
        ids = [c.id for c in self.list_cards()]
        if saved and saved in ids:
            return saved
        if PREFERRED_CARD in ids:
            return PREFERRED_CARD
        return ids[0] if ids else None
